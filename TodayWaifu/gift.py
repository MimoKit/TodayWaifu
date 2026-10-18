"""TodayWaifu gift command.

赠送需要对方确认，因此「发起」与「接受」之间存在最长 60 秒的窗口。这段时间内赠送方
的对象可能被抢、被转送或已离婚，所以发起时的校验结论不能作为写入依据——接受方必须
在持锁状态下重新校验赠送方与接受方的状态，再决定是否落盘。本模块按该原则划分：
发起阶段只做即时反馈与登记待确认项，真正的状态判定与写入全部集中在 ``_accept_gift_daily``
的临界区内。
"""

from __future__ import annotations

import time

from .shared import (
    LOG_PREFIX,
    Bot,
    Event,
    WifeRecord,
    RoleCandidate,
    MessageSegment,
    _cfg,
    logger,
    gift_sv,
    _cfg_bool,
    _user_key,
    _safe_send,
    _wife_state,
    _context_key,
    _record_to_dict,
    name_from_event,
    _has_active_wife,
    _daily_item_title,
    _record_from_dict,
    _daily_bucket_name,
    _husband_available,
    _daily_context_lock,
    _is_secondhand_wife,
    _load_daily_context,
    _save_daily_records,
    _daily_kind_metadata,
    _send_daily_result_image,
    _get_event_target_user_id,
    _get_existing_daily_record,
)
from .payloads import PendingGift

# 确认窗口与容量上限共同界定待确认表的行为：超时未确认自动作废，容量满时淘汰最早登记项。
# 时限不设得更长是因为窗口越长，接受时状态已改变（被抢/被送/已离婚）的概率越高，
# 用户越容易在点确认时收到「赠送已失效」。
GIFT_CONFIRM_TIMEOUT_SECONDS = 60
# 容量按会话数估算：每个会话同时最多存在少量待确认项，4096 足以覆盖，同时防止
# 异常刷屏（反复 @ 不同用户）使内存无界增长。淘汰最早项可能让个别用户的请求提前失效，
# 但优于内存持续膨胀。
GIFT_PENDING_MAX_ENTRIES = 4096
# 待确认项仅存于内存：进程重启后全部失效，用户重新发起即可，无需持久化。
_GIFT_PENDING: dict[str, PendingGift] = {}


def _gift_enabled(kind: str) -> bool:
    # 默认开启，与开关项的语义保持一致：仅在配置显式关闭时才拦截。
    return _cfg_bool(_daily_kind_metadata(kind).gift_enabled_key, True)


def _gift_success_template(kind: str) -> str:
    # 配置为空串或 None 时回落到内置默认文案：用户清空配置项不应导致成功提示为空。
    metadata = _daily_kind_metadata(kind)
    return str(_cfg(metadata.gift_success_key) or metadata.gift_success_default)


def _build_gift_success_text(role: RoleCandidate, target_user_id: str, kind: str) -> str:
    # 模板占位符由配置决定，此处一次性补齐全部可用字段；多传的键不会影响 format，
    # 从而让用户自定义模板时无需关心本函数提供了哪些字段。
    template = _gift_success_template(kind)
    return template.format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )


def _gift_pending_key(ev: Event, target_user_id: str, kind: str = 'wife') -> str:
    # 键包含会话、类型与目标用户三者：同一会话内不同类型的赠送、以及针对不同对象的
    # 赠送是彼此独立的待确认项，必须能同时存在而不互相覆盖。
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
    # 复用每日结果的图片渲染路径，使赠送成功图与抽卡结果图样式一致。
    await _send_daily_result_image(bot, role, image, text, user_id, is_group, kind)


