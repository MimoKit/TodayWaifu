"""TodayWaifu 的消息发送兼容层。"""
from __future__ import annotations

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Message

from .payloads import SendMessage
from .constants import LOG_PREFIX


def _is_xwuid_group_activity_hook_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        isinstance(exc, AttributeError)
        and 'PluginHookManager' in message
        and 'group_activity_hooks' in message
    )


def _parse_send_options(
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> tuple[bool, dict[str, object] | None, bool]:
    options = dict(kwargs)
    at_sender = options.pop('at_sender', False)
    extra_metadata = options.pop('extra_metadata', None)
    wait_recall = options.pop('wait_recall', False)

    if len(args) > 3:
        raise TypeError(f'Bot.send expected at most 3 positional options, got {len(args)}')
    if len(args) >= 1:
        at_sender = args[0]
    if len(args) >= 2:
        extra_metadata = args[1]
    if len(args) >= 3:
        wait_recall = args[2]
    if options:
        unexpected = ', '.join(options)
        raise TypeError(f'Bot.send got unexpected keyword argument(s): {unexpected}')
    metadata = extra_metadata if isinstance(extra_metadata, dict) else None
    return bool(at_sender), metadata, bool(wait_recall)


async def _target_send_without_bot_hooks(
    bot: Bot,
    message: SendMessage,
    *args: object,
    **kwargs: object,
) -> list[str] | None:
    at_sender, extra_metadata, wait_recall = _parse_send_options(args, kwargs)
    ev = bot.ev
    target_type = ev.user_type
    target_id = ev.user_id if ev.user_type == 'direct' else ev.group_id
    return await bot.bot.target_send(
        message,
        target_type,
        target_id,
        ev.real_bot_id,
        bot.bot_self_id,
        ev.msg_id,
        at_sender,
        ev.user_id,
        ev.group_id,
        ev.task_id,
        ev.task_event,
        extra_metadata=extra_metadata,
        wait_recall=wait_recall,
    )


def _is_at_message(item: object) -> bool:
    return isinstance(item, Message) and item.type == 'at'


def _remove_private_mentions(message: SendMessage) -> SendMessage:
    items: list[object] = list(message) if isinstance(message, list) else [message]
    result: list[object] = []
    skip_linebreak = False
    for item in items:
        if _is_at_message(item):
            skip_linebreak = True
            continue
        if skip_linebreak and isinstance(item, str) and item in ('\n', '\r\n'):
            skip_linebreak = False
            continue
        skip_linebreak = False
        result.append(item)

    if isinstance(message, list):
        return result
    return result[0] if result else ''


def _adapt_mentions_for_platform(bot: Bot, message: SendMessage) -> SendMessage:
    if bot.ev.user_type == 'direct':
        return _remove_private_mentions(message)
    return message


async def _safe_send(
    bot: Bot,
    message: SendMessage,
    *args: object,
    **kwargs: object,
) -> list[str] | None:
    adapted = _adapt_mentions_for_platform(bot, message)
    try:
        return await bot.send(adapted, *args, **kwargs)
    except AttributeError as exc:
        if not _is_xwuid_group_activity_hook_error(exc):
            raise
        logger.warning(f'{LOG_PREFIX} 检测到 XWUID BotHook 兼容问题，改用底层发送: {exc}')
        return await _target_send_without_bot_hooks(bot, adapted, *args, **kwargs)


async def _send_loli_text(bot: Bot, text: str, *args: object, **kwargs: object) -> list[str] | None:
    return await _safe_send(bot, text, *args, **kwargs)


async def _send_shota_text(bot: Bot, text: str, *args: object, **kwargs: object) -> list[str] | None:
    return await _safe_send(bot, text, *args, **kwargs)
