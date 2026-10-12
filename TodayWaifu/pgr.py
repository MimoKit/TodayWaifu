"""战双帕弥什老婆的每日抽取：本地角色目录与远程图库共用同一套候选与投递路径。

战双的角色图片按「角色文件夹」组织，故存在两套候选来源：`_load_pgr_wife_candidates` 按
`_image_source('pgr')` 在本地目录与远程图库之间取舍。无论来源如何，记录都写入独立的
pgr 分桶（与鸣潮、异环相互独立），并在同一用户当天已有持有记录时拒绝重复抽取。
"""
from __future__ import annotations

from .daily import _build_text
from .shared import (
    LOG_PREFIX,
    UPLOAD_IMAGE_MAX_BYTES,
    Bot,
    Path,
    Event,
    WifeRecord,
    RoleCandidate,
    time,
    logger,
    random,
    _cfg_bool,
    _user_key,
    _daily_rng,
    _is_master,
    _safe_send,
    _wife_state,
    pgr_wife_sv,
    _pgr_wife_root,
    _record_to_dict,
    image_upload_sv,
    specify_wife_sv,
    _send_role_image,
    _can_specify_wife,
    _pick_role_record,
    _record_from_dict,
    _send_local_image,
    _can_upload_images,
    _daily_bucket_name,
    _daily_context_lock,
    _load_daily_context,
    _save_daily_records,
    _normalize_role_name,
    _load_pgr_wife_candidates,
    _get_other_daily_wife_name,
    _invalidate_candidate_cache,
)
from .executor import run_blocking
from .image_input import image_hash_id, read_image_bytes, collect_image_refs
from .folder_gallery import find_named_role_directory


def _unique_pgr_image_path(role_dir: Path, suffix: str, index: int) -> Path:
    # 毫秒时间戳在同一批次内可能重复，用自增尾号消解冲突，避免后写覆盖先写的图片
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
    # 超限或来源不是图片时返回 None，由调用方计入失败，不影响同批其余图片的落盘
    if image_data is None:
        return None
    data, suffix = image_data
    path = _unique_pgr_image_path(role_dir, suffix, index)
    path.write_bytes(data)
    return path


async def _send_upload_pgr_wife_images(bot: Bot, ev: Event) -> None:
    if not _can_upload_images(ev):
        return await _safe_send(bot, '你不在图片上传白名单中。')

    # 剥掉常见引号：用户习惯把角色名连同引号一起复制，未剥离会导致精确匹配失败
    role_name = str(ev.text or '').strip().strip('"“”‘’')
    if not role_name:
        return await _safe_send(
            bot,
            '请输入已有角色文件夹名称，例如：上传战双老婆图片 露西亚，并附带图片。',
        )

    # 只接受已存在的角色目录，不自动新建：目录名即角色名，凭空创建会把拼写错误固化成新角色，
    # 而图库目录由主人统一维护
    role_dir = find_named_role_directory(_pgr_wife_root(), role_name)
    if role_dir is None:
        return await _safe_send(
            bot,
            f'战双图库中不存在角色文件夹【{role_name}】，请先由主人创建对应文件夹。',
        )

    image_refs = collect_image_refs(ev)
    if not image_refs:
        return await _safe_send(
            bot,
            f'请同时发送图片和命令，例如：上传战双老婆图片 {role_dir.name}',
        )

    saved: list[Path] = []
    failed = 0
    # 单张失败不中断整批：附件中可能混有非图片内容或超出体积上限的图片，已成功的部分仍应
    # 落盘并回报，失败数量单独统计后附在结果里
    for index, image_ref in enumerate(image_refs, 1):
        path = await run_blocking(_save_pgr_image, role_dir, image_ref, index)
        if path is None:
            failed += 1
        else:
            saved.append(path)

    if not saved:
        return await _safe_send(
            bot,
            f'【{role_dir.name}】上传图片失败，请确认消息里附带的是图片。',
        )

    # 候选缓存必须失效：否则新上传的图片要等 TTL 到期才会进入抽取池
    _invalidate_candidate_cache()
    image_ids = [image_hash_id(path) for path in saved]
    lines = [
        f'【{role_dir.name}】上传战双老婆图片成功',
        f'成功：{len(saved)} 张',
        f'图片ID：{", ".join(image_ids)}',
    ]
    if failed:
        lines.append(f'失败：{failed} 张')
    await _safe_send(bot, '\n'.join(lines))


