"""TodayWaifu gift command."""
from __future__ import annotations

import time
from typing import Any

from .shared import (
    Bot,
    Event,
    LOG_PREFIX,
    MessageSegment,
    RoleCandidate,
    _cfg,
    _cfg_bool,
    _context_key,
    _daily_bucket_name,
    _daily_item_title,
    _daily_kind_metadata,
    _get_event_target_user_id,
    _get_existing_daily_record,
    _get_today_context,
    _has_active_wife,
    _husband_available,
    _husband_unavailable_message,
    _is_secondhand_wife,
    _load_wife_data,
    _record_to_dict,
    _save_wife_data,
    _send_daily_result_image,
    _send_prefixed,
    _user_display_name,
    _user_key,
    _wife_state,
    logger,
    gift_sv,
)


GIFT_CONFIRM_TIMEOUT_SECONDS = 60
_GIFT_PENDING: dict[str, dict[str, Any]] = {}


def _gift_enabled(kind: str) -> bool:
    return _cfg_bool(_daily_kind_metadata(kind).gift_enabled_key, True)


def _gift_success_template(kind: str) -> str:
    metadata = _daily_kind_metadata(kind)
    return str(_cfg(metadata.gift_success_key) or metadata.gift_success_default)


def _build_gift_success_text(role: RoleCandidate, target_user_id: str, kind: str) -> str:
    template = _gift_success_template(kind)
    return template.format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )


def _gift_pending_key(ev: Event, target_user_id: str, kind: str = 'wife') -> str:
    return f'{_context_key(ev)}:{kind}:{target_user_id}'


async def _send_gift_result_image(
    bot: Bot,
    role: RoleCandidate,
    image: str,
    text: str,
    user_id: str,
    is_group: bool,
    kind: str,
) -> None:
    await _send_daily_result_image(bot, role, image, text, user_id, is_group, kind)


def _get_pending_gift(ev: Event, target_user_id: str, kind: str = 'wife') -> dict[str, Any] | None:
    key = _gift_pending_key(ev, target_user_id, kind)
    pending = _GIFT_PENDING.get(key)
    if not isinstance(pending, dict):
        return None
    try:
        created_at = float(pending.get('created_at') or 0)
    except (TypeError, ValueError):
        created_at = 0
    if time.time() - created_at > GIFT_CONFIRM_TIMEOUT_SECONDS:
        _GIFT_PENDING.pop(key, None)
        return None
    return pending


def _set_pending_gift(ev: Event, target_user_id: str, giver_id: str, kind: str = 'wife') -> None:
    _GIFT_PENDING[_gift_pending_key(ev, target_user_id, kind)] = {
        'giver_id': giver_id,
        'kind': kind,
        'created_at': time.time(),
    }


def _clear_pending_gift(ev: Event, target_user_id: str, kind: str = 'wife') -> None:
    _GIFT_PENDING.pop(_gift_pending_key(ev, target_user_id, kind), None)


def clear_pending_gifts_for_user(ev: Event, user_id: str) -> None:
    """取消当前会话中该用户作为赠送方或接收方的全部待确认请求。"""
    context_prefix = f'{_context_key(ev)}:'
    for key, pending in tuple(_GIFT_PENDING.items()):
        if not key.startswith(context_prefix):
            continue
        is_recipient = key.rsplit(':', 1)[-1] == user_id
        is_giver = isinstance(pending, dict) and str(pending.get('giver_id') or '') == user_id
        if is_recipient or is_giver:
            _GIFT_PENDING.pop(key, None)


