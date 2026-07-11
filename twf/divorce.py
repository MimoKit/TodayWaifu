"""TodayWaifu divorce command."""
from __future__ import annotations

from .shared import (
    Bot,
    Event,
    LOG_PREFIX,
    _daily_bucket_name,
    _daily_item_title,
    _get_today_context,
    _has_active_wife,
    _load_wife_data,
    _save_wife_data,
    _send_prefixed,
    _user_key,
    divorce_sv,
    logger,
    time,
)


async def _send_divorce(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} 发起离婚: {title}')

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    bucket = _daily_bucket_name(kind)
    user_key = _user_key(ev)
    current = context[bucket].get(user_key)
    if not _has_active_wife(current):
        return await _send_prefixed(bot, f'你今天没有可以离婚的{title}。', kind=kind)

    if not isinstance(current, dict):
        return await _send_prefixed(bot, f'你今天没有可以离婚的{title}。', kind=kind)

    item_name = str(current.get('name') or title)
    current['divorced'] = True
    current['divorced_at'] = int(time.time())
    _save_wife_data(data)

    if kind == 'loli':
        return await _send_prefixed(bot, '你已经和今天的萝莉离婚了。', kind=kind)
    await _send_prefixed(bot, f'你已经和今天的{title}{item_name}离婚了。', kind=kind)


@divorce_sv.on_fullmatch(
    ('老婆离婚', '离婚', '离婚老婆', '今日老婆离婚', '和老婆离婚'),
    block=True,
    to_ai="""和当前用户今天的老婆离婚。
    当用户说“我要和老婆离婚”“离婚老婆”“今日老婆离婚”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def divorce_wife(bot: Bot, ev: Event):
    await _send_divorce(bot, ev, 'wife')


@divorce_sv.on_fullmatch(
    ('老公离婚', '离婚老公', '今日老公离婚', '和老公离婚'),
    block=True,
    to_ai="""和当前用户今天的老公离婚。
    当用户说“我要和老公离婚”“老公离婚”“今日老公离婚”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def divorce_husband(bot: Bot, ev: Event):
    await _send_divorce(bot, ev, 'husband')


@divorce_sv.on_fullmatch(
    ('萝莉离婚', '离婚萝莉', '今日萝莉离婚', '和萝莉离婚'),
    block=True,
    to_ai="""和当前用户今天的萝莉离婚。
    当用户说“我要和萝莉离婚”“萝莉离婚”“今日萝莉离婚”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def divorce_loli(bot: Bot, ev: Event):
    await _send_divorce(bot, ev, 'loli')
