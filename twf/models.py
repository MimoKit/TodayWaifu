"""TodayWaifu 每日记录数据库模型。

替代旧的 data/TodayWaifu/daily_wife_data.json 单文件存储：
一行 = 某用户（user_id）某天（day）在某群（bot_id+group_id）某个桶（bucket）里的一条记录。
record 字典整体序列化进 payload 列，name/state/origin 等列用于控制台展示与查询过滤。

本模块不依赖 twf 内其它模块，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from sqlmodel import Field, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gsuid_core.logger import logger
from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.base_models import BaseModel, with_session

LOG_PREFIX = '[鸣潮今日老婆]'

# 迁移旧 JSON 时只保留最近几天的数据
LEGACY_MIGRATION_KEEP_DAYS = 2

# 非 dict 的桶记录（如 rob_attempts 里的 True 标记）在表里的 record_type
MARKER_RECORD_TYPE = 'marker'


def _record_state(raw: Any) -> str:
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


def _record_origin(raw: Any) -> str:
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

    __table_args__: Dict[str, Any] = {"extend_existing": True}

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

    def to_record_value(self) -> Any:
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
        value: Any,
    ) -> 'DailyWifeRecord':
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
    @with_session
    async def load_day(
        cls,
        session: AsyncSession,
        day: str,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """加载某一天的全部记录，返回 {context_key: {bucket: {user_key: value}}}。"""
        result = await session.execute(select(cls).where(cls.day == day))
        contexts: Dict[str, Dict[str, Dict[str, Any]]] = {}
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
        context: Dict[str, Dict[str, Any]],
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
        rows: List['DailyWifeRecord'] = []
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
        data: Dict[str, Any],
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
                rows: List['DailyWifeRecord'] = []
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


@site.register_admin
class DailyWifeRecordAdmin(GsAdminModel):
    pk_name = 'id'
    page_schema = PageSchema(
        label='今日老婆每日记录',
        icon='fa fa-heart',
    )  # type: ignore

    model = DailyWifeRecord
