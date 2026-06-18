"""TodayWaifu - rob module."""
from __future__ import annotations

from .shared import *  # noqa: F403


def _rob_success_rate() -> float:
    try:
        value = float(_cfg('DailyWifeRobSuccessRate'))
    except (TypeError, ValueError):
        value = 0.5
    return max(0.0, min(1.0, value))


def _build_rob_success_text(role: RoleCandidate, target_user_id: str) -> str:
    return str(_cfg('DailyWifeRobSuccessTemplate') or '抢老婆成功！你把对方今天的老婆{name}抢过来了！').format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )



async def _send_rob_wife(bot: Bot, ev: Event):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id} 发起了抢老婆操作')
    if not _cfg_bool('DailyWifeRobEnabled', True):
        return await _send_prefixed(bot,'抢老婆功能当前已关闭。')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await _send_prefixed(bot,'要抢谁的老婆？请艾特对方或在命令后面写对方 QQ。')

    robber_id = _user_key(ev)
    if target_user_id == robber_id:
        return await _send_prefixed(bot,'自己抢自己的老婆也太奇怪了吧！')

    target_record = _get_existing_daily_wife_record(ev, target_user_id)
    if target_record is None:
        return await _send_prefixed(bot,'对方今天还没有老婆呢~')
    if target_record.record_type == 'member':
        return await _send_prefixed(bot,'对方今天娶到的是群友，不能被抢走哦~')

    data = _load_wife_data()
    context = _get_today_context(data, ev)

    target_data = context['wives'].get(_user_key(ev, target_user_id))
    # 离手即结算：对方老婆已被抢走 / 已送出，不能再抢（堵死复制 BUG 与链式抢）
    if _wife_state(target_data) != 'owned':
        # 对方有补偿老婆，给更明确提示
        if isinstance(context.get('safe_wives', {}).get(_user_key(ev, target_user_id)), dict):
            return await _send_prefixed(bot, '对方这个是补偿老婆，抢不动哦~')
        return await _send_prefixed(bot, '对方的老婆已经不在身边了，抢不到了哦~')
    # 到手即终结：抢来的 / 别人送的老婆不能再被抢走
    if _is_secondhand_wife(target_data):
        return await _send_prefixed(bot, '对方这个老婆是抢来或别人送的，抢不动哦~')

    attempts = context.setdefault('rob_attempts', {})
    is_master = _is_master(ev)
    
    if not is_master and attempts.get(robber_id):
        logger.info(f'{LOG_PREFIX} 用户 {robber_id} 今天抢老婆次数已用尽')
        return await _send_prefixed(bot,'今天已经抢过老婆啦，明天再来吧！')

    if not is_master:
        attempts[robber_id] = True

    if random.random() >= _rob_success_rate():
        logger.info(f'{LOG_PREFIX} 用户 {robber_id} 抢 {target_user_id} 的老婆失败')
        _save_wife_data(data)
        return await _send_prefixed(bot,'抢老婆失败了，还被对方痛扁了一顿！🤣')

    logger.info(f'{LOG_PREFIX} 用户 {robber_id} 成功抢走了 {target_user_id} 的老婆')
    context['wives'][robber_id] = _record_to_dict(target_record, ev, robber_id)
    context['wives'][robber_id]['stolen_from'] = target_user_id

    robber_name = _user_display_name(ev, robber_id)
    if isinstance(context['wives'].get(target_user_id), dict):
        context['wives'][target_user_id]['stolen_by'] = robber_id
        context['wives'][target_user_id]['stolen_by_name'] = robber_name

    _save_wife_data(data)

    role = target_record.to_role()
    text = _build_rob_success_text(role, target_user_id)
    await _send_role_image(bot, role, target_record.image, text, robber_id)



@sv.on_prefix(('抢老婆', '抢今日老婆', '抢婆娘'), block=True)
async def rob_wife(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)


@sv.on_fullmatch(('抢老婆', '抢今日老婆', '抢婆娘'), block=True)
async def rob_wife_at(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)