def _pgr_candidates_by_name(
    candidates: tuple[RoleCandidate, ...],
    specified_name: str,
) -> tuple[RoleCandidate, ...]:
    # 归一化后整体比较：不同来源对间隔号的写法不一致，逐字比较会把同一角色判成两个
    target = _normalize_role_name(specified_name).casefold()
    return tuple(
        candidate
        for candidate in candidates
        if _normalize_role_name(candidate.name).casefold() == target
    )


def _stored_pgr_record(raw: object) -> WifeRecord | None:
    # 仅接受 state 为 owned 的记录：已离婚/被抢/送出的记录继续当作持有会让用户重复抽取
    if not isinstance(raw, dict) or _wife_state(raw) != 'owned':
        return None
    record = _record_from_dict(raw)
    if record is None:
        return None
    # 远程图片 URL 无需本地校验；本地图片必须确认文件仍存在，否则会在发送阶段才失败
    if record.image.startswith(('http://', 'https://')):
        return record
    if not Path(record.image).is_file():
        return None
    return record


async def _ensure_daily_pgr_wife_record(
    ev: Event, specified_role: 'RoleCandidate | None' = None
) -> WifeRecord | None:
    key = _user_key(ev)
    bucket = _daily_bucket_name('pgr')
    context = await _load_daily_context(ev)
    current_raw = context[bucket].get(key)
    # 已离手的记录不参与复用：镜像下方锁内写入时的判定，避免对外表现不一致
    if isinstance(current_raw, dict) and _wife_state(current_raw) != 'owned':
        return None
    current = _stored_pgr_record(current_raw)
    if current is not None:
        return current

    if specified_role is not None:
        # 主人指定：跳过随机池，直接锁定指定角色
        chosen = _pick_role_record((specified_role,), random)
    else:
        candidates = await _load_pgr_wife_candidates()
        if not candidates:
            return None
        # 以「日期 + 用户」为种子的确定性随机：当天重复调用必然取到同一角色
        chosen = _pick_role_record(
            candidates,
            _daily_rng(ev, key, 'pgr_wife'),
        )
    if chosen is None:
        return None

    # 读取候选到写入之间存在 await，期间可能已有其它协程写入本用户的记录。因此持锁重新读取
    # 并二次校验：已有记录则复用之，已离手则拒绝覆盖，二者都保证「同一用户当天只有一个」。
    async with _daily_context_lock(ev):
        context = await _load_daily_context(ev)
        existing_raw = context[bucket].get(key)
        if isinstance(existing_raw, dict) and _wife_state(existing_raw) != 'owned':
            logger.debug(f'{LOG_PREFIX} 写入前发现已离手的战双老婆记录，拒绝覆盖')
            return None
        existing = _stored_pgr_record(existing_raw)
        if existing is not None:
            return existing
        value = _record_to_dict(chosen, ev, key)
        await _save_daily_records(ev, [(bucket, key, value)])
    logger.info(f'{LOG_PREFIX} 为用户 {key} 生成新的战双老婆: {chosen.name}')
    return chosen


def _pgr_result_text(record: WifeRecord, user_id: str = '') -> str | None:
    if not _cfg_bool('DailyWifeSendText', True):
        return None
    # 必须复用鸣潮/异环的公共文本构造：自建模板会漏掉角色台词与 ID 行，且台词开关、显示 ID
    # 等配置项将只对部分作品生效
    return _build_text(record.to_role(), 'pgr', user_id)


