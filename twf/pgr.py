"""战双帕弥什本地图库抽取。"""
from __future__ import annotations

from .shared import *  # noqa: F403
from .folder_gallery import find_named_role_directory
from .image_input import collect_image_refs, image_hash_id, read_image_bytes


def _unique_pgr_image_path(role_dir: Path, suffix: str, index: int) -> Path:
    stamp = int(time.time() * 1000)
    counter = 0
    while True:
        tail = f'_{counter}' if counter else ''
        path = role_dir / f'pgr_{stamp}_{index}{tail}{suffix}'
        if not path.exists():
            return path
        counter += 1


def _save_pgr_image(role_dir: Path, source: str, index: int) -> Path | None:
    image_data = read_image_bytes(source, UPLOAD_IMAGE_MAX_BYTES)
    if image_data is None:
        return None
    data, suffix = image_data
    path = _unique_pgr_image_path(role_dir, suffix, index)
    path.write_bytes(data)
    return path


async def _send_upload_pgr_wife_images(bot: Bot, ev: Event) -> None:
    if not _can_upload_images(ev):
        return await _send_prefixed(bot, '你不在图片上传白名单中。', kind='pgr')

    role_name = str(ev.text or '').strip().strip('"“”‘’')
    if not role_name:
        return await _send_prefixed(
            bot,
            '请输入已有角色文件夹名称，例如：上传战双老婆图片 露西亚，并附带图片。',
            kind='pgr',
        )

    role_dir = find_named_role_directory(_pgr_wife_root(), role_name)
    if role_dir is None:
        return await _send_prefixed(
            bot,
            f'战双图库中不存在角色文件夹【{role_name}】，请先由主人创建对应文件夹。',
            kind='pgr',
        )

    image_refs = collect_image_refs(ev)
    if not image_refs:
        return await _send_prefixed(
            bot,
            f'请同时发送图片和命令，例如：上传战双老婆图片 {role_dir.name}',
            kind='pgr',
        )

    saved: list[Path] = []
    failed = 0
    for index, image_ref in enumerate(image_refs, 1):
        path = await asyncio.to_thread(_save_pgr_image, role_dir, image_ref, index)
        if path is None:
            failed += 1
        else:
            saved.append(path)

    if not saved:
        return await _send_prefixed(
            bot,
            f'【{role_dir.name}】上传图片失败，请确认消息里附带的是图片。',
            kind='pgr',
        )

    _invalidate_candidate_cache()
    image_ids = [image_hash_id(path) for path in saved]
    lines = [
        f'【{role_dir.name}】上传战双老婆图片成功',
        f'成功：{len(saved)} 张',
        f'图片ID：{", ".join(image_ids)}',
    ]
    if failed:
        lines.append(f'失败：{failed} 张')
    await _send_prefixed(bot, '\n'.join(lines), kind='pgr')


def _load_pgr_wife_candidates() -> tuple[RoleCandidate, ...]:
    return _load_pgr_local_candidates()


def _pgr_candidates_by_name(
    candidates: tuple[RoleCandidate, ...],
    specified_name: str,
) -> tuple[RoleCandidate, ...]:
    target = _normalize_role_name(specified_name).casefold()
    return tuple(
        candidate
        for candidate in candidates
        if _normalize_role_name(candidate.name).casefold() == target
    )


def _stored_pgr_record(raw: Any) -> WifeRecord | None:
    if not isinstance(raw, dict) or _wife_state(raw) != 'owned':
        return None
    record = _record_from_dict(raw)
    if record is None or not Path(record.image).is_file():
        return None
    return record


async def _ensure_daily_pgr_wife_record(ev: Event) -> WifeRecord | None:
    key = _user_key(ev)
    bucket = _daily_bucket_name('pgr')
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current_raw = context[bucket].get(key)
    if isinstance(current_raw, dict) and _wife_state(current_raw) != 'owned':
        return None
    current = _stored_pgr_record(current_raw)
    if current is not None:
        if await _nsfw_record_passes(current):
            return current
        context[bucket].pop(key, None)
        _save_wife_data(data)

    candidates = _load_pgr_wife_candidates()
    if not candidates:
        return None
    chosen = await _pick_nsfw_checked_role_record(
        candidates,
        _daily_rng(ev, key, 'pgr_wife'),
        'pgr',
    )
    if chosen is None:
        return None

    # await 后重新读取，避免并发命令覆盖已经生成的结果。
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    existing_raw = context[bucket].get(key)
    if isinstance(existing_raw, dict) and _wife_state(existing_raw) != 'owned':
        logger.debug(f'{LOG_PREFIX} 写入前发现已离手的战双老婆记录，拒绝覆盖')
        return None
    existing = _stored_pgr_record(existing_raw)
    if existing is not None:
        return existing
    context[bucket][key] = _record_to_dict(chosen, ev, key)
    _save_wife_data(data)
    logger.info(f'{LOG_PREFIX} 为用户 {key} 生成新的战双老婆: {chosen.name}')
    return chosen