async def _send_gift_daily(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} 发起送{title}')
    if kind == 'husband' and not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message(), kind=kind)
    if not _gift_enabled(kind):
        return await _send_prefixed(bot, f'送{title}功能当前已关闭。', kind=kind)

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await _send_prefixed(bot, '要送给谁？请在命令中艾特对方。', kind=kind)

    giver_id = _user_key(ev)
    if target_user_id == giver_id:
        return await _send_prefixed(bot, f'不能把{title}送给自己哦！', kind=kind)

    giver_record = _get_existing_daily_record(ev, giver_id, kind)
    if giver_record is None:
        return await _send_prefixed(bot, f'你今天还没有{title}，先去抽一个吧~', kind=kind)

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    bucket = _daily_bucket_name(kind)
    giver_data = context[bucket].get(giver_id)

    state = _wife_state(giver_data)
    if state == 'lost_stolen':
        return await _send_prefixed(bot, f'你的{title}已经被抢走了，没有{title}可以送了~', kind=kind)
    if state == 'lost_gifted':
        return await _send_prefixed(bot, f'你今天已经把{title}送出去了~', kind=kind)
    if state == 'divorced':
        return await _send_prefixed(bot, f'你今天已经和{title}离婚了，没有{title}可以送了~', kind=kind)
    if _is_secondhand_wife(giver_data):
        return await _send_prefixed(bot, f'这个{title}是抢来或别人送的，不能再送出去哦~', kind=kind)

    target_key = _user_key(ev, target_user_id)
    if _has_active_wife(context[bucket].get(target_key)):
        return await _send_prefixed(bot, f'对方今天已经有{title}了，不需要你送哦~', kind=kind)

    if _get_pending_gift(ev, target_user_id, kind) is not None:
        return await _send_prefixed(
            bot,
            f'对方已经有一个待确认的送{title}请求，请等待处理或超时后再试~',
            kind=kind,
        )

    _set_pending_gift(ev, target_user_id, giver_id, kind)
    giver_name = _user_display_name(ev, giver_id)
    role = giver_record.to_role()
    item_text = title if kind == 'loli' else f'{title}{role.name}'
    text = (
        f'{giver_name} 想把今天的{item_text}送给你！\n'
        f'请在 {GIFT_CONFIRM_TIMEOUT_SECONDS} 秒内发送「接受{title}赠送」，'
        f'或发送「拒绝{title}赠送」，超时自动取消。'
    )
    await _send_prefixed(bot, [MessageSegment.at(target_user_id), '\n', text], kind=kind)


async def _accept_gift_daily(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    target_user_id = _user_key(ev)
    pending = _get_pending_gift(ev, target_user_id, kind)
    if pending is None:
        return await _send_prefixed(
            bot,
            f'没有待确认的送{title}请求，可能已经超时或被取消了~',
            kind=kind,
        )

    giver_id = str(pending['giver_id'])
    _clear_pending_gift(ev, target_user_id, kind)

    if kind == 'husband' and not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message(), kind=kind)
    if not _gift_enabled(kind):
        return await _send_prefixed(bot, f'送{title}功能已关闭，这次赠送已失效。', kind=kind)

    giver_record = _get_existing_daily_record(ev, giver_id, kind)
    if giver_record is None:
        return await _send_prefixed(
            bot,
            f'对方现在已经没有{title}可以送给你了，赠送已失效~',
            kind=kind,
        )

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    bucket = _daily_bucket_name(kind)
    giver_data = context[bucket].get(giver_id)

    state = _wife_state(giver_data)
    if state == 'lost_stolen':
        return await _send_prefixed(bot, f'对方的{title}已经被抢走了，赠送已失效~', kind=kind)
    if state == 'lost_gifted':
        return await _send_prefixed(bot, f'对方已经把{title}送给别人了，赠送已失效~', kind=kind)
    if state == 'divorced':
        return await _send_prefixed(bot, f'对方已经和{title}离婚了，赠送已失效~', kind=kind)
    if _is_secondhand_wife(giver_data):
        return await _send_prefixed(
            bot,
            f'这个{title}是抢来或别人送的，不能再送出去，赠送已失效~',
            kind=kind,
        )

    if _has_active_wife(context[bucket].get(target_user_id)):
        return await _send_prefixed(bot, f'你现在已经有{title}了，不需要接受赠送啦~', kind=kind)

    context[bucket][target_user_id] = _record_to_dict(giver_record, ev, target_user_id)
    context[bucket][target_user_id]['gifted_from'] = giver_id

    if isinstance(context[bucket].get(giver_id), dict):
        context[bucket][giver_id]['gifted_to'] = target_user_id
        context[bucket][giver_id]['gifted_to_name'] = _user_display_name(ev, target_user_id)

    _save_wife_data(data)

    role = giver_record.to_role()
    await _send_gift_result_image(
        bot,
        role,
        giver_record.image,
        _build_gift_success_text(role, target_user_id, kind),
        giver_id,
        ev.group_id is not None,
        kind,
    )