def _get_pending_gift(ev: Event, target_user_id: str, kind: str = 'wife') -> PendingGift | None:
    key = _gift_pending_key(ev, target_user_id, kind)
    pending = _GIFT_PENDING.get(key)
    # 非字典值说明该键被外部写入了非预期结构（如测试夹具），按不存在处理而非抛出。
    if not isinstance(pending, dict):
        return None
    try:
        created_at = float(pending.get('created_at') or 0)
    except (TypeError, ValueError):
        created_at = 0
    # 超时项在读取路径上就地移除，而非等待定时清理：由读取方承担惰性回收，
    # 可确保过期请求在任何时刻都无法被接受，即便清理协程尚未运行。
    if time.time() - created_at > GIFT_CONFIRM_TIMEOUT_SECONDS:
        _GIFT_PENDING.pop(key, None)
        return None
    return pending


def _set_pending_gift(ev: Event, target_user_id: str, giver_id: str, kind: str = 'wife') -> None:
    # 写入前先清理过期项：容量统计基于当前表长，不清理会把已失效的条目算进去，
    # 导致仍在有效期内的请求被提前淘汰。
    clear_expired_pending_gifts()
    # 达到上限时淘汰 created_at 最小者。这里按值扫描而非依赖插入顺序，
    # 因为过期清理与覆盖写入都会打乱 FIFO 语义，按时间取最旧才符合预期。
    if len(_GIFT_PENDING) >= GIFT_PENDING_MAX_ENTRIES:
        oldest_key = min(
            _GIFT_PENDING,
            key=lambda key: float(_GIFT_PENDING[key].get('created_at') or 0),
        )
        _GIFT_PENDING.pop(oldest_key, None)
    _GIFT_PENDING[_gift_pending_key(ev, target_user_id, kind)] = {
        'giver_id': giver_id,
        'kind': kind,
        'created_at': time.time(),
    }


def _clear_pending_gift(ev: Event, target_user_id: str, kind: str = 'wife') -> None:
    _GIFT_PENDING.pop(_gift_pending_key(ev, target_user_id, kind), None)


def clear_expired_pending_gifts() -> None:
    # 遍历前先固化键集合：遍历过程中会删除元素，直接迭代原字典会触发运行时错误。
    now = time.time()
    for key, pending in tuple(_GIFT_PENDING.items()):
        if not isinstance(pending, dict):
            _GIFT_PENDING.pop(key, None)
            continue
        try:
            created_at = float(pending.get('created_at') or 0)
        except (TypeError, ValueError):
            created_at = 0
        if now - created_at > GIFT_CONFIRM_TIMEOUT_SECONDS:
            _GIFT_PENDING.pop(key, None)


def clear_pending_gifts_for_user(ev: Event, user_id: str) -> None:
    """取消当前会话中该用户作为赠送方或接收方的全部待确认请求。

    用户退出关系、被抢或执行其它会使赠送前提失效的操作时调用：待确认项在前置条件
    已不成立后仍被接受，会造成「记录被抢走却又被送出」的重复状态，因此必须主动撤销。

    只清理当前会话：待确认键以会话为前缀，跨会话的赠送本就互不可见。
    """
    context_prefix = f'{_context_key(ev)}:'
    for key, pending in tuple(_GIFT_PENDING.items()):
        if not key.startswith(context_prefix):
            continue
        # 键的最后一段是目标用户，据此判断接收方身份；赠送方则存在值里。
        is_recipient = key.rsplit(':', 1)[-1] == user_id
        is_giver = isinstance(pending, dict) and str(pending.get('giver_id') or '') == user_id
        if is_recipient or is_giver:
            _GIFT_PENDING.pop(key, None)


