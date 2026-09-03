"""TodayWaifu divorce commands."""
from __future__ import annotations

from .shared import (
    Bot,
    Event,
    LOG_PREFIX,
    _daily_bucket_name,
    _daily_context_lock,
    _load_daily_context,
    _save_daily_records,
    _safe_send,
    _user_key,
    divorce_sv,
    logger,
    time,
)


DIVORCE_COMMANDS = (
    '离婚',
    '老婆离婚',
    '离婚老婆',
    '今日老婆离婚',
    '和老婆离婚',
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
NTE_DIVORCE_COMMANDS = ('异环老婆离婚', '离婚异环老婆')
PGR_DIVORCE_COMMANDS = ('战双老婆离婚', '离婚战双老婆')


def _divorce_result_name(kind: str, name: str) -> str:
    """把内部记录名称转换为适合用户阅读的离婚结果。"""
    if kind == 'loli':
        return '今日萝莉'
    return name


async def _send_divorce(bot: Bot, ev: Event, kind: str) -> None:
    user_key = _user_key(ev)
    title = {
        'wife': '老婆',
        'husband': '老公',
        'loli': '萝莉',
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
        if not isinstance(record, dict) or not str(record.get('name') or '').strip():
            response = f'你今天没有可以离婚的{title}。'
        elif record.get('divorced'):
            response = f'你今天已经和{title}离婚了。'
        else:
            updated_record = dict(record)
            updated_record['divorced'] = True
            updated_record['divorced_at'] = int(time.time())
            await _save_daily_records(ev, [(bucket_name, user_key, updated_record)])
            result_name = _divorce_result_name(kind, str(record['name']))

    if response is not None:
        return await _safe_send(bot, response)
    await _safe_send(bot, f'已经和今天的{title}离婚：{result_name}。')


@divorce_sv.on_fullmatch(
    DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的老婆婚姻关系。
    “离婚”默认表示离婚老婆，也可以使用老婆离婚等同义命令。
    Args:
        text: 无需参数，留空。
    """,
)
async def divorce_wife(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'wife')


@divorce_sv.on_fullmatch(HUSBAND_DIVORCE_COMMANDS, block=True)
async def divorce_husband(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'husband')


@divorce_sv.on_fullmatch(LOLI_DIVORCE_COMMANDS, block=True)
async def divorce_loli(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'loli')


@divorce_sv.on_fullmatch(NTE_DIVORCE_COMMANDS, block=True)
async def divorce_nte(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'nte')


@divorce_sv.on_fullmatch(PGR_DIVORCE_COMMANDS, block=True)
async def divorce_pgr(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'pgr')
