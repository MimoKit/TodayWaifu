"""TodayWaifu - daily module."""
from __future__ import annotations

from .shared import *  # noqa: F403


OFFICIAL_MARKDOWN_BOT_IDS = {'qqgroup', 'qqguild'}


def _is_official_markdown_event(ev: Event) -> bool:
    for bot_id in (
        getattr(ev, 'real_bot_id', ''),
        getattr(ev, 'bot_id', ''),
        getattr(ev, 'WS_BOT_ID', ''),
    ):
        bot_id_text = str(bot_id or '').split(':', 1)[0].strip().lower()
        if bot_id_text in OFFICIAL_MARKDOWN_BOT_IDS:
            return True
    return False


def _blockquote_markdown(text: str) -> str:
    lines = text.splitlines() or ['']
    return '\n'.join(f'> {line}' if line else '>' for line in lines)


def _wife_list_markdown_from_items(
    title_text: str,
    items: list[tuple[int, str, str]],
    mode: str = 'wife',
) -> str:
    text = _wife_list_text_from_items(title_text, items)
    if _cfg_bool('DailyWifeReplyPrefixEnabled', True):
        text = _reply_text(text, mode)
    return _blockquote_markdown(text)


def _build_text(role: RoleCandidate, mode: str = 'wife') -> str:
    metadata = _daily_kind_metadata(mode)
    template = str(_cfg(metadata.text_template_key) or metadata.text_template_default)
    lines = [
        template.format(
            name=role.name,
            role_id='/'.join(role.role_ids),
        )
    ]
    if bool(_cfg('DailyWifeShowRoleId')):
        lines.append(f'角色ID：{"/".join(role.role_ids)}')
    return '\n'.join(lines)


def _build_member_text(member: MemberCandidate, mode: str = 'daily') -> str:
    if mode == 'marry':
        template = str(_cfg('DailyWifeMarryGroupMemberTextTemplate') or '你娶到的群友是{name}')
    else:
        template = str(_cfg('DailyWifeGroupMemberTextTemplate') or '你今天的老婆是{name}')
    return template.format(name=member.name, user_id=member.user_id)


def _record_text(record: WifeRecord, mode: str = 'wife') -> str:
    if record.record_type == 'member':
        return _build_member_text(record.to_member())
    return _build_text(record.to_role(), mode)


async def _ensure_daily_wife_record(
    ev: Event, user_id: str | int | None = None, mode: str = 'wife'
) -> WifeRecord | None:
    bucket = _daily_bucket_name(mode)
    salt = mode if mode != 'wife' else ''
    key = _user_key(ev, user_id)

    # 快速路径：当天已有记录直接返回
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = context[bucket].get(key)
    if isinstance(current, dict):
        if _wife_state(current) != 'owned':
            logger.debug(f'{LOG_PREFIX} 命中已离手的 {mode} 记录，拒绝复用')
            return None
        record = _record_from_dict(current)
        if record is not None:
            if not await _nsfw_record_passes(record):
                logger.info(f'{LOG_PREFIX} 已有 {mode} 记录未通过 NSFW 检测，静默替换: {record.name}')
                context[bucket].pop(key, None)
                _save_wife_data(data)
            else:
                logger.debug(f'{LOG_PREFIX} 命中已有的 {mode} 记录: {record.name}')
                return record

    # 准备阶段：含 await，先不持有待写入的 data，避免覆盖期间其它协程的写入
    chosen: WifeRecord | None = None
    if mode == 'wife':
        chosen = await _roll_group_member_wife(ev, key)
        if chosen is not None and not await _nsfw_record_passes(chosen):
            logger.info(f'{LOG_PREFIX} 群友老婆未通过 NSFW 检测，改抽角色: {chosen.name}')
            chosen = None

    if chosen is None:
        candidates, error = await _load_candidates(mode)
        if error or not candidates:
            logger.error(f'{LOG_PREFIX} 获取候选列表失败: {error}')
            return None
        candidates = _filter_by_mode(candidates, mode)
        if not candidates:
            logger.warning(f'{LOG_PREFIX} 过滤后没有可用的 {mode} 角色')
            return None
        rng = _daily_rng(ev, key, salt)
        chosen = await _pick_nsfw_checked_role_record(candidates, rng, mode)
        if chosen is None:
            logger.warning(f'{LOG_PREFIX} 没有通过 NSFW 检测的 {mode} 角色图片')
            return None

    # 写入阶段：重新加载并二次校验，整段不含 await，事件循环下保证原子
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    existing = context[bucket].get(key)
    if isinstance(existing, dict):
        if _wife_state(existing) != 'owned':
            logger.debug(f'{LOG_PREFIX} 写入前发现已离手的 {mode} 记录，拒绝覆盖')
            return None
        existing_record = _record_from_dict(existing)
        if existing_record is not None:
            logger.debug(f'{LOG_PREFIX} 写入前发现已有 {mode} 记录，直接复用: {existing_record.name}')
            return existing_record

    logger.info(f'{LOG_PREFIX} 为用户 {key} 生成新的 {mode}: {chosen.name}')
    context[bucket][key] = _record_to_dict(chosen, ev, key)
    _save_wife_data(data)
    return chosen