async def _send_gift_daily(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    # 功能状态在入口处先行判定：后续步骤会读取上下文并可能登记待确认项，
    # 未开启的能力不应产生任何副作用。
    if kind == 'husband' and not _husband_available():
        return
    if not _gift_enabled(kind):
        return
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} 发起送{title}')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await _safe_send(
            bot,
            '要送给谁？请艾特对方，或在命令后面写对方 QQ。\nQQ 官方机器人等没有 QQ 号的平台，可直接粘贴对方的用户 ID。',
        )

    giver_id = _user_key(ev)
    # 自送必须先于状态检查拦截：否则会登记一个永远无法被接受的待确认项，
    # 并占用该用户在这 60 秒内的赠送名额。
    if target_user_id == giver_id:
        return await _safe_send(bot, f'不能把{title}送给自己哦！')

    giver_record = await _get_existing_daily_record(ev, giver_id, kind)
    if giver_record is None:
        return await _safe_send(bot, f'你今天还没有{title}，先去抽一个吧~')

    # 以下校验均为即时反馈，仅用于尽早告知用户不可赠送；写入前会在接受阶段重新校验，
    # 因此这里读到的状态即便随后失效也不会产生错误的落盘结果。
    context = await _load_daily_context(ev)
    bucket = _daily_bucket_name(kind)
    giver_data = context[bucket].get(giver_id)

    state = _wife_state(giver_data)
    if state == 'lost_stolen':
        return await _safe_send(bot, f'你的{title}已经被抢走了，没有{title}可以送了~')
    if state == 'lost_gifted':
        return await _safe_send(bot, f'你今天已经把{title}送出去了~')
    if state == 'divorced':
        return await _safe_send(bot, f'你今天已经和{title}离婚了，没有{title}可以送了~')
    # 抢来或别人送的记录不再允许转送，防止同一对象在用户之间被无限接力。
    if _is_secondhand_wife(giver_data):
        return await _safe_send(bot, f'这个{title}是抢来或别人送的，不能再送出去哦~')

    target_key = _user_key(ev, target_user_id)
    # 对方已有对象时直接拒绝：接受阶段还会再判一次，此处拦截可避免无谓的等待确认。
    if _has_active_wife(context[bucket].get(target_key)):
        return await _safe_send(bot, f'对方今天已经有{title}了，不需要你送哦~')

    # 同一目标只允许存在一个待确认请求：并发登记会让先到的请求被后到的覆盖，
    # 用户点确认时依据的可能是较晚那次的内容。
    if _get_pending_gift(ev, target_user_id, kind) is not None:
        return await _safe_send(
            bot,
            f'对方已经有一个待确认的送{title}请求，请等待处理或超时后再试~',
        )

    _set_pending_gift(ev, target_user_id, giver_id, kind)
    giver_name = name_from_event(ev, giver_id)
    role = giver_record.to_role()
    # 实际展示的赠送对象文案按类型区分：萝莉不暴露角色名，正太统一以类型名呈现。
    item_text = title if kind == 'loli' else f'{title}{role.name}'
    if kind == 'shota':
        item_text = title
    text = (
        f'{giver_name} 想把今天的{item_text}送给你！\n'
        f'请在 {GIFT_CONFIRM_TIMEOUT_SECONDS} 秒内发送「接受{title}赠送」，'
        f'或发送「拒绝{title}赠送」，超时自动取消。'
    )
    await _safe_send(bot, [MessageSegment.at(target_user_id), '\n', text])


