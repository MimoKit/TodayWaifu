"""TodayWaifu - gift module."""
from __future__ import annotations

from .shared import *  # noqa: F403

GIFT_CONFIRM_TIMEOUT_SECONDS = 60
_GIFT_PENDING: dict[str, dict[str, Any]] = {}


def _build_gift_success_text(role: RoleCandidate, target_user_id: str) -> str:
    return str(_cfg('DailyWifeGiftSuccessTemplate') or '你把今天的老婆{name}送给了对方！').format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )


def _gift_pending_key(ev: Event, target_user_id: str) -> str:
    return f'{_context_key(ev)}:{target_user_id}'


def _get_pending_gift(ev: Event, target_user_id: str) -> dict[str, Any] | None:
    key = _gift_pending_key(ev, target_user_id)
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


def _set_pending_gift(ev: Event, target_user_id: str, giver_id: str) -> None:
    _GIFT_PENDING[_gift_pending_key(ev, target_user_id)] = {
        'giver_id': giver_id,
        'created_at': time.time(),
    }


def _clear_pending_gift(ev: Event, target_user_id: str) -> None:
    _GIFT_PENDING.pop(_gift_pending_key(ev, target_user_id), None)


async def _send_gift_wife(bot: Bot, ev: Event):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id} 发起了送老婆操作')
    if not _cfg_bool('DailyWifeGiftEnabled', True):
        return await _send_prefixed(bot, '送老婆功能当前已关闭。')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await _send_prefixed(bot, '要送给谁？请艾特对方或在命令后面写对方 QQ。')

    giver_id = _user_key(ev)
    if target_user_id == giver_id:
        return await _send_prefixed(bot, '不能把老婆送给自己哦！')

    giver_record = _get_existing_daily_wife_record(ev, giver_id)
    if giver_record is None:
        return await _send_prefixed(bot, '你今天还没有老婆，先去抽一个吧~')
    if giver_record.record_type == 'member':
        return await _send_prefixed(bot, '群友老婆不能送出去哦~')

    data = _load_wife_data()
    context = _get_today_context(data, ev)

    giver_data = context['wives'].get(giver_id)
    # 离手即结算：自己的老婆已被抢走 / 已送出，没有老婆可送
    giver_state = _wife_state(giver_data)
    if giver_state == 'lost_stolen':
        return await _send_prefixed(bot, '你的老婆已经被抢走了，没有老婆可以送了~')
    if giver_state == 'lost_gifted':
        return await _send_prefixed(bot, '你今天已经把老婆送出去了~')
    # 到手即终结：抢来的 / 别人送的老婆不能再送出去
    if _is_secondhand_wife(giver_data):
        return await _send_prefixed(bot, '这个老婆是抢来或别人送的，不能再送出去哦~')

    # 对方仍持有有效老婆时不需要再送（被抢走/送出后变“空”才可接收）
    target_existing = context['wives'].get(target_user_id)
    if _has_active_wife(target_existing):
        return await _send_prefixed(bot, '对方今天已经有老婆了，不需要你送哦~')
    # 对方已有补偿老婆，不能再送（safe_wife 不能被送）
    if isinstance(context.get('safe_wives', {}).get(target_user_id), dict):
        return await _send_prefixed(bot, '对方已经有补偿老婆了，不需要你送哦~')

    if _get_pending_gift(ev, target_user_id) is not None:
        return await _send_prefixed(bot, '对方已经有一个待确认的送老婆请求，请等待处理或超时后再试~')

    _set_pending_gift(ev, target_user_id, giver_id)
    logger.info(f'{LOG_PREFIX} 用户 {giver_id} 向 {target_user_id} 发起送老婆请求，等待对方确认')

    giver_name = _user_display_name(ev, giver_id)
    role = giver_record.to_role()
    text = (
        f'{giver_name} 想把今天的老婆{role.name}送给你！\n'
        f'请在 {GIFT_CONFIRM_TIMEOUT_SECONDS} 秒内发送「同意送老婆」接受，'
        f'或发送「拒绝送老婆」拒绝，超时将自动取消。'
    )
    await _send_prefixed(bot, [MessageSegment.at(target_user_id), '\n', text])


async def _accept_gift_wife(bot: Bot, ev: Event):
    target_user_id = _user_key(ev)
    pending = _get_pending_gift(ev, target_user_id)
    if pending is None:
        return await _send_prefixed(bot, '没有待确认的送老婆请求，可能已经超时或被取消了~')

    giver_id = pending['giver_id']
    _clear_pending_gift(ev, target_user_id)

    if not _cfg_bool('DailyWifeGiftEnabled', True):
        return await _send_prefixed(bot, '送老婆功能已关闭，这次赠送已失效。')

    giver_record = _get_existing_daily_wife_record(ev, giver_id)
    if giver_record is None or giver_record.record_type == 'member':
        return await _send_prefixed(bot, '对方现在已经没有老婆可以送给你了，赠送已失效~')

    data = _load_wife_data()
    context = _get_today_context(data, ev)

    giver_data = context['wives'].get(giver_id)
    giver_state = _wife_state(giver_data)
    if giver_state == 'lost_stolen':
        return await _send_prefixed(bot, '对方的老婆已经被抢走了，赠送已失效~')
    if giver_state == 'lost_gifted':
        return await _send_prefixed(bot, '对方已经把老婆送给别人了，赠送已失效~')
    if _is_secondhand_wife(giver_data):
        return await _send_prefixed(bot, '这个老婆是抢来或别人送的，不能再送出去，赠送已失效~')

    target_existing = context['wives'].get(target_user_id)
    if _has_active_wife(target_existing):
        return await _send_prefixed(bot, '你现在已经有老婆了，不需要接受赠送啦~')
    if isinstance(context.get('safe_wives', {}).get(target_user_id), dict):
        return await _send_prefixed(bot, '你已经有补偿老婆了，不需要接受赠送啦~')

    logger.info(f'{LOG_PREFIX} 用户 {giver_id} 把老婆送给了 {target_user_id}（对方已同意）')
    context['wives'][target_user_id] = _record_to_dict(giver_record, ev, target_user_id)
    context['wives'][target_user_id]['gifted_from'] = giver_id

    if isinstance(context['wives'].get(giver_id), dict):
        context['wives'][giver_id]['gifted_to'] = target_user_id
        context['wives'][giver_id]['gifted_to_name'] = _user_display_name(ev, target_user_id)

    _save_wife_data(data)

    role = giver_record.to_role()
    text = _build_gift_success_text(role, target_user_id)
    await _send_role_image(bot, role, giver_record.image, text, giver_id)


async def _reject_gift_wife(bot: Bot, ev: Event):
    target_user_id = _user_key(ev)
    if _get_pending_gift(ev, target_user_id) is None:
        return await _send_prefixed(bot, '没有待确认的送老婆请求。')
    _clear_pending_gift(ev, target_user_id)
    await _send_prefixed(bot, '已拒绝对方的送老婆请求。')


@sv.on_prefix(('送老婆', '送今日老婆'), block=True)
async def gift_wife(bot: Bot, ev: Event):
    await _send_gift_wife(bot, ev)


@sv.on_fullmatch(('送老婆', '送今日老婆'), block=True)
async def gift_wife_at(bot: Bot, ev: Event):
    await _send_gift_wife(bot, ev)


@sv.on_fullmatch('同意送老婆', block=True)
async def gift_wife_accept(bot: Bot, ev: Event):
    await _accept_gift_wife(bot, ev)


@sv.on_fullmatch('拒绝送老婆', block=True)
async def gift_wife_reject(bot: Bot, ev: Event):
    await _reject_gift_wife(bot, ev)


