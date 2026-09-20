"""TodayWaifu status metrics for core status page."""
from __future__ import annotations

import asyncio
from typing import Any

from PIL import Image

from gsuid_core.status.plugin_status import register_status

from .shared import DailyWifeRecord, HELP_ICON_PATH, _daily_bucket_name, _load_wife_data, _today_key


_STATUS_INFLIGHT: asyncio.Task[dict[str, int]] | None = None
_STATUS_CACHE: tuple[str, dict[str, int]] | None = None


def _is_countable_daily_record(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    name = raw.get('name')
    if not isinstance(name, str) or not name.strip():
        return False
    return not (raw.get('stolen_from') or raw.get('gifted_from') or raw.get('safe'))


def _daily_record_count(day_data: Any, bucket_name: str) -> int:
    if not isinstance(day_data, dict):
        return 0

    count = 0
    for context in day_data.values():
        if not isinstance(context, dict):
            continue
        bucket = context.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        count += sum(1 for raw in bucket.values() if _is_countable_daily_record(raw))
    return count


async def _today_data() -> dict[str, Any]:
    """兼容旧的状态读取辅助函数；指标本身使用下方的一次聚合查询。"""
    data = await _load_wife_data()
    days = data.get('days')
    # 保留旧数据结构的 days.get(_today_key()) 兼容口径。
    today = days.get(_today_key()) if isinstance(days, dict) else {}
    return today if isinstance(today, dict) else {}


async def _today_record_counts() -> dict[str, int]:
    """一次数据库查询计算三个指标，避免每个回调重复 hydrate 全部上下文。"""
    global _STATUS_INFLIGHT, _STATUS_CACHE
    day = _today_key()
    if _STATUS_CACHE is not None and _STATUS_CACHE[0] == day:
        return _STATUS_CACHE[1]

    task = _STATUS_INFLIGHT
    if task is None:
        bucket_names = (
            _daily_bucket_name('wife'),
            _daily_bucket_name('loli'),
            _daily_bucket_name('husband'),
        )

        async def load() -> dict[str, int]:
            return await DailyWifeRecord.count_daily_records(day, bucket_names)

        task = asyncio.create_task(load())
        _STATUS_INFLIGHT = task
    try:
        counts = await task
    finally:
        if task.done() and _STATUS_INFLIGHT is task:
            _STATUS_INFLIGHT = None
    _STATUS_CACHE = (day, counts)
    return counts


def invalidate_status_cache() -> None:
    """在每日记录成功提交后丢弃聚合快照。"""
    global _STATUS_CACHE
    _STATUS_CACHE = None


async def _today_record_count(kind: str) -> int:
    counts = await _today_record_counts()
    return int(counts.get(_daily_bucket_name(kind), 0))


async def get_today_wife_count() -> int:
    return await _today_record_count('wife')


async def get_today_loli_count() -> int:
    return await _today_record_count('loli')


async def get_today_husband_count() -> int:
    return await _today_record_count('husband')


register_status(
    Image.open(HELP_ICON_PATH).convert('RGBA'),
    'TodayWaifu',
    {
        '今日老婆': get_today_wife_count,
        '今日萝莉': get_today_loli_count,
        '今日老公': get_today_husband_count,
    },
)
