"""TodayWaifu 的消息发送兼容层。"""
from __future__ import annotations

from typing import Any

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Message


LOG_PREFIX = '[鸣潮今日老婆]'


def is_xwuid_group_activity_hook_error(exc: Exception) -> bool:
    message = str(exc)
    return isinstance(exc, AttributeError) and 'PluginHookManager' in message and 'group_activity_hooks' in message


def parse_send_options(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[bool, Any, bool]:
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
    return bool(at_sender), extra_metadata, bool(wait_recall)


async def target_send_without_bot_hooks(bot: Bot, message: Any, *args: Any, **kwargs: Any) -> Any:
    at_sender, extra_metadata, wait_recall = parse_send_options(args, kwargs)
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


def remove_private_mentions(message: Any) -> Any:
    items = message if isinstance(message, list) else [message]
    result: list[Any] = []
    skip_linebreak = False
    for item in items:
        if isinstance(item, Message) and item.type == 'at':
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


def adapt_mentions_for_platform(bot: Bot, message: Any) -> Any:
    if bot.ev.user_type == 'direct':
        return remove_private_mentions(message)
    return message


async def safe_send(bot: Bot, message: Any, *args: Any, **kwargs: Any) -> Any:
    message = adapt_mentions_for_platform(bot, message)
    try:
        return await bot.send(message, *args, **kwargs)
    except AttributeError as exc:
        if not is_xwuid_group_activity_hook_error(exc):
            raise
        logger.warning(f'{LOG_PREFIX} 检测到 XWUID BotHook 兼容问题，改用底层发送: {exc}')
        return await target_send_without_bot_hooks(bot, message, *args, **kwargs)


async def send_loli_text(bot: Bot, text: str, *args: Any, **kwargs: Any) -> Any:
    return await safe_send(bot, text, *args, **kwargs)
