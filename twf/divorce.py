"""TodayWaifu unified divorce command."""
from __future__ import annotations

from .shared import (
    Bot,
    Event,
    LOG_PREFIX,
    _get_today_context,
    _load_wife_data,
    _mark_all_daily_records_divorced,
    _save_wife_data,
    _send_prefixed,
    _user_key,
    divorce_sv,
    logger,
    time,
)


DIVORCE_COMMANDS = (
    '离婚',
    '全部离婚',
    '老婆离婚',
    '离婚老婆',
    '今日老婆离婚',
    '和老婆离婚',
    '老公离婚',
    '离婚老公',
    '今日老公离婚',
    '和老公离婚',
    '萝莉离婚',
    '离婚萝莉',
    '今日萝莉离婚',
    '和萝莉离婚',
    '异环老婆离婚',
    '离婚异环老婆',
    '战双老婆离婚',
    '离婚战双老婆',
)


def _divorce_result_name(kind: str, name: str) -> str:
    """把内部记录名称转换为适合用户阅读的离婚结果。"""
    if kind == 'loli':
        return '今日萝莉'
    return name


async def _send_divorce_all(bot: Bot, ev: Event) -> None:
    user_key = _user_key(ev)
    logger.info(
        f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} '
        '发起统一离婚'
    )

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    divorced = _mark_all_daily_records_divorced(context, user_key, int(time.time()))

    from .gift import clear_pending_gifts_for_user

    clear_pending_gifts_for_user(ev, user_key)
    if not divorced:
        return await _send_prefixed(bot, '你今天没有可以离婚的对象。')

    _save_wife_data(data)
    names = '、'.join(
        dict.fromkeys(_divorce_result_name(kind, name) for kind, name in divorced)
    )
    await _send_prefixed(bot, f'已经全部离婚：{names}。')


@divorce_sv.on_fullmatch(
    DIVORCE_COMMANDS,
    block=True,
    to_ai="""一次性结束当前用户今天的全部婚姻关系。
    会同时处理今日老婆、异环老婆、战双老婆、今日老公、今日萝莉、娶群友和补偿老婆。
    当用户说“离婚”“全部离婚”或要结束今天的任意婚姻关系时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def divorce_all(bot: Bot, ev: Event) -> None:
    await _send_divorce_all(bot, ev)