async def _send_daily_pgr_wife(
    bot: Bot,
    ev: Event,
    specified_name: str = '',
) -> None:
    if not _cfg_bool('DailyWifePgrEnabled', True):
        return

    is_master = _is_master(ev)
    is_debug_active = _cfg_bool('DailyWifeDebugMode', False) and is_master
    can_specify_role = _can_specify_wife(ev)
    specified_name = _normalize_role_name(specified_name)
    # 仅 Debug 模式保持临时预览不落库；主人指定同样写入每日记录，0 点随记录重置。若把主人
    # 指定也当作临时预览，指定结果不进记录，抢/送/离婚将全部读不到目标
    is_transient_draw = is_debug_active

    specified_role: RoleCandidate | None = None
    # 指定权限先于存在性校验：无权限用户不应从报错差异中探测图库内容
    if specified_name and not can_specify_role:
        return await _safe_send(
            bot,
            '只有机器人主人或指定老婆白名单用户才能指定战双老婆。',
        )

    if specified_name and not is_transient_draw:
        matched = _pgr_candidates_by_name(
            await _load_pgr_wife_candidates(), specified_name
        )
        if not matched:
            return await _safe_send(
                bot,
                f'战双老婆图库中没有角色【{specified_name}】。',
            )
        specified_role = matched[0]

    if not is_transient_draw:
        context = await _load_daily_context(ev)
        current_raw = context[_daily_bucket_name('pgr')].get(_user_key(ev))
        if specified_role is not None:
            owned = _stored_pgr_record(current_raw)
            # 已持有记录时指定同样被拒：指定不是绕过每日唯一性约束的通道
            if owned is not None:
                return await _safe_send(
                    bot,
                    f'你今天已经有{owned.name}了，不要贪心！',
                )
        state = _wife_state(current_raw)
        if state == 'divorced':
            return await _safe_send(
                bot,
                '你今天已经和战双老婆离婚了，明天再来吧~',
            )
        if state == 'lost_stolen':
            return await _safe_send(bot, '你的战双老婆已经被抢走了。')
        if state == 'lost_gifted':
            return await _safe_send(bot, '你的战双老婆已经送出去了。')

        # 战双与鸣潮/异环共享同一份「今日老婆」名额：分桶独立只用于存放数据，唯一性跨作品
        other_wife_name = await _get_other_daily_wife_name(ev, 'pgr')
        if other_wife_name:
            return await _safe_send(
                bot,
                f'你今天已经有{other_wife_name}了，不要贪心！',
            )

    if is_transient_draw:
        # 临时预览分支按真随机抽取且不落库，便于主人反复观察结果
        candidates = await _load_pgr_wife_candidates()
        if specified_name:
            candidates = _pgr_candidates_by_name(candidates, specified_name)
            if not candidates:
                return await _safe_send(
                    bot,
                    f'战双老婆图库中没有角色【{specified_name}】。',
                )
        record = _pick_role_record(candidates, random)
    else:
        record = await _ensure_daily_pgr_wife_record(ev, specified_role=specified_role)
    if record is None:
        root = _pgr_wife_root()
        return await _safe_send(
            bot,
            '战双老婆图库里还没有可用图片。\n'
            f'把图片放成“{root}\\角色名\\图片文件”即可。',
        )

    # 按图片来源选择发送通道：远程 URL 交给共享发送器下载与压缩，本地路径交由 _send_local_image
    # 处理「文件已不存在」的提示，二者不可互换
    if record.image.startswith(('http://', 'https://')):
        await _send_role_image(
            bot,
            RoleCandidate(record.name, record.role_ids, (record.image,)),
            record.image,
            _pgr_result_text(record, str(ev.user_id or '')) or '',
            ev.user_id,
            ev.group_id is not None,
            'pgr',
        )
        return

    await _send_local_image(
        bot,
        record.image,
        '这张战双老婆图片已经不存在，请重新发送命令。',
        _pgr_result_text(record, str(ev.user_id or '')),
        ev.user_id,
        ev.group_id is not None,
        'pgr',
    )


# ── 触发器注册 ────────────────────────────────────────────────────────────────
# block=True：命令命中后不再交给后续处理器，避免同一条消息被多个功能重复消费。
# to_ai 文本是 AI 工具的对外描述（调用时机与参数语义），与命令文案同属兼容性契约。
# 前缀与全匹配分别注册：前者允许附带角色名，后者覆盖不带参数的直接抽取


@specify_wife_sv.on_prefix(
    ('今日战双老婆', 'jrzslp'),
    block=True,
    to_ai="""抽取当前用户今天的战双老婆。
    机器人主人或指定老婆白名单用户可以指定战双角色名。
    Args:
        text: 战双角色名，例如“露西亚”；仅主人或白名单用户可用。
    """,
    covers=['战双帕弥什角色每日随机抽取，返回角色名与立绘'],
    aliases=['今日老婆·抽战双老婆', '今日老婆·今日战双老婆'],
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
    covers=['战双帕弥什角色每日随机抽取，返回角色名与立绘'],
    aliases=['今日老婆·抽战双老婆', '今日老婆·今日战双老婆'],
)
async def daily_pgr_wife(bot: Bot, ev: Event) -> None:
    await _send_daily_pgr_wife(bot, ev, '')


@image_upload_sv.on_command(
    ('上传战双老婆图片', '战双老婆上传图片'),
    block=True,
)
async def upload_pgr_wife(bot: Bot, ev: Event) -> None:
    await _send_upload_pgr_wife_images(bot, ev)
