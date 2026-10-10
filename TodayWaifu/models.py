"""TodayWaifu 每日记录数据库模型。

取代旧的 data/TodayWaifu/daily_wife_data.json 单文件存储：整份 JSON 的读-改-写随群数与
天数增长产生写放大，且并发写入会相互覆盖；按“用户 × 日期 × 群 × 桶”拆成独立行后，单次
变更只落一行，record 整体序列化进 payload 以兼容旧结构，name/state/origin 冗余供查询过滤。

本模块不依赖 TodayWaifu 内其它模块，测试用 importlib 直接加载，故禁止运行时相对导入。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from sqlmodel import Field, delete, select
from sqlalchemy import Table, UniqueConstraint, func, tuple_
from sqlalchemy.sql.dml import Insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from gsuid_core.logger import logger
from gsuid_core.server import on_core_start_before
from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.startup import exec_list
from gsuid_core.utils.database.base_models import (
    BaseModel,
    engine,
    with_session,
    with_read_session,
)

if TYPE_CHECKING:
    # 仅供类型检查器解析：本模块被测试以 importlib 独立加载，运行时相对导入会直接失败。
    from .payloads import DailyContext, RoleRecordValue

LOG_PREFIX = '[鸣潮今日老婆]'

# 非 dict 值（如 rob_attempts 的 True 标记）没有可拆分的业务列，以此 record_type 标识。
MARKER_RECORD_TYPE = 'marker'


class _ExcludedColumns(Protocol):
    """SQLAlchemy 未公开 excluded 的类型，故以协议声明其按列名的下标访问。"""

    def __getitem__(self, key: str) -> ColumnElement[object]: ...


def _conflict_update_columns(statement: Insert) -> dict[str, ColumnElement[object]]:
    """构造冲突时的覆盖列集合；主键与业务键不参与覆盖，避免行被改写为另一业务键。"""
    excluded: _ExcludedColumns = statement.excluded
    return {column: excluded[column] for column in _CONFLICT_UPDATE_COLUMNS}


# upsert 冲突时需要覆盖的业务列：须与 _row_from_value 产出的列一致，漏列会使旧值残留。
_CONFLICT_UPDATE_COLUMNS = (
    'name', 'display_name', 'image', 'record_type', 'state',
    'origin', 'updated_at', 'payload',
)


def _record_state(raw: object) -> str:
    """派生 state 列；取值口径须与 shared._wife_state 一致，否则控制台过滤会错判。"""
    if not isinstance(raw, dict):
        return 'owned'
    if raw.get('divorced'):
        return 'divorced'
    if raw.get('stolen_by'):
        return 'lost_stolen'
    if raw.get('gifted_to'):
        return 'lost_gifted'
    return 'owned'


def _record_origin(raw: object) -> str:
    """派生 origin 列；取值口径须与 shared._wife_origin 一致，否则统计聚合会与业务口径偏离。"""
    if not isinstance(raw, dict):
        return 'self'
    if raw.get('stolen_from'):
        return 'robbed'
    if raw.get('gifted_from'):
        return 'gifted'
    if raw.get('safe'):
        return 'safe'
    return 'self'


def split_context_key(context_key: str) -> tuple[str, str]:
    """拆分 shared._context_key 拼接的上下文键；群号缺失时归为 direct，避免落库出现空群号。"""
    bot_id, _, group_id = str(context_key).partition(':')
    return bot_id, group_id or 'direct'


class DailyWifeRecord(BaseModel, table=True):
    """今日老婆每日记录表；唯一约束保证同一业务键在库内至多一行，upsert 才有可依的冲突目标。"""

    __table_args__ = (
        UniqueConstraint('day', 'bot_id', 'group_id', 'bucket', 'user_id'),
        {'extend_existing': True},
    )

    day: str = Field(title='日期', index=True)
    group_id: str = Field(default='direct', title='群号')
    bucket: str = Field(default='wives', title='记录桶')
    name: str = Field(default='', title='名称')
    display_name: str = Field(default='', title='显示名')
    image: str = Field(default='', title='图片')
    record_type: str = Field(default='role', title='记录类别')
    state: str = Field(default='owned', title='持有状态')
    origin: str = Field(default='self', title='来源')
    updated_at: int = Field(default=0, title='更新时间')
    payload: str = Field(default='{}', title='完整记录JSON')

    def to_record_value(self) -> RoleRecordValue | bool | None:
        """还原为旧 JSON 结构的记录值，使上层读取路径与单文件存储时期保持同构。"""
        try:
            value = json.loads(self.payload)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            return value
        if self.record_type == MARKER_RECORD_TYPE:
            return True
        # payload 损坏时的兜底重建：宁可丢 role_ids，也要保住 name/image，否则展示层会整条空白。
        return {
            'name': self.name,
            'role_ids': [],
            'image': self.image,
            'record_type': self.record_type or 'role',
            'display_name': self.display_name,
            'updated_at': self.updated_at,
        }

    @classmethod
    def _row_from_value(
        cls,
        day: str,
        bot_id: str,
        group_id: str,
        bucket: str,
        user_key: str,
        value: RoleRecordValue | bool | None,
    ) -> "DailyWifeRecord":
        if isinstance(value, dict):
            try:
                updated_at = int(value.get('updated_at') or 0)
            except (TypeError, ValueError):
                updated_at = 0
            return cls(
                bot_id=bot_id,
                user_id=str(user_key),
                day=day,
                group_id=group_id,
                bucket=bucket,
                name=str(value.get('name') or ''),
                display_name=str(value.get('display_name') or ''),
                image=str(value.get('image') or ''),
                record_type=str(value.get('record_type') or 'role'),
                state=_record_state(value),
                origin=_record_origin(value),
                updated_at=updated_at,
                payload=json.dumps(value, ensure_ascii=False),
            )
        # 非 dict 值没有可拆分的字段，仅落 payload，避免为标记类记录虚构业务列。
        return cls(
            bot_id=bot_id,
            user_id=str(user_key),
            day=day,
            group_id=group_id,
            bucket=bucket,
            record_type=MARKER_RECORD_TYPE,
            payload=json.dumps(value, ensure_ascii=False),
        )

    @classmethod
    @with_read_session
    async def get_context(
        cls,
        session: AsyncSession,
        day: str,
        bot_id: str,
        group_id: str,
    ) -> DailyContext:
        """只加载单个 bot/group 上下文，避免每日热路径一次取回全天所有群的行。"""
        result = await session.execute(
            select(cls)
            .where(cls.day == day)
            .where(cls.bot_id == bot_id)
            .where(cls.group_id == group_id)
        )
        context: DailyContext = {}
        for row in result.scalars().all():
            context.setdefault(row.bucket, {})[row.user_id] = row.to_record_value()
        return context

    @classmethod
    @with_session
    async def upsert_record(
        cls,
        session: AsyncSession,
        day: str,
        bot_id: str,
        group_id: str,
        bucket: str,
        user_key: str,
        value: RoleRecordValue | bool | None,
    ) -> None:
        """以业务键 upsert 单条记录，使不同用户或桶的并发写入互不覆盖。"""
        row = cls._row_from_value(day, bot_id, group_id, bucket, user_key, value)
        values = {
            'name': row.name,
            'display_name': row.display_name,
            'image': row.image,
            'record_type': row.record_type,
            'state': row.state,
            'origin': row.origin,
            'updated_at': row.updated_at,
            'payload': row.payload,
        }
        statement = sqlite_insert(cls).values(
            day=day,
            bot_id=bot_id,
            group_id=group_id,
            bucket=bucket,
            user_id=str(user_key),
            **values,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=['day', 'bot_id', 'group_id', 'bucket', 'user_id'],
                set_=values,
            )
        )

    @classmethod
    @with_session
    async def upsert_records(
        cls,
        session: AsyncSession,
        day: str,
        bot_id: str,
        group_id: str,
        records: list[tuple[str, str, RoleRecordValue | bool | None]],
        deletes: list[tuple[str, str]] | None = None,
    ) -> None:
        """在同一事务内先删后写少量记录；删除必须先于插入，否则会连带清掉刚写入的行。"""
        if deletes:
            for bucket, user_key in deletes:
                await session.execute(
                    delete(cls)
                    .where(cls.day == day)
                    .where(cls.bot_id == bot_id)
                    .where(cls.group_id == group_id)
                    .where(cls.bucket == bucket)
                    .where(cls.user_id == str(user_key))
                )

        values = []
        for bucket, user_key, value in records:
            user_key = str(user_key)
            row = cls._row_from_value(day, bot_id, group_id, bucket, user_key, value)
            values.append(
                {
                    'day': day,
                    'bot_id': bot_id,
                    'group_id': group_id,
                    'bucket': bucket,
                    'user_id': user_key,
                    'name': row.name,
                    'display_name': row.display_name,
                    'image': row.image,
                    'record_type': row.record_type,
                    'state': row.state,
                    'origin': row.origin,
                    'updated_at': row.updated_at,
                    'payload': row.payload,
                }
            )

        if not values:
            return
        statement = sqlite_insert(cls).values(values)
        update_columns = _conflict_update_columns(statement)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=['day', 'bot_id', 'group_id', 'bucket', 'user_id'],
                set_=update_columns,
            )
        )

    @classmethod
    async def _upsert_rows(
        cls,
        session: AsyncSession,
        rows: list[tuple[str, str, str, str, str, RoleRecordValue | bool | None]],
    ) -> None:
        """在调用方事务中执行多上下文 upsert；不提交也不回滚，原子边界由调用方决定。"""
        values = []
        for day, bot_id, group_id, bucket, user_key, value in rows:
            row = cls._row_from_value(day, bot_id, group_id, bucket, str(user_key), value)
            values.append(
                {
                    'day': day,
                    'bot_id': bot_id,
                    'group_id': group_id,
                    'bucket': bucket,
                    'user_id': str(user_key),
                    'name': row.name,
                    'display_name': row.display_name,
                    'image': row.image,
                    'record_type': row.record_type,
                    'state': row.state,
                    'origin': row.origin,
                    'updated_at': row.updated_at,
                    'payload': row.payload,
                }
            )

        if not values:
            return
        statement = sqlite_insert(cls).values(values)
        update_columns = _conflict_update_columns(statement)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=['day', 'bot_id', 'group_id', 'bucket', 'user_id'],
                set_=update_columns,
            )
        )

    @classmethod
    async def _delete_rows(
        cls,
        session: AsyncSession,
        rows: list[tuple[str, str, str, str, str]],
    ) -> None:
        """在调用方事务中执行删除；不提交也不回滚，以便与同批 upsert 共用原子边界。"""
        for day, bot_id, group_id, bucket, user_key in rows:
            await session.execute(
                delete(cls)
                .where(cls.day == day)
                .where(cls.bot_id == bot_id)
                .where(cls.group_id == group_id)
                .where(cls.bucket == bucket)
                .where(cls.user_id == str(user_key))
            )

    @classmethod
    @with_session
    async def upsert_rows(
        cls,
        session: AsyncSession,
        rows: list[tuple[str, str, str, str, str, RoleRecordValue | bool | None]],
    ) -> None:
        """以一次多值 upsert 落盘多个上下文，避免按上下文逐次提交带来的额外事务开销。"""
        await cls._upsert_rows(session, rows)

    @classmethod
    @with_session
    async def delete_rows(
        cls,
        session: AsyncSession,
        rows: list[tuple[str, str, str, str, str]],
    ) -> None:
        """以单次事务删除多个上下文的记录，保证批内全部生效或全部回滚。"""
        await cls._delete_rows(session, rows)

    @classmethod
    @with_session
    async def apply_rows(
        cls,
        session: AsyncSession,
        rows: list[tuple[str, str, str, str, str, RoleRecordValue | bool | None]],
        deletes: list[tuple[str, str, str, str, str]],
    ) -> None:
        """在同一事务内应用删除与 upsert，避免部分成功使上下文停留在中间态。"""
        await cls._delete_rows(session, deletes)
        await cls._upsert_rows(session, rows)

    @classmethod
    @with_session
    async def upsert_context(
        cls,
        session: AsyncSession,
        day: str,
        bot_id: str,
        group_id: str,
        context: DailyContext,
    ) -> None:
        """以事务整体覆盖单个上下文快照，并同步清理快照中已消失的业务键。"""
        values = []
        desired_keys: set[tuple[str, str]] = set()
        for bucket, records in context.items():
            if not isinstance(records, dict):
                continue
            for user_key, value in records.items():
                user_key = str(user_key)
                desired_keys.add((bucket, user_key))
                row = cls._row_from_value(
                    day, bot_id, group_id, bucket, user_key, value
                )
                values.append(
                    {
                        'day': day,
                        'bot_id': bot_id,
                        'group_id': group_id,
                        'bucket': bucket,
                        'user_id': user_key,
                        'name': row.name,
                        'display_name': row.display_name,
                        'image': row.image,
                        'record_type': row.record_type,
                        'state': row.state,
                        'origin': row.origin,
                        'updated_at': row.updated_at,
                        'payload': row.payload,
                    }
                )

        # 快照也可能减少记录（离婚、赠送、补偿覆盖）；仅做 upsert 会让库中残留行在下次
        # hydrate 时重新出现，故先按业务键删除快照中已不存在的行，再写回当前快照。
        existing = await session.execute(
            select(cls.bucket, cls.user_id)
            .where(cls.day == day)
            .where(cls.bot_id == bot_id)
            .where(cls.group_id == group_id)
        )
        stale_keys = [
            (bucket, user_key)
            for bucket, user_key in existing.all()
            if (bucket, user_key) not in desired_keys
        ]
        if stale_keys:
            await session.execute(
                delete(cls)
                .where(cls.day == day)
                .where(cls.bot_id == bot_id)
                .where(cls.group_id == group_id)
                .where(tuple_(cls.bucket, cls.user_id).in_(stale_keys))
            )

        if not values:
            return
        statement = sqlite_insert(cls).values(values)
        update_columns = _conflict_update_columns(statement)
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=['day', 'bot_id', 'group_id', 'bucket', 'user_id'],
                set_=update_columns,
            )
        )

    @classmethod
    @with_session
    async def delete_record(
        cls,
        session: AsyncSession,
        day: str,
        bot_id: str,
        group_id: str,
        bucket: str,
        user_key: str,
    ) -> None:
        """按业务键删除单条记录；键不存在时静默成功，调用方无需预先探测。"""
        await session.execute(
            delete(cls)
            .where(cls.day == day)
            .where(cls.bot_id == bot_id)
            .where(cls.group_id == group_id)
            .where(cls.bucket == bucket)
            .where(cls.user_id == str(user_key))
        )

    @classmethod
    @with_session
    async def delete_before(cls, session: AsyncSession, cutoff_day: str) -> int:
        """删除 `cutoff_day` 之前的每日记录，返回删除行数。

        `day` 为 ISO 日期字符串（`YYYY-MM-DD`），字典序与时间序一致，可直接比较；
        表行数按“群 × 用户 × 桶 × 天数”增长，缺少清理会无限膨胀并使查询随天数退化。
        """
        result = await session.execute(delete(cls).where(cls.day < cutoff_day))
        return int(result.rowcount or 0)

    @classmethod
    @with_read_session
    async def get_record(
        cls,
        session: AsyncSession,
        day: str,
        bot_id: str,
        group_id: str,
        bucket: str,
        user_key: str,
    ) -> RoleRecordValue | bool | None:
        """读取单个业务键；返回 None 表示该键不存在，而非存在一条空记录。"""
        result = await session.execute(
            select(cls)
            .where(cls.day == day)
            .where(cls.bot_id == bot_id)
            .where(cls.group_id == group_id)
            .where(cls.bucket == bucket)
            .where(cls.user_id == str(user_key))
        )
        row = result.scalar_one_or_none()
        return row.to_record_value() if row is not None else None

    @classmethod
    @with_read_session
    async def count_daily_records(
        cls,
        session: AsyncSession,
        day: str,
        bucket_names: tuple[str, ...],
    ) -> dict[str, int]:
        """以聚合查询统计指定日期各桶的「原始」记录数量。

        旧实现会把当天所有行的 `payload` 拉回逐行 `json.loads` 再判断，等价于对全天
        所有群做全表扫描。判定条件已冗余到列上（非空 name 且 `origin == 'self'`，
        即不含 stolen_from/gifted_from/safe），因此改为带索引的 COUNT 聚合：既不
        传输也不解析 payload。返回的计数口径与旧实现完全一致，调用方无需感知差异，
        但该口径依赖写入时 state/origin 冗余列与 payload 始终同步。
        """
        if not bucket_names:
            return {}
        result = await session.execute(
            select(cls.bucket, func.count())
            .where(cls.day == day)
            .where(cls.bucket.in_(bucket_names))
            .where(cls.name != '')
            .where(cls.origin == 'self')
            .group_by(cls.bucket)
        )
        counts = {bucket: 0 for bucket in bucket_names}
        for bucket, count in result.all():
            counts[bucket] = int(count)
        return counts

    @classmethod
    @with_read_session
    async def load_day(
        cls,
        session: AsyncSession,
        day: str,
    ) -> dict[str, DailyContext]:
        """加载整天记录并重建旧 JSON 的嵌套结构，供跨群统计与迁移核对使用。"""
        result = await session.execute(select(cls).where(cls.day == day))
        contexts: dict[str, DailyContext] = {}
        for row in result.scalars().all():
            context_key = f'{row.bot_id}:{row.group_id}'
            bucket_data = contexts.setdefault(context_key, {}).setdefault(row.bucket, {})
            bucket_data[row.user_id] = row.to_record_value()
        return contexts

    @classmethod
    @with_session
    async def save_context(
        cls,
        session: AsyncSession,
        day: str,
        bot_id: str,
        group_id: str,
        context: DailyContext,
    ) -> int:
        """整体覆写某天某群的全部桶记录（先删后插，重复执行结果一致）。

        调用方必须持有该上下文的 `_daily_context_lock(ev)`（见 daily_store）：本方法的
        删除与插入之间存在窗口，缺少该锁时同一 (bot, group) 的并发读-改-写会相互覆盖。
        事务若在外部被中断，必须回滚，否则删除已生效而插入丢失，数据将出现整体缺口。
        """
        await session.execute(
            delete(cls)
            .where(cls.day == day)
            .where(cls.bot_id == bot_id)
            .where(cls.group_id == group_id)
        )
        rows: list[DailyWifeRecord] = []
        for bucket, records in context.items():
            if not isinstance(records, dict):
                continue
            for user_key, value in records.items():
                rows.append(cls._row_from_value(day, bot_id, group_id, bucket, user_key, value))
        if rows:
            session.add_all(rows)
        return len(rows)


# importlib / GsCore 热加载会在同一 SQLModel.metadata 中重复声明本表：SQLModel 对
# ``Field(index=True)`` 的重复声明会挂上等价但独立的 Index 对象，随后 create_all 会对
# 同一索引执行两次 CREATE INDEX 而报错。此处按签名去重只保留一个，不改动表名与索引名，
# 从而无需为既有数据库安排迁移。
def _deduplicate_table_indexes(table: Table) -> None:
    seen: set[tuple[str | None, tuple[str, ...], bool | None]] = set()
    for index in tuple(table.indexes):
        signature = (
            index.name,
            tuple(column.key for column in index.columns),
            index.unique,
        )
        if signature in seen:
            table.indexes.discard(index)
        else:
            seen.add(signature)


_deduplicate_table_indexes(DailyWifeRecord.__table__)


@on_core_start_before(priority=-70)
async def _ensure_daily_wife_record_table() -> None:
    """补建本插件数据表：模型注册晚于 Core 全局建表，缺少该步骤时首启查询会因表不存在而失败。"""
    async with engine.begin() as conn:
        await conn.run_sync(
            DailyWifeRecord.metadata.create_all,
            tables=[DailyWifeRecord.metadata.tables['dailywiferecord']],
            checkfirst=True,
        )


# 为既有数据库补建业务键唯一索引：SQLite 的 ON CONFLICT 依赖它作为冲突目标，缺失即退化为重复插入。
exec_list.append(
    'CREATE UNIQUE INDEX IF NOT EXISTS '
    'ix_daily_wife_record_business_key '
    'ON DailyWifeRecord (day, bot_id, group_id, bucket, user_id)'
)


@site.register_admin
class DailyWifeRecordAdmin(GsAdminModel):
    pk_name = 'id'
    page_schema = PageSchema(
        label='今日老婆每日记录',
        icon='fa fa-heart',
    )  # type: ignore

    model = DailyWifeRecord