async def _reject_gift_daily(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    target_user_id = _user_key(ev)
    if _get_pending_gift(ev, target_user_id, kind) is None:
        return await _send_prefixed(bot, f'没有待确认的送{title}请求。', kind=kind)
    _clear_pending_gift(ev, target_user_id, kind)
    await _send_prefixed(bot, f'已拒绝对方的送{title}请求。', kind=kind)


async def _send_gift_wife(bot: Bot, ev: Event) -> None:
    await _send_gift_daily(bot, ev, 'wife')


async def _accept_gift_wife(bot: Bot, ev: Event) -> None:
    await _accept_gift_daily(bot, ev, 'wife')


async def _reject_gift_wife(bot: Bot, ev: Event) -> None:
    await _reject_gift_daily(bot, ev, 'wife')


async def _send_gift_husband(bot: Bot, ev: Event) -> None:
    await _send_gift_daily(bot, ev, 'husband')


async def _accept_gift_husband(bot: Bot, ev: Event) -> None:
    await _accept_gift_daily(bot, ev, 'husband')


async def _reject_gift_husband(bot: Bot, ev: Event) -> None:
    await _reject_gift_daily(bot, ev, 'husband')


async def _send_gift_loli(bot: Bot, ev: Event) -> None:
    await _send_gift_daily(bot, ev, 'loli')


async def _accept_gift_loli(bot: Bot, ev: Event) -> None:
    await _accept_gift_daily(bot, ev, 'loli')


async def _reject_gift_loli(bot: Bot, ev: Event) -> None:
    await _reject_gift_daily(bot, ev, 'loli')


@gift_sv.on_prefix(
    ('送老婆', '送今日老婆'),
    block=True,
    to_ai="""把当前用户今天的老婆送给指定用户。
    当用户说“把我的老婆送给某人”“送老婆 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
)
async def gift_wife(bot: Bot, ev: Event):
    await _send_gift_wife(bot, ev)


@gift_sv.on_fullmatch(
    ('送老婆', '送今日老婆'),
    block=True,
    to_ai="""显示送老婆的用法。
    当用户只说“送老婆”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_wife_at(bot: Bot, ev: Event):
    await _send_gift_wife(bot, ev)


@gift_sv.on_fullmatch(
    ('接受老婆赠送', '同意送老婆'),
    block=True,
    to_ai="""同意接收别人赠送的今日老婆。
    当用户说“接受老婆赠送”“同意送老婆”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_wife_accept(bot: Bot, ev: Event):
    await _accept_gift_wife(bot, ev)


@gift_sv.on_fullmatch(
    ('拒绝老婆赠送', '拒绝送老婆'),
    block=True,
    to_ai="""拒绝接收别人赠送的今日老婆。
    当用户说“拒绝老婆赠送”“拒绝送老婆”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_wife_reject(bot: Bot, ev: Event):
    await _reject_gift_wife(bot, ev)


@gift_sv.on_prefix(
    ('送老公', '送今日老公'),
    block=True,
    to_ai="""把当前用户今天的老公送给指定用户。
    当用户说“把我的老公送给某人”“送老公 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
)
async def gift_husband(bot: Bot, ev: Event):
    await _send_gift_husband(bot, ev)


@gift_sv.on_fullmatch(
    ('送老公', '送今日老公'),
    block=True,
    to_ai="""显示送老公的用法。
    当用户只说“送老公”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_husband_at(bot: Bot, ev: Event):
    await _send_gift_husband(bot, ev)


@gift_sv.on_fullmatch(
    ('接受老公赠送', '同意送老公'),
    block=True,
    to_ai="""同意接收别人赠送的今日老公。
    当用户说“接受老公赠送”“同意送老公”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_husband_accept(bot: Bot, ev: Event):
    await _accept_gift_husband(bot, ev)


@gift_sv.on_fullmatch(
    ('拒绝老公赠送', '拒绝送老公'),
    block=True,
    to_ai="""拒绝接收别人赠送的今日老公。
    当用户说“拒绝老公赠送”“拒绝送老公”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_husband_reject(bot: Bot, ev: Event):
    await _reject_gift_husband(bot, ev)


@gift_sv.on_prefix(
    ('送萝莉', '送今日萝莉'),
    block=True,
    to_ai="""把当前用户今天的萝莉送给指定用户。
    当用户说“把我的萝莉送给某人”“送萝莉 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
)
async def gift_loli(bot: Bot, ev: Event):
    await _send_gift_loli(bot, ev)


@gift_sv.on_fullmatch(
    ('送萝莉', '送今日萝莉'),
    block=True,
    to_ai="""显示送萝莉的用法。
    当用户只说“送萝莉”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_loli_at(bot: Bot, ev: Event):
    await _send_gift_loli(bot, ev)


@gift_sv.on_fullmatch(
    ('接受萝莉赠送', '同意送萝莉'),
    block=True,
    to_ai="""同意接收别人赠送的今日萝莉。
    当用户说“接受萝莉赠送”“同意送萝莉”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_loli_accept(bot: Bot, ev: Event):
    await _accept_gift_loli(bot, ev)


@gift_sv.on_fullmatch(
    ('拒绝萝莉赠送', '拒绝送萝莉'),
    block=True,
    to_ai="""拒绝接收别人赠送的今日萝莉。
    当用户说“拒绝萝莉赠送”“拒绝送萝莉”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def gift_loli_reject(bot: Bot, ev: Event):
    await _reject_gift_loli(bot, ev)
