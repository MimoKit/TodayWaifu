"""TodayWaifu 每日记录数据库模型。

替代旧的 data/TodayWaifu/daily_wife_data.json 单文件存储：
一行 = 某用户（user_id）某天（day）在某群（bot_id+group_id）某个桶（bucket）里的一条记录。
record 字典整体序列化进 payload 列，name/state/origin 等列用于控制台展示与查询过滤。

本模块不依赖 TodayWaifu 内其它模块，可独立加载（测试用 importlib 直接加载）。
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
    # 仅供类型检查器解析：本模块会被测试用 importlib 独立加载，不能有运行时相对导入。
    from .payloads import WifeData, DailyContext, RoleRecordValue

LOG_PREFIX = '[鸣潮今日老婆]'

# 迁移旧 JSON 时只保留最近几天的数据
LEGACY_MIGRATION_KEEP_DAYS = 2

# 非 dict 的桶记录（如 rob_attempts 里的 True 标记）在表里的 record_type
MARKER_RECORD_TYPE = 'marker'


class _ExcludedColumns(Protocol):
    """SQLite upsert 冲突时本次待写入的列集合（statement.excluded）。"""

    def __getitem__(self, key: str) -> ColumnElement[object]: ...


def _conflict_update_columns(statement: Insert) -> dict[str, ColumnElement[object]]:
    """冲突时用 excluded（本次待写入的值）覆盖的业务列；列名固定，逐列按名取。"""
    excluded: _ExcludedColumns = statement.excluded
    return {column: excluded[column] for column in _CONFLICT_UPDATE_COLUMNS}


# upsert 冲突时需要覆盖的业务列（与 _row_from_value 写入的列保持一致）
_CONFLICT_UPDATE_COLUMNS = (
    'name', 'display_name', 'image', 'record_type', 'state',
    'origin', 'updated_at', 'payload',
)


def _record_state(raw: object) -> str:
    """与 shared._wife_state 口径一致：owned/lost_stolen/lost_gifted/divorced。"""
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
    """与 shared._wife_origin 口径一致：self/robbed/gifted/safe。"""
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
    """把 shared._context_key 拼出的 'bot_id:group_id' 拆回两段。"""
    bot_id, _, group_id = str(context_key).partition(':')
    return bot_id, group_id or 'direct'


class DailyWifeRecord(BaseModel, table=True):
    """今日老婆每日记录表。"""

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
        """还原为旧 JSON 结构里的记录值（dict 或 True 标记）。"""
        try:
            value = json.loads(self.payload)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            return value
        if self.record_type == MARKER_RECORD_TYPE:
            return True
        # payload 损坏时的兜底重建，保证 name/image 等关键字段不丢
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
        # 非 dict 值（如 rob_attempts 的 True 标记）只保留 payload
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
        """只加载一个 bot/group 上下文，供每日热路径使用。"""
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
        """定向写入一条记录，不影响同一上下文的其它用户或桶。"""
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
        """在一个事务中定向更新或删除少量业务记录。"""
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
    @with_session
    async def upsert_context(
        cls,
        session: AsyncSession,
        day: str,
        bot_id: str,
        group_id: str,
        context: DailyContext,
    ) -> None:
        """在一个写事务中 upsert 一个上下文的全部记录。"""
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

        # 快照也可能删除记录（离婚、赠送、补偿覆盖），先清理数据库中
        # 不再存在的业务键，避免仅 upsert 导致旧记录重新 hydrate 出现。
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
        """定向删除一条记录。"""
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

        `day` 是 ISO 日期字符串（`YYYY-MM-DD`），字典序与时间序一致，可以直接比较。
        表行数 = 群 × 用户 × 桶 × 天数，不清理会随天数无限增长。
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
        """读取一个业务键对应的记录值。"""
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
        """一次聚合查询统计指定日期各桶的「原始」记录数量。

        原来是把当天所有行的 `payload` 全拉回来逐行 `json.loads` 再判断，
        是**全天所有群的全表扫描**。这些判断条件其实都已经落到列上了：
        非空 name + `origin == 'self'`（即没有 stolen_from/gifted_from/safe），
        所以改成一条带索引的 COUNT 聚合，不再传输与解析任何 payload。
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
        """加载某一天的全部记录，返回 {context_key: {bucket: {user_key: value}}}。"""
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
        """整体覆写某一天某个群的全部桶记录（先删后插，幂等）。

        调用方必须持有 shared._daily_data_lock，保证读-改-写串行。
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

    @classmethod
    @with_session
    async def import_legacy_data(
        cls,
        session: AsyncSession,
        data: WifeData,
        keep_days: int = LEGACY_MIGRATION_KEEP_DAYS,
    ) -> int:
        """导入旧 daily_wife_data.json 的内容，只保留最近 keep_days 天。

        每个 (day, context) 都是先删后插，重复执行结果一致（幂等）。
        """
        days = data.get('days') if isinstance(data, dict) else None
        if not isinstance(days, dict) or not days:
            return 0

        imported = 0
        for day in sorted((str(key) for key in days.keys()), reverse=True)[:keep_days]:
            contexts = days.get(day)
            if not isinstance(contexts, dict):
                continue
            for context_key, context in contexts.items():
                if not isinstance(context, dict):
                    continue
                bot_id, group_id = split_context_key(context_key)
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
                        rows.append(
                            cls._row_from_value(day, bot_id, group_id, bucket, user_key, value)
                        )
                if rows:
                    session.add_all(rows)
                    imported += len(rows)
        logger.info(f'{LOG_PREFIX} 旧 JSON 数据迁移完成，共导入 {imported} 条记录')
        return imported


# importlib / GsCore 热加载可能在同一个 SQLModel.metadata 中再次声明本表。
# SQLModel 对 ``Field(index=True)`` 的重复声明会挂上同名 Index，随后
# create_all 会尝试执行两次 CREATE INDEX。只保留同一表上的一个等价索引，
# 不改变现有表名、索引名或业务键。
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
    """插件模型晚于 Core 全局建表时，补建本插件数据表。"""
    async with engine.begin() as conn:
        await conn.run_sync(
            DailyWifeRecord.metadata.create_all,
            tables=[DailyWifeRecord.metadata.tables['dailywiferecord']],
            checkfirst=True,
        )


# 为已有数据库补充业务键唯一约束，供 SQLite upsert 使用。
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