async def _wife_list_items(ev: Event, mode: str = 'wife') -> tuple[str, list[tuple[int, str, str]]]:
    bucket = 'husbands' if mode == 'husband' else 'wives'
    title = '老公' if mode == 'husband' else '老婆'
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    wives = context.get(bucket, {})
    if not isinstance(wives, dict):
        wives = {}

    group_display_names = await _load_group_display_names(ev)
    data_changed = False
    items: list[tuple[int, str, str]] = []
    seen_users: set[str] = set()
    for user_id, raw_record in wives.items():
        if not isinstance(raw_record, dict):
            continue
        record = _record_from_dict(raw_record)
        if record is None:
            continue
        seen_users.add(user_id)
        display_name = _valid_display_name(raw_record.get('display_name'), user_id)
        if not display_name:
            display_name = group_display_names.get(str(user_id), '')
            if display_name:
                raw_record['display_name'] = display_name
                raw_record['display_name_source'] = 'coreuser'
                raw_record['display_name_updated_at'] = int(time.time())
                data_changed = True
        if not display_name:
            display_name = str(user_id)
        updated_at = raw_record.get('updated_at')
        try:
            order = int(updated_at)
        except (TypeError, ValueError):
            order = 0
        state = _wife_state(raw_record)
        # 被抢但有补偿老婆的，留给 safe_wives 循环显示补偿名字，不显示"被抢走了~"
        if state == 'lost_stolen' and isinstance(context.get('safe_wives', {}).get(user_id), dict):
            continue
        if state == 'lost_stolen':
            wife_name = '被抢走了~'
        elif state == 'lost_gifted':
            wife_name = '送出去了~'
        elif state == 'divorced':
            wife_name = '离婚了~'
        else:
            wife_name = record.name

        items.append((order, display_name, wife_name))

    # 补偿老婆（safe_wives）：被抢后重抽的补偿记录，显示"(补)"后缀
    if mode == 'wife':
        safe_wives = context.get('safe_wives', {})
        if isinstance(safe_wives, dict):
            for user_id, raw_record in safe_wives.items():
                if not isinstance(raw_record, dict):
                    continue
                record = _record_from_dict(raw_record)
                if record is None:
                    continue
                seen_users.add(user_id)
                display_name = _valid_display_name(raw_record.get('display_name'), user_id)
                if not display_name:
                    display_name = group_display_names.get(str(user_id), '')
                    if display_name:
                        raw_record['display_name'] = display_name
                        raw_record['display_name_source'] = 'coreuser'
                        raw_record['display_name_updated_at'] = int(time.time())
                        data_changed = True
                if not display_name:
                    display_name = str(user_id)
                updated_at = raw_record.get('updated_at')
                try:
                    order = int(updated_at)
                except (TypeError, ValueError):
                    order = 0
                state = _wife_state(raw_record)
                if state == 'divorced':
                    items.append((order, display_name, '离婚了~'))
                else:
                    items.append((order, display_name, record.name + '(补)'))

    if not items:
        return f'今天本群还没有可用的{title}记录。', []

    if data_changed:
        _save_wife_data(data)

    items.sort(key=lambda item: (item[0], item[1]))
    return f'今日{title}列表：', items


def _wife_list_text_from_items(title_text: str, items: list[tuple[int, str, str]]) -> str:
    if not items:
        return title_text
    lines = [title_text]
    lines.extend(f'{index}. {display_name} → {wife_name}' for index, (_, display_name, wife_name) in enumerate(items, 1))
    return '\n'.join(lines)


async def _wife_list_text(ev: Event, mode: str = 'wife') -> str:
    title_text, items = await _wife_list_items(ev, mode)
    return _wife_list_text_from_items(title_text, items)



