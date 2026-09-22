"""TodayWaifu divorce commands."""
from __future__ import annotations

from .shared import (
    LOG_PREFIX,
    Bot,
    Event,
    time,
    logger,
    _user_key,
    _safe_send,
    divorce_sv,
    _daily_bucket_name,
    _daily_context_lock,
    _load_daily_context,
    _save_daily_records,
)

DIVORCE_COMMANDS = (
    '离婚',
    '老婆离婚',
    '离婚老婆',
    '今日老婆离婚',
    '和老婆离婚',
    '离婚群友',
    '群友离婚',
    '今日群友离婚',
    '和群友离婚',
)
HUSBAND_DIVORCE_COMMANDS = (
    '老公离婚',
    '离婚老公',
    '今日老公离婚',
    '和老公离婚',
)
LOLI_DIVORCE_COMMANDS = (
    '萝莉离婚',
    '离婚萝莉',
    '今日萝莉离婚',
    '和萝莉离婚',
)
SHOTA_DIVORCE_COMMANDS = (
    '正太离婚',
    '离婚正太',
    '今日正太离婚',
    '和正太离婚',
)
NTE_DIVORCE_COMMANDS = ('异环老婆离婚', '离婚异环老婆')
PGR_DIVORCE_COMMANDS = ('战双老婆离婚', '离婚战双老婆')


def _divorce_result_name(kind: str, name: str) -> str:
    """把内部记录名称转换为适合用户阅读的离婚结果。"""
    if kind == 'loli':
        return '今日萝莉'
    if kind == 'shota':
        return '今日正太'
    return name


async def _send_divorce(bot: Bot, ev: Event, kind: str) -> None:
    user_key = _user_key(ev)
    title = {
        'wife': '老婆',
        'husband': '老公',
        'loli': '萝莉',
        'shota': '正太',
        'nte': '异环老婆',
        'pgr': '战双老婆',
    }[kind]
    logger.info(
        f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} '
        f'发起{title}离婚'
    )

    response: str | None = None
    result_name = ''
    async with _daily_context_lock(ev):
        context = await _load_daily_context(ev)
        bucket_name = _daily_bucket_name(kind)
        bucket = context[bucket_name]
        record = bucket.get(user_key)
        item_title = '群友' if isinstance(record, dict) and record.get('record_type') == 'member' else title
        if not isinstance(record, dict) or not str(record.get('name') or '').strip():
            response = f'你今天没有可以离婚的{item_title}。'
        elif record.get('divorced'):
            response = f'你今天已经和{item_title}离婚了。'
        else:
            updated_record = dict(record)
            updated_record['divorced'] = True
            updated_record['divorced_at'] = int(time.time())
            await _save_daily_records(ev, [(bucket_name, user_key, updated_record)])
            result_name = _divorce_result_name(kind, str(record['name']))

    if response is not None:
        return await _safe_send(bot, response)
    await _safe_send(bot, f'已经和今天的{item_title}离婚：{result_name}。')


@divorce_sv.on_fullmatch(
    DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的老婆婚姻关系。
    “离婚”默认表示离婚老婆，也可以使用老婆离婚等同义命令。
    Args:
        text: 无需参数，留空。
    """,
    covers=['结束今日老婆婚姻关系'],
    aliases=['今日老婆·离婚', '今日老婆·和老婆离婚'],
)
async def divorce_wife(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'wife')


@divorce_sv.on_fullmatch(
    HUSBAND_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的老公婚姻关系。
    当用户说“跟老公离婚”“老公离婚”“离婚老公”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日老公婚姻关系'],
    aliases=['今日老婆·和老公离婚', '今日老婆·老公离婚'],
)
async def divorce_husband(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'husband')


@divorce_sv.on_fullmatch(
    LOLI_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的萝莉关系。
    当用户说“跟萝莉离婚”“萝莉离婚”“离婚萝莉”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日萝莉关系'],
    aliases=['今日老婆·和萝莉离婚', '今日老婆·萝莉离婚'],
)
async def divorce_loli(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'loli')


@divorce_sv.on_fullmatch(
    SHOTA_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的正太关系。
    当用户说“跟正太离婚”“正太离婚”“离婚正太”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日正太关系'],
    aliases=['今日老婆·和正太离婚', '今日老婆·正太离婚'],
)
async def divorce_shota(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'shota')


@divorce_sv.on_fullmatch(
    NTE_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的异环老婆婚姻关系。
    当用户说“异环老婆离婚”“离婚异环老婆”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日异环老婆婚姻关系'],
    aliases=['今日老婆·异环老婆离婚'],
)
async def divorce_nte(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'nte')


@divorce_sv.on_fullmatch(
    PGR_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的战双老婆婚姻关系。
    当用户说“战双老婆离婚”“离婚战双老婆”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日战双老婆婚姻关系'],
    aliases=['今日老婆·战双老婆离婚'],
)
async def divorce_pgr(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'pgr')
