"""TodayWaifu status metrics for core status page."""
from __future__ import annotations

import time
import asyncio

from PIL import Image

from gsuid_core.status.plugin_status import register_status

from .shared import (
    HELP_ICON_PATH,
    STATUS_MIN_RECOMPUTE_SECONDS,
    DailyWifeRecord,
    _today_key,
    _load_wife_data,
    _daily_bucket_name,
)
from .payloads import DailyContext

_STATUS_INFLIGHT: asyncio.Task[dict[str, int]] | None = None
_STATUS_CACHE: tuple[str, dict[str, int]] | None = None
_STATUS_COMPUTED_AT = 0.0
_STATUS_STALE = True


def _is_countable_daily_record(raw: object) -> bool:
    if not isinstance(raw, dict):
        return False
    name = raw.get('name')
    if not isinstance(name, str) or not name.strip():
        return False
    return not (raw.get('stolen_from') or raw.get('gifted_from') or raw.get('safe'))


def _daily_record_count(day_data: object, bucket_name: str) -> int:
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


async def _today_data() -> DailyContext:
    """兼容旧的状态读取辅助函数；指标本身使用下方的一次聚合查询。"""
    data = await _load_wife_data()
    days = data.get('days')
    # 保留旧数据结构的 days.get(_today_key()) 兼容口径。
    today = days.get(_today_key()) if isinstance(days, dict) else {}
    return today if isinstance(today, dict) else {}


async def _today_record_counts() -> dict[str, int]:
    """一次数据库查询计算三个指标，避免每个回调重复 hydrate 全部上下文。"""
    global _STATUS_INFLIGHT, _STATUS_CACHE, _STATUS_COMPUTED_AT, _STATUS_STALE
    day = _today_key()
    cached = _STATUS_CACHE
    if cached is not None and cached[0] == day:
        # 刚提交过写入但还没到最小重算间隔时，先返回上一次的聚合结果。
        # 高峰期每次写入都重算会让网页控制台的每次轮询都打一次数据库聚合。
        fresh_enough = (
            not _STATUS_STALE
            or time.monotonic() - _STATUS_COMPUTED_AT < STATUS_MIN_RECOMPUTE_SECONDS
        )
        if fresh_enough:
            return cached[1]

    task = _STATUS_INFLIGHT
    if task is None:
        bucket_names = (
            _daily_bucket_name('wife'),
            _daily_bucket_name('loli'),
            _daily_bucket_name('shota'),
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
    _STATUS_COMPUTED_AT = time.monotonic()
    _STATUS_STALE = False
    return counts


def invalidate_status_cache() -> None:
    """在每日记录成功提交后把聚合快照标记为过期。

    这里**只标记、不丢弃**：真正的重算由 `_today_record_counts` 按
    `STATUS_MIN_RECOMPUTE_SECONDS` 的最小间隔决定，避免高峰期每次写入
    都让网页控制台的下一次轮询触发一次全表聚合。
    """
    global _STATUS_STALE
    _STATUS_STALE = True


async def _today_record_count(kind: str) -> int:
    counts = await _today_record_counts()
    return int(counts.get(_daily_bucket_name(kind), 0))


async def get_today_wife_count() -> int:
    return await _today_record_count('wife')


async def get_today_loli_count() -> int:
    return await _today_record_count('loli')


async def get_today_shota_count() -> int:
    return await _today_record_count('shota')


async def get_today_husband_count() -> int:
    return await _today_record_count('husband')


register_status(
    Image.open(HELP_ICON_PATH).convert('RGBA'),
    'TodayWaifu',
    {
        '今日老婆': get_today_wife_count,
        '今日萝莉': get_today_loli_count,
        '今日正太': get_today_shota_count,
        '今日老公': get_today_husband_count,
    },
)