async def _send_record_image(
    bot: Bot,
    record: WifeRecord,
    mode: str = 'wife',
    user_id: str | int | None = None,
    is_group: bool = True,
) -> None:
    text = _record_text(record, mode) if bool(_cfg('DailyWifeSendText')) else None
    if record.record_type == 'member':
        await _send_local_image(
            bot,
            record.image,
            '本地群友头像文件不存在，请稍后重试。',
            text,
            user_id,
            is_group,
            mode,
        )
        return
    await _send_role_image(bot, record.to_role(), record.image, text, user_id, is_group, mode)



async def _send_daily_wife(bot: Bot, ev: Event, mode: str = 'wife', specified_name: str = ''):
    title = _daily_item_title(mode)
    logger.debug(
        f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} '
        f'请求 {title} (指定: {specified_name or "无"})'
    )

    other_wife_name = _get_other_daily_wife_name(ev, mode)
    if other_wife_name:
        return await _send_prefixed(
            bot,
            f'你今天已经有{other_wife_name}了，不要贪心！',
            kind=mode,
        )
    
    is_master = _is_master(ev)
    is_debug = _cfg_bool('DailyWifeDebugMode', False)
    is_debug_active = is_debug and is_master

    if not is_debug_active:
        data = _load_wife_data()
        context = _get_today_context(data, ev)
        user_key = _user_key(ev)
        bucket = _daily_bucket_name(mode)
        current_record = context[bucket].get(user_key)

        # 离手即结算：老婆被抢走后可补偿重抽一次（safe_wife），送出/离婚仍锁死；老公离手后也锁死。
        state = _wife_state(current_record)
        if state == 'lost_stolen' and mode == 'wife':
            # 已有补偿老婆的直接展示
            safe_record = context['safe_wives'].get(user_key)
            if isinstance(safe_record, dict):
                safe_wife = _record_from_dict(safe_record)
                if safe_wife is not None:
                    if not await _nsfw_record_passes(safe_wife):
                        logger.info(f'{LOG_PREFIX} 已有补偿老婆未通过 NSFW 检测，静默替换: {safe_wife.name}')
                        context['safe_wives'].pop(user_key, None)
                        _save_wife_data(data)
                    else:
                        logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 展示已有的补偿老婆: {safe_wife.name}')
                        return await _send_record_image(bot, safe_wife, mode, ev.user_id, ev.group_id is not None)

            # 未抽过补偿老婆：抽一个，写入 safe_wives
            wife_name = current_record.get('name', '老婆')
            stolen_by_name = current_record.get('stolen_by_name') or current_record.get('stolen_by')
            candidates, error = await _load_candidates(mode)
            if error or not candidates:
                return await _send_prefixed(bot, error or '没有找到可用角色。', kind=mode)
            if not candidates:
                return await _send_prefixed(bot, f'没有找到可用的{title}角色。', kind=mode)
            rng = _daily_rng(ev, user_key, f'{mode}_safe')
            candidates = _filter_by_mode(candidates, mode)
            safe_wife = await _pick_nsfw_checked_role_record(candidates, rng, mode)
            if safe_wife is None:
                logger.warning(f'{LOG_PREFIX} 补偿抽取没有通过 NSFW 检测的图片')
                return await _send_prefixed(bot, f'没有找到可用的{title}角色。', kind=mode)
            context['safe_wives'][user_key] = _record_to_dict(safe_wife, ev, user_key)
            context['safe_wives'][user_key]['safe'] = True
            _save_wife_data(data)
            logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 的老婆被抢，补偿抽取: {safe_wife.name}')
            return await _send_role_image(
                bot, safe_wife.to_role(), safe_wife.image,
                text=f'你的{wife_name}已经被{stolen_by_name}抢走了…\n但你迎来了新的{title}{safe_wife.name}！',
                user_id=ev.user_id,
                is_group=ev.group_id is not None,
                kind=mode,
            )
        if state == 'lost_stolen':
            item_name = current_record.get('name', title) if isinstance(current_record, dict) else title
            stolen_by_name = current_record.get('stolen_by_name') or current_record.get('stolen_by')
            return await _send_prefixed(
                bot,
                f'你的{item_name}已经被{stolen_by_name}抢走了，今天就先忍忍吧~',
                kind=mode,
            )
        if state == 'lost_gifted':
            wife_name = current_record.get('name', title)
            gifted_to_name = current_record.get('gifted_to_name') or current_record.get('gifted_to')
            logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 的{title}已送出，拒绝分配新角色')
            return await _send_prefixed(
                bot,
                f'你的{wife_name}已经送给{gifted_to_name}了，今天就先忍忍吧~',
                kind=mode,
            )
        if state == 'divorced':
            item_name = current_record.get('name', title) if isinstance(current_record, dict) else title
            return await _send_prefixed(
                bot,
                f'你今天已经和{item_name}离婚了，明天再来吧~',
                kind=mode,
            )

    record: WifeRecord | None = None

    if is_debug_active:
        logger.debug(f'{LOG_PREFIX} 主人 Debug 模式开启')
        candidates, error = await _load_candidates(mode)
        if error or not candidates:
            return await _send_prefixed(bot, error or '没有找到可用角色。', kind=mode)
        if not candidates:
            return await _send_prefixed(bot, f'没有找到可用的{title}角色。', kind=mode)
        if specified_name:
            target_candidates = [c for c in candidates if c.name == specified_name]
            if not target_candidates:
                return await _send_prefixed(
                    bot,
                    f'未找到名为“{specified_name}”的{title}角色。',
                    kind=mode,
                )
            candidates = tuple(target_candidates)
        else:
            candidates = _filter_by_mode(candidates, mode)

        record = await _pick_nsfw_checked_role_record(candidates, random, mode)
        if record is None:
            logger.warning(f'{LOG_PREFIX} Debug 抽取没有通过 NSFW 检测的图片')
            return await _send_prefixed(bot, f'没有找到可用的{title}角色。', kind=mode)
    else:
        if specified_name:
            logger.warning(f'{LOG_PREFIX} 普通用户 {ev.user_id} 尝试指定角色 {specified_name}，已拒绝')
            return await _send_prefixed(
                bot,
                f'只有在 Debug 模式下主人才能指定{title}哦。',
                kind=mode,
            )

        record = await _ensure_daily_wife_record(ev, mode=mode)
        if record is None:
            return await _send_prefixed(bot, f'没有找到可用的{title}角色。', kind=mode)

    if record.record_type == 'member':
        member = record.to_member()
        logger.debug(
            f'{LOG_PREFIX} mode={mode} user={ev.user_id} group={ev.group_id or "direct"} '
            f'member={member.name} qq={member.user_id} avatar={record.image} debug={is_debug_active}'
        )
    else:
        role = record.to_role()
        logger.debug(
            f'{LOG_PREFIX} mode={mode} user={ev.user_id} group={ev.group_id or "direct"} '
            f'role={role.name} ids={role.role_ids} image={record.image} debug={is_debug_active}'
        )
    await _send_record_image(bot, record, mode, ev.user_id, ev.group_id is not None)