async def _accept_gift_daily(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    # 待确认键以接受方为末段，故此处用接受者自身的键来查找。
    target_user_id = _user_key(ev)
    pending = _get_pending_gift(ev, target_user_id, kind)
    if pending is None:
        return await _safe_send(
            bot,
            f'没有待确认的送{title}请求，可能已经超时或被取消了~',
        )

    giver_id = str(pending['giver_id'])
    # 先摘除待确认项再执行校验：本函数后续可能因状态失效而提前返回，
    # 若不先摘除，该请求会一直留到超时，用户在窗口内无法重新发起。
    _clear_pending_gift(ev, target_user_id, kind)

    # 与发起阶段同源的功能判定。此处不重新校验功能开关是否变化，
    # 因为开关变更属于运维操作，已登记的请求按登记时状态继续处理更符合预期。
    if kind == 'husband' and not _husband_available():
        return
    if not _gift_enabled(kind):
        return

    giver_record = await _get_existing_daily_record(ev, giver_id, kind)
    if giver_record is None:
        return await _safe_send(
            bot,
            f'对方现在已经没有{title}可以送给你了，赠送已失效~',
        )

    response: str | None = None
    confirmed_giver_record: WifeRecord | None = None
    # 关键临界区：从读取上下文到写回记录全程持锁。发起与确认之间相隔最长 60 秒，
    # 期间赠送方的对象可能被抢、被转送，接受方也可能自己抽到了对象；只有在这里
    # 基于最新状态重新判定，才能避免把已不属于赠送方的记录写进接受方账下。
    async with _daily_context_lock(ev):
        context = await _load_daily_context(ev)
        bucket = _daily_bucket_name(kind)
        giver_data = context[bucket].get(giver_id)

        # 逐项重新校验，并把失败原因写入 response 后统一在锁外发送，
        # 避免持锁做网络往返而阻塞同上下文的其它命令。
        state = _wife_state(giver_data)
        if state == 'lost_stolen':
            response = f'对方的{title}已经被抢走了，赠送已失效~'
        elif state == 'lost_gifted':
            response = f'对方已经把{title}送给别人了，赠送已失效~'
        elif state == 'divorced':
            response = f'对方已经和{title}离婚了，赠送已失效~'
        elif _is_secondhand_wife(giver_data):
            response = f'这个{title}是抢来或别人送的，不能再送出去，赠送已失效~'
        elif _has_active_wife(context[bucket].get(target_user_id)):
            # 接受方在等待期间自己抽到了对象，此时接受会造成一人持有两份记录。
            response = f'你现在已经有{title}了，不需要接受赠送啦~'
        else:
            # 从上下文中的字典重建记录：上下文里存的是原始 dict，
            # 而写回需要不可变的记录对象（其构造会再次校验字段完整性）。
            confirmed_giver_record = _record_from_dict(giver_data)
            if confirmed_giver_record is None:
                response = f'对方现在已经没有{title}可以送给你了，赠送已失效~'
            else:
                # gifted_from 标记接受方的记录来源，供后续判断其为「别人送的」，
                # 从而禁止再次转送。
                receiver_record = _record_to_dict(confirmed_giver_record, ev, target_user_id)
                receiver_record['gifted_from'] = giver_id
                giver_update = context[bucket].get(giver_id)
                # 两次写入在同一批提交中落盘：接受方新增记录与赠送方标记转出必须原子生效，
                # 若分两次提交，中间失败会造成记录被复制而非转移。
                updates = [(bucket, target_user_id, receiver_record)]
                if isinstance(giver_update, dict):
                    # 复制后再改，避免就地修改上下文缓存中的字典。
                    giver_update = dict(giver_update)
                    giver_update['gifted_to'] = target_user_id
                    giver_update['gifted_to_name'] = name_from_event(ev, target_user_id)
                    updates.append((bucket, giver_id, giver_update))
                await _save_daily_records(ev, updates)

    if response is not None:
        return await _safe_send(bot, response)
    # 双重保护：临界区内未确认记录时不可继续发送成功结果。
    if confirmed_giver_record is None:
        return await _safe_send(bot, f'对方现在已经没有{title}可以送给你了，赠送已失效~')

    role = confirmed_giver_record.to_role()
    # 结果图归属赠送方（其记录被转出），因此用户键传 giver_id 而非接受方。
    await _send_gift_result_image(
        bot,
        role,
        confirmed_giver_record.image,
        _build_gift_success_text(role, target_user_id, kind),
        giver_id,
        ev.group_id is not None,
        kind,
    )


async def _reject_gift_daily(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    target_user_id = _user_key(ev)
    # 无待确认项时不报错也不提示「已拒绝」：避免用户通过反复发送拒绝命令
    # 探测他人是否正在向自己发起赠送。
    if _get_pending_gift(ev, target_user_id, kind) is None:
        return await _safe_send(bot, f'没有待确认的送{title}请求。')
    _clear_pending_gift(ev, target_user_id, kind)
    await _safe_send(bot, f'已拒绝对方的送{title}请求。')


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


async def _send_gift_shota(bot: Bot, ev: Event) -> None:
    await _send_gift_daily(bot, ev, 'shota')


async def _accept_gift_shota(bot: Bot, ev: Event) -> None:
    await _accept_gift_daily(bot, ev, 'shota')


async def _reject_gift_shota(bot: Bot, ev: Event) -> None:
    await _reject_gift_daily(bot, ev, 'shota')


@gift_sv.on_prefix(
    ('送老婆', '送今日老婆'),
    block=True,
    to_ai="""把当前用户今天的老婆送给指定用户。
    当用户说“把我的老婆送给某人”“送老婆 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
    covers=['把自己今日老婆赠送给他人，需对方确认'],
    aliases=['今日老婆·送老婆', '今日老婆·赠送老婆'],
)
async def gift_wife(bot: Bot, ev: Event) -> None:
    await _send_gift_wife(bot, ev)


@gift_sv.on_fullmatch(
    ('送老婆', '送今日老婆'),
    block=True,
    to_ai="""显示送老婆的用法。
    当用户只说“送老婆”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['把自己今日老婆赠送给他人，需对方确认'],
    aliases=['今日老婆·送老婆', '今日老婆·赠送老婆'],
)
async def gift_wife_at(bot: Bot, ev: Event) -> None:
    await _send_gift_wife(bot, ev)


@gift_sv.on_fullmatch(
    ('接受老婆赠送', '同意送老婆'),
    block=True,
    to_ai="""同意接收别人赠送的今日老婆。
    当用户说“接受老婆赠送”“同意送老婆”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['接受他人赠送的老婆'],
    aliases=['今日老婆·接受送老婆', '今日老婆·同意送老婆'],
)
async def gift_wife_accept(bot: Bot, ev: Event) -> None:
    await _accept_gift_wife(bot, ev)


@gift_sv.on_fullmatch(
    ('拒绝老婆赠送', '拒绝送老婆'),
    block=True,
    to_ai="""拒绝接收别人赠送的今日老婆。
    当用户说“拒绝老婆赠送”“拒绝送老婆”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['拒绝他人赠送的老婆'],
    aliases=['今日老婆·拒绝送老婆'],
)
async def gift_wife_reject(bot: Bot, ev: Event) -> None:
    await _reject_gift_wife(bot, ev)


@gift_sv.on_prefix(
    ('送老公', '送今日老公'),
    block=True,
    to_ai="""把当前用户今天的老公送给指定用户。
    当用户说“把我的老公送给某人”“送老公 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
    covers=['把自己今日老公赠送给他人，需对方确认'],
    aliases=['今日老婆·送老公', '今日老婆·赠送老公'],
)
async def gift_husband(bot: Bot, ev: Event) -> None:
    await _send_gift_husband(bot, ev)


@gift_sv.on_fullmatch(
    ('送老公', '送今日老公'),
    block=True,
    to_ai="""显示送老公的用法。
    当用户只说“送老公”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['把自己今日老公赠送给他人，需对方确认'],
    aliases=['今日老婆·送老公', '今日老婆·赠送老公'],
)
async def gift_husband_at(bot: Bot, ev: Event) -> None:
    await _send_gift_husband(bot, ev)


@gift_sv.on_fullmatch(
    ('接受老公赠送', '同意送老公'),
    block=True,
    to_ai="""同意接收别人赠送的今日老公。
    当用户说“接受老公赠送”“同意送老公”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['接受他人赠送的老公'],
    aliases=['今日老婆·接受送老公', '今日老婆·同意送老公'],
)
async def gift_husband_accept(bot: Bot, ev: Event) -> None:
    await _accept_gift_husband(bot, ev)


@gift_sv.on_fullmatch(
    ('拒绝老公赠送', '拒绝送老公'),
    block=True,
    to_ai="""拒绝接收别人赠送的今日老公。
    当用户说“拒绝老公赠送”“拒绝送老公”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['拒绝他人赠送的老公'],
    aliases=['今日老婆·拒绝送老公'],
)
async def gift_husband_reject(bot: Bot, ev: Event) -> None:
    await _reject_gift_husband(bot, ev)


@gift_sv.on_prefix(
    ('送萝莉', '送今日萝莉'),
    block=True,
    to_ai="""把当前用户今天的萝莉送给指定用户。
    当用户说“把我的萝莉送给某人”“送萝莉 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
    covers=['把自己今日萝莉赠送给他人，需对方确认'],
    aliases=['今日老婆·送萝莉', '今日老婆·赠送萝莉'],
)
async def gift_loli(bot: Bot, ev: Event) -> None:
    await _send_gift_loli(bot, ev)


@gift_sv.on_fullmatch(
    ('送萝莉', '送今日萝莉'),
    block=True,
    to_ai="""显示送萝莉的用法。
    当用户只说“送萝莉”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['把自己今日萝莉赠送给他人，需对方确认'],
    aliases=['今日老婆·送萝莉', '今日老婆·赠送萝莉'],
)
async def gift_loli_at(bot: Bot, ev: Event) -> None:
    await _send_gift_loli(bot, ev)


@gift_sv.on_fullmatch(
    ('接受萝莉赠送', '同意送萝莉'),
    block=True,
    to_ai="""同意接收别人赠送的今日萝莉。
    当用户说“接受萝莉赠送”“同意送萝莉”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['接受他人赠送的萝莉'],
    aliases=['今日老婆·接受送萝莉', '今日老婆·同意送萝莉'],
)
async def gift_loli_accept(bot: Bot, ev: Event) -> None:
    await _accept_gift_loli(bot, ev)


@gift_sv.on_fullmatch(
    ('拒绝萝莉赠送', '拒绝送萝莉'),
    block=True,
    to_ai="""拒绝接收别人赠送的今日萝莉。
    当用户说“拒绝萝莉赠送”“拒绝送萝莉”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['拒绝他人赠送的萝莉'],
    aliases=['今日老婆·拒绝送萝莉'],
)
async def gift_loli_reject(bot: Bot, ev: Event) -> None:
    await _reject_gift_loli(bot, ev)


@gift_sv.on_prefix(
    ('送正太', '送今日正太'),
    block=True,
    to_ai="""把当前用户今天的正太送给指定用户。
    当用户说“把我的正太送给某人”“送正太 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
    covers=['把自己今日正太赠送给他人，需对方确认'],
    aliases=['今日老婆·送正太', '今日老婆·赠送正太'],
)
async def gift_shota(bot: Bot, ev: Event) -> None:
    await _send_gift_shota(bot, ev)


@gift_sv.on_fullmatch(
    ('送正太', '送今日正太'),
    block=True,
    to_ai="""显示送正太的用法。
    当用户只说“送正太”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['把自己今日正太赠送给他人，需对方确认'],
    aliases=['今日老婆·送正太', '今日老婆·赠送正太'],
)
async def gift_shota_at(bot: Bot, ev: Event) -> None:
    await _send_gift_shota(bot, ev)


@gift_sv.on_fullmatch(
    ('接受正太赠送', '同意送正太'),
    block=True,
    to_ai="""同意接收别人赠送的今日正太。
    当用户说“接受正太赠送”“同意送正太”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['接受他人赠送的正太'],
    aliases=['今日老婆·接受送正太', '今日老婆·同意送正太'],
)
async def gift_shota_accept(bot: Bot, ev: Event) -> None:
    await _accept_gift_shota(bot, ev)


@gift_sv.on_fullmatch(
    ('拒绝正太赠送', '拒绝送正太'),
    block=True,
    to_ai="""拒绝接收别人赠送的今日正太。
    当用户说“拒绝正太赠送”“拒绝送正太”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['拒绝他人赠送的正太'],
    aliases=['今日老婆·拒绝送正太'],
)
async def gift_shota_reject(bot: Bot, ev: Event) -> None:
    await _reject_gift_shota(bot, ev)
