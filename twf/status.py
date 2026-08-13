"""TodayWaifu status metrics for core status page."""
from __future__ import annotations

from typing import Any

from PIL import Image

from gsuid_core.status.plugin_status import register_status

from .shared import HELP_ICON_PATH, _daily_bucket_name, _load_wife_data, _today_key


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
    data = await _load_wife_data()
    days = data.get('days')
    if not isinstance(days, dict):
        return {}
    today = days.get(_today_key())
    if not isinstance(today, dict):
        return {}
    return today


async def _today_record_count(kind: str) -> int:
    return _daily_record_count(await _today_data(), _daily_bucket_name(kind))


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