def _assignment_role_name(ev: Event, target_user_id: str) -> str:
    text = str(ev.text or '').strip()
    if not text:
        return ''

    text = re.sub(r'\[CQ:at,[^\]]*\]', ' ', text)
    text = re.sub(r'<(?:qqbot-)?at[^>]*>', ' ', text)
    text = re.sub(r'<qqbot-at-user[^>]*/?>', ' ', text)
    text = re.sub(r'@\S+', ' ', text)
    if target_user_id:
        text = text.replace(target_user_id, ' ')
    text = re.sub(r'\b(?:qq|QQ|id|user_id|openid|open_id)\s*[:=]\s*\S+', ' ', text)
    text = re.sub(r'[，,。；;：:\n\r\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    for prefix in ('给', '把', '将', '为'):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    for word in ('分配老婆', '分配今日老婆', '分配', '老婆'):
        text = text.replace(word, ' ')
    return re.sub(r'\s+', ' ', text).strip()


def _find_assignable_wife(candidates: tuple[RoleCandidate, ...], role_name: str) -> RoleCandidate | None:
    target = _normalize_role_name(role_name)
    for candidate in candidates:
        if _normalize_role_name(candidate.name) == target:
            return candidate
    for candidate in candidates:
        if role_name in candidate.role_ids:
            return candidate
    return None


async def _send_assign_wife(bot: Bot, ev: Event) -> None:
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 发起主人分配老婆命令')
    if not _is_master(ev):
        return await _send_prefixed(bot, '只有机器人主人可以分配老婆。')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await _send_prefixed(bot, '要分配给谁？用法：分配老婆 @对方 角色名')

    role_name = _assignment_role_name(ev, str(target_user_id))
    if not role_name:
        return await _send_prefixed(bot, '要分配哪个老婆？用法：分配老婆 @对方 角色名')

    candidates, error = await _load_candidates('wife')
    if error or not candidates:
        return await _send_prefixed(bot, error or '没有找到可用角色。')

    candidates = _filter_by_mode(candidates, 'wife')
    role = _find_assignable_wife(candidates, role_name)
    if role is None:
        return await _send_prefixed(bot, f'未找到名为“{role_name}”的老婆角色。')

    record = await _pick_nsfw_checked_role_record((role,), random, 'wife')
    if record is None:
        logger.warning(f'{LOG_PREFIX} 主人分配老婆未找到通过 NSFW 检测的图片: {role.name}')
        return await _send_prefixed(bot, f'未找到“{role.name}”可用的老婆图片。')
    image = record.image
    target_key = str(target_user_id)

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    context['wives'][target_key] = _record_to_dict(record, ev, target_key)
    context['wives'][target_key]['assigned_by'] = _user_key(ev)
    context['wives'][target_key]['assigned_by_name'] = _user_display_name(ev)
    if isinstance(context.get('safe_wives'), dict):
        context['safe_wives'].pop(target_key, None)
    _save_wife_data(data)

    logger.info(
        f'{LOG_PREFIX} 主人 {ev.user_id} 将老婆 {role.name} 分配给 {target_key}, '
        f'ids={role.role_ids} image={image}'
    )
    await _send_role_image(
        bot,
        role,
        image,
        f'已把今天的老婆{role.name}分配给对方。',
        target_key,
        ev.group_id is not None,
    )


async def _send_group_member_wife(bot: Bot, ev: Event):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 触发了娶群友命令')
    if not _marry_member_enabled():
        return await _send_prefixed(bot,'娶群友功能当前已关闭。')
    if not ev.group_id:
        return await _send_prefixed(bot,'这个命令只能在群聊里使用。')

    member = await _pick_group_member(ev, _event_rng(ev))
    if member is None:
        return await _send_prefixed(bot,'没有获取到本群成员，暂时娶不到群友。')

    logger.info(
        f'{LOG_PREFIX} marry_member user={ev.user_id} group={ev.group_id} '
        f'member={member.name} qq={member.user_id} avatar={member.avatar}'
    )
    text = _build_member_text(member, 'marry') if bool(_cfg('DailyWifeSendText')) else None
    await _send_local_image(bot, member.avatar, '本地群友头像文件不存在，请稍后重试。', text, ev.user_id, ev.group_id is not None)


async def _send_wife_list(bot: Bot, ev: Event, mode: str = 'wife'):
    logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id} 请求了 {mode} 列表')
    title_text, items = await _wife_list_items(ev, mode)
    if _is_official_markdown_event(ev):
        markdown = _wife_list_markdown_from_items(title_text, items, mode)
        await _safe_send(bot, MessageSegment.markdown(markdown))
        return
    if len(items) > LIST_FORWARD_THRESHOLD:
        await _send_prefixed(
            bot,
            MessageSegment.node([_wife_list_text_from_items(title_text, items)]),
            kind=mode,
        )
        return
    await _send_prefixed(bot, _wife_list_text_from_items(title_text, items), kind=mode)


