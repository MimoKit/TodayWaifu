"""TodayWaifu rob command."""
from __future__ import annotations

import random

from .shared import (
    Bot,
    Event,
    LOG_PREFIX,
    MessageSegment,
    Path,
    RoleCandidate,
    _cfg,
    _cfg_bool,
    _cfg_probability,
    _daily_bucket_name,
    _daily_item_title,
    _get_event_target_user_id,
    _get_existing_daily_record,
    _get_today_context,
    _has_active_wife,
    _husband_available,
    _husband_unavailable_message,
    _is_master,
    _is_secondhand_wife,
    _load_wife_data,
    _record_to_dict,
    _save_wife_data,
    _send_prefixed,
    _send_role_image,
    _user_display_name,
    _user_key,
    _wife_state,
    _with_loli_reply_prefix,
    logger,
    rob_sv,
)


def _rob_enabled(kind: str) -> bool:
    if kind == 'husband':
        return _cfg_bool('DailyHusbandRobEnabled', True)
    if kind == 'loli':
        return _cfg_bool('DailyLoliRobEnabled', True)
    return _cfg_bool('DailyWifeRobEnabled', True)


def _rob_success_rate(kind: str) -> float:
    if kind == 'loli':
        return _cfg_probability('DailyLoliRobSuccessRate', 0.5)
    return _cfg_probability('DailyWifeRobSuccessRate', 0.5)


def _rob_success_template(kind: str) -> str:
    if kind == 'husband':
        return str(_cfg('DailyHusbandRobSuccessTemplate') or '抢老公成功！你把对方今天的老公{name}抢过来了！')
    if kind == 'loli':
        return str(_cfg('DailyLoliRobSuccessTemplate') or '抢萝莉成功！你把对方今天的萝莉抢过来了！')
    return str(_cfg('DailyWifeRobSuccessTemplate') or '抢老婆成功！你把对方今天的老婆{name}抢过来了！')


def _build_rob_success_text(role: RoleCandidate, target_user_id: str, kind: str) -> str:
    template = _rob_success_template(kind)
    return template.format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )


def _rob_attempt_key(kind: str, user_id: str) -> str:
    return user_id if kind == 'wife' else f'{kind}:{user_id}'


async def _send_rob_result_image(
    bot: Bot,
    role: RoleCandidate,
    image: str,
    text: str,
    user_id: str,
    is_group: bool,
    kind: str,
) -> None:
    if kind != 'loli':
        await _send_role_image(bot, role, image, text, user_id, is_group)
        return

    messages: list[object] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    messages.append(_with_loli_reply_prefix(text))
    image_ref = image if image.startswith(('http://', 'https://')) else Path(image)
    messages.append(MessageSegment.image(image_ref))
    await bot.send(messages)


async def _send_rob_daily(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} 发起抢{title}')
    if kind == 'husband' and not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message())
    if not _rob_enabled(kind):
        return await _send_prefixed(bot, f'抢{title}功能当前已关闭。')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await _send_prefixed(bot, f'要抢谁的{title}？请艾特对方或在命令后面写对方 QQ。')

    robber_id = _user_key(ev)
    if target_user_id == robber_id:
        return await _send_prefixed(bot, f'自己抢自己的{title}也太奇怪了吧！')

    target_record = _get_existing_daily_record(ev, target_user_id, kind)
    if target_record is None:
        return await _send_prefixed(bot, f'对方今天还没有{title}呢~')

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    bucket = _daily_bucket_name(kind)
    target_key = _user_key(ev, target_user_id)
    target_data = context[bucket].get(target_key)

    if _wife_state(target_data) != 'owned':
        return await _send_prefixed(bot, f'对方的{title}已经不在身边了，抢不到了哦~')
    if _is_secondhand_wife(target_data):
        return await _send_prefixed(bot, f'对方这个{title}是抢来或别人送的，抢不动哦~')
    if not _has_active_wife(target_data):
        return await _send_prefixed(bot, f'对方今天还没有{title}呢~')

    attempts = context.setdefault('rob_attempts', {})
    is_master = _is_master(ev)
    attempt_key = _rob_attempt_key(kind, robber_id)
    if not is_master and (attempts.get(attempt_key) or (kind == 'wife' and attempts.get(robber_id))):
        logger.info(f'{LOG_PREFIX} 用户 {robber_id} 今天抢{title}次数已用尽')
        return await _send_prefixed(bot, f'今天已经抢过{title}啦，明天再来吧！')

    if not is_master:
        attempts[attempt_key] = True

    if random.random() >= _rob_success_rate(kind):
        logger.info(f'{LOG_PREFIX} 用户 {robber_id} 抢 {target_user_id} 的{title}失败')
        _save_wife_data(data)
        return await _send_prefixed(bot, f'恭喜你,抢{title}失败了！')

    logger.info(f'{LOG_PREFIX} 用户 {robber_id} 成功抢走 {target_user_id} 的{title}')
    context[bucket][robber_id] = _record_to_dict(target_record, ev, robber_id)
    context[bucket][robber_id]['stolen_from'] = target_user_id

    if isinstance(context[bucket].get(target_key), dict):
        context[bucket][target_key]['stolen_by'] = robber_id
        context[bucket][target_key]['stolen_by_name'] = _user_display_name(ev, robber_id)

    _save_wife_data(data)

    role = target_record.to_role()
    await _send_rob_result_image(
        bot,
        role,
        target_record.image,
        _build_rob_success_text(role, target_user_id, kind),
        robber_id,
        ev.group_id is not None,
        kind,
    )


async def _send_rob_wife(bot: Bot, ev: Event) -> None:
    await _send_rob_daily(bot, ev, 'wife')


async def _send_rob_husband(bot: Bot, ev: Event) -> None:
    await _send_rob_daily(bot, ev, 'husband')


async def _send_rob_loli(bot: Bot, ev: Event) -> None:
    await _send_rob_daily(bot, ev, 'loli')


@rob_sv.on_prefix(('抢老婆', '抢今日老婆', '抢婆娘'), block=True)
async def rob_wife(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)


@rob_sv.on_fullmatch(('抢老婆', '抢今日老婆', '抢婆娘'), block=True)
async def rob_wife_at(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)


@rob_sv.on_prefix(('抢老公', '抢今日老公'), block=True)
async def rob_husband(bot: Bot, ev: Event):
    await _send_rob_husband(bot, ev)


@rob_sv.on_fullmatch(('抢老公', '抢今日老公'), block=True)
async def rob_husband_at(bot: Bot, ev: Event):
    await _send_rob_husband(bot, ev)


@rob_sv.on_prefix(('抢萝莉', '抢今日萝莉'), block=True)
async def rob_loli(bot: Bot, ev: Event):
    await _send_rob_loli(bot, ev)


@rob_sv.on_fullmatch(('抢萝莉', '抢今日萝莉'), block=True)
async def rob_loli_at(bot: Bot, ev: Event):
    await _send_rob_loli(bot, ev)