def _pgr_result_text(record: WifeRecord) -> str | None:
    if not _cfg_bool('DailyWifeSendText', True):
        return None
    metadata = _daily_kind_metadata('pgr')
    template = str(_cfg(metadata.text_template_key) or metadata.text_template_default)
    return template.format(name=record.name, role_id='/'.join(record.role_ids))


async def _send_daily_pgr_wife(
    bot: Bot,
    ev: Event,
    specified_name: str = '',
) -> None:
    if not _cfg_bool('DailyWifePgrEnabled', True):
        return await _send_prefixed(bot, '今日战双老婆功能当前已关闭。', kind='pgr')

    is_master = _is_master(ev)
    is_debug_active = _cfg_bool('DailyWifeDebugMode', False) and is_master
    can_specify_role = _can_specify_wife(ev)
    specified_name = _normalize_role_name(specified_name)
    is_transient_draw = is_debug_active or bool(specified_name)

    if specified_name and not can_specify_role:
        return await _send_prefixed(
            bot,
            '只有机器人主人或指定老婆白名单用户才能指定战双老婆。',
            kind='pgr',
        )

    if not is_transient_draw:
        data = _load_wife_data()
        context = _get_today_context(data, ev)
        current = context[_daily_bucket_name('pgr')].get(_user_key(ev))
        state = _wife_state(current)
        if state == 'divorced':
            return await _send_prefixed(
                bot,
                '你今天已经和战双老婆离婚了，明天再来吧~',
                kind='pgr',
            )
        if state == 'lost_stolen':
            return await _send_prefixed(bot, '你的战双老婆已经被抢走了。', kind='pgr')
        if state == 'lost_gifted':
            return await _send_prefixed(bot, '你的战双老婆已经送出去了。', kind='pgr')

        other_wife_name = _get_other_daily_wife_name(ev, 'pgr')
        if other_wife_name:
            return await _send_prefixed(
                bot,
                f'你今天已经有{other_wife_name}了，不要贪心！',
                kind='pgr',
            )

    if is_transient_draw:
        candidates = _load_pgr_wife_candidates()
        if specified_name:
            candidates = _pgr_candidates_by_name(candidates, specified_name)
            if not candidates:
                return await _send_prefixed(
                    bot,
                    f'战双老婆图库中没有角色【{specified_name}】。',
                    kind='pgr',
                )
        record = await _pick_nsfw_checked_role_record(candidates, random, 'pgr')
    else:
        record = await _ensure_daily_pgr_wife_record(ev)
    if record is None:
        root = _pgr_wife_root()
        return await _send_prefixed(
            bot,
            '战双老婆图库里还没有可用图片。\n'
            f'把图片放成“{root}\\角色名\\图片文件”即可。',
            kind='pgr',
        )

    await _send_local_image(
        bot,
        record.image,
        '这张战双老婆图片已经不存在，请重新发送命令。',
        _pgr_result_text(record),
        ev.user_id,
        ev.group_id is not None,
        'pgr',
    )


@specify_wife_sv.on_prefix(
    ('今日战双老婆', 'jrzslp'),
    block=True,
    to_ai="""抽取当前用户今天的战双老婆。
    机器人主人或指定老婆白名单用户可以指定战双角色名。
    Args:
        text: 战双角色名，例如“露西亚”；仅主人或白名单用户可用。
    """,
)
async def daily_pgr_wife_prefix(bot: Bot, ev: Event) -> None:
    await _send_daily_pgr_wife(bot, ev, str(ev.text or '').strip())


@pgr_wife_sv.on_fullmatch(
    ('今日战双老婆', 'jrzslp'),
    block=True,
    to_ai="""随机抽取当前用户今天的战双老婆。
    当用户说“今日战双老婆”“jrzslp”“我今天的战双老婆是谁”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_pgr_wife(bot: Bot, ev: Event) -> None:
    await _send_daily_pgr_wife(bot, ev, '')


@image_upload_sv.on_command(
    ('上传战双老婆图片', '战双老婆上传图片'),
    block=True,
)
async def upload_pgr_wife(bot: Bot, ev: Event) -> None:
    await _send_upload_pgr_wife_images(bot, ev)