@daily_wife_sv.on_prefix(
    ('今日老婆', '娶婆娘', 'jrlp', 'qlp'),
    block=True,
    to_ai="""抽取当前用户今天的老婆。
    当用户说“今日老婆”“帮我娶个老婆”“我今天的老婆是谁”时调用。
    如果用户指定了角色名，把角色名放在 text 里，例如“今汐”“长离”；如果用户要看列表，text 填“列表”。
    Args:
        text: 可选，指定老婆角色名；留空表示随机抽取今日老婆；填“列表”表示查看老婆列表。
    """,
)
async def daily_wife_prefix(bot: Bot, ev: Event):
    specified_name = str(ev.text or '').strip()
    if specified_name == '列表':
        return await _send_wife_list(bot, ev, mode='wife')
    await _send_daily_wife(bot, ev, mode='wife', specified_name=specified_name)


@daily_wife_sv.on_fullmatch(
    ('今日老婆', '娶婆娘', 'jrlp', 'qlp'),
    block=True,
    to_ai="""随机抽取当前用户今天的老婆。
    当用户说“今日老婆”“我今天老婆是谁”“帮我娶个老婆”且没有指定角色名时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_wife_full(bot: Bot, ev: Event):
    await _send_daily_wife(bot, ev, mode='wife', specified_name='')


@daily_nte_wife_sv.on_prefix(
    '今日异环老婆',
    block=True,
    to_ai="""抽取当前用户今天的异环老婆。
    该功能需要先在控制台开启。可在主人 Debug 模式下把角色名放入 text。
    Args:
        text: 可选，主人 Debug 模式下指定异环角色名；普通用户留空。
    """,
)
async def daily_nte_wife_prefix(bot: Bot, ev: Event):
    if not _cfg_bool('DailyWifeNteEnabled', False):
        return await _send_prefixed(bot, '今日异环老婆功能当前已关闭。')
    await _send_daily_wife(bot, ev, mode='nte', specified_name=str(ev.text or '').strip())


@daily_nte_wife_sv.on_fullmatch(
    '今日异环老婆',
    block=True,
    to_ai="""随机抽取当前用户今天的异环老婆。
    该功能需要先在控制台开启。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_nte_wife_full(bot: Bot, ev: Event):
    if not _cfg_bool('DailyWifeNteEnabled', False):
        return await _send_prefixed(bot, '今日异环老婆功能当前已关闭。')
    await _send_daily_wife(bot, ev, mode='nte', specified_name='')


@wife_list_sv.on_fullmatch(
    ('老婆列表', '查看老婆列表'),
    block=True,
    to_ai="""查看可抽取的老婆角色列表。
    当用户询问“老婆列表”“有哪些老婆可以抽”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_wife_list(bot: Bot, ev: Event):
    await _send_wife_list(bot, ev)


@assign_wife_sv.on_prefix(
    ('分配老婆', '分配今日老婆'),
    block=True,
    to_ai="""为指定用户分配今日老婆。
    当管理员或用户说“给某人分配老婆”“分配今日老婆 @某人 角色名”时调用。
    Args:
        text: 分配参数，通常包含目标用户和老婆名，例如“@用户 今汐”。
    """,
)
async def assign_wife(bot: Bot, ev: Event):
    await _send_assign_wife(bot, ev)


@assign_wife_sv.on_fullmatch(
    ('分配老婆', '分配今日老婆'),
    block=True,
    to_ai="""显示分配今日老婆的用法。
    当用户只说“分配老婆”但没有提供目标或角色名时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def assign_wife_usage(bot: Bot, ev: Event):
    await _send_assign_wife(bot, ev)


@daily_husband_sv.on_prefix(
    '今日老公',
    block=True,
    to_ai="""抽取当前用户今天的老公。
    当用户说“今日老公”“我今天的老公是谁”时调用。
    如果用户指定了角色名，把角色名放在 text 里；如果用户要看列表，text 填“列表”。
    Args:
        text: 可选，指定老公角色名；留空表示随机抽取今日老公；填“列表”表示查看老公列表。
    """,
)
async def daily_husband_prefix(bot: Bot, ev: Event):
    if not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message(), kind='husband')
    specified_name = str(ev.text or '').strip()
    if specified_name == '列表':
        return await _send_wife_list(bot, ev, mode='husband')
    await _send_daily_wife(bot, ev, mode='husband', specified_name=specified_name)


@daily_husband_sv.on_fullmatch(
    '今日老公',
    block=True,
    to_ai="""随机抽取当前用户今天的老公。
    当用户说“今日老公”“我今天老公是谁”且没有指定角色名时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_husband_full(bot: Bot, ev: Event):
    if not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message(), kind='husband')
    await _send_daily_wife(bot, ev, mode='husband', specified_name='')


@husband_list_sv.on_fullmatch(
    ('老公列表', '查看老公列表'),
    block=True,
    to_ai="""查看可抽取的老公角色列表。
    当用户询问“老公列表”“有哪些老公可以抽”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_husband_list(bot: Bot, ev: Event):
    if not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message(), kind='husband')
    await _send_wife_list(bot, ev, mode='husband')


@marry_member_sv.on_fullmatch(
    ('娶群友', '取群友'),
    block=True,
    to_ai="""随机抽取当前群里的一个群友作为今日互动对象。
    当用户说“娶群友”“随机娶一个群友”“帮我抽个群友”时调用；只能在群聊使用。
    Args:
        text: 无需参数，留空。
    """,
)
async def group_member_wife(bot: Bot, ev: Event):
    await _send_group_member_wife(bot, ev)
