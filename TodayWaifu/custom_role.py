"""TodayWaifu - custom_role module.

用户自定义角色横跨两份数据：对照表 JSON（角色 ID → 角色名）与按 ID 分目录的图片堆。
两者必须保持一致，否则会出现「表里有角色但没有图」或「图还在但已从列表消失」。
本模块因此把「先确定 ID、再落盘图片」作为固定顺序，并在删除时同时清理两处。

所有阻塞操作（目录扫描、图片读写、文件删除）都经 ``run_blocking`` 投递到专用线程池，
不占用事件循环。
"""
from __future__ import annotations

from .shared import (
    LOG_PREFIX,
    IMAGE_EXTENSIONS,
    CUSTOM_ROLE_ID_START,
    UPLOAD_IMAGE_MAX_BYTES,
    CUSTOM_ROLE_DELETE_PENDING,
    CUSTOM_ROLE_DELETE_CONFIRM_SECONDS,
    Bot,
    Path,
    Event,
    Message,
    MessageSegment,
    PendingCustomRoleDelete,
    re,
    time,
    logger,
    shutil,
    _user_key,
    _safe_send,
    _context_key,
    _load_role_map,
    custom_role_sv,
    write_role_map,
    image_upload_sv,
    _can_upload_images,
    _normalize_role_name,
    _writable_role_map_path,
    _image_message_from_path,
    _writable_role_pile_root,
    _invalidate_candidate_cache,
)
from .executor import run_blocking
from .image_input import (
    image_hash_id,
    read_image_bytes,
    collect_image_refs,
    detect_image_suffix,
    image_suffix_from_source,
)


def _role_ids_by_name(role_map: dict[str, str], role_name: str) -> tuple[str, ...]:
    # 名称比较统一经 _normalize_role_name 归一（大小写、全半角、空白），
    # 使用户输入「达妮娅 」与库中「达妮娅」被视为同一角色。
    # 排序键对纯数字 ID 按数值比较：字符串序会让 "10" 排在 "9" 之前，
    # 导致同名多 ID 时选中的不是最小 ID。
    target = _normalize_role_name(role_name)
    ids = [role_id for role_id, name in role_map.items() if _normalize_role_name(name) == target]
    return tuple(sorted(ids, key=lambda item: int(item) if item.isdigit() else item))


def _next_custom_role_id(role_map: dict[str, str], pile_root: Path) -> str:
    # 同时统计对照表与图片目录中的 ID：只表一方面会在「表中条目被手工删除但目录仍在」
    # 的情况下分配到已被占用的 ID，进而把新角色的图片写进旧角色的目录。
    used_ids: set[int] = set()
    for role_id in role_map:
        if role_id.isdigit():
            used_ids.add(int(role_id))
    if pile_root.is_dir():
        for item in pile_root.iterdir():
            if item.is_dir() and item.name.isdigit():
                used_ids.add(int(item.name))

    # 从自定义区间起点之后取最大值再加一，使 ID 只增不减：复用已删除角色的 ID
    # 会让历史记录中的 role_id 指向新角色，造成当天结果前后不一致。
    role_id = max([CUSTOM_ROLE_ID_START - 1, *(item for item in used_ids if item >= CUSTOM_ROLE_ID_START)]) + 1
    while role_id in used_ids:
        role_id += 1
    return str(role_id)


def _write_custom_role_map(path: Path, role_map: dict[str, str]) -> None:
    """原子写回自定义老婆对照表 JSON。"""
    # 写入委托给 role_map_store.write_role_map：对照表格式与原子替换策略集中在一处，
    # 避免本模块另写一份实现后两者在并发写时互相覆盖。
    write_role_map(path, role_map)


def _clean_upload_role_name(raw: str, strip_wife_suffix: bool = False) -> str:
    # 去除成对引号：用户常以「创建老婆 "达妮娅"」的形式输入，引号不应成为角色名的一部分。
    name = str(raw or '').strip().strip('"“”‘’')
    # 连续空白折叠为单个空格：角色名用于与图库目录名及对照表比对，多空格会造成匹配失败。
    name = re.sub(r'\s+', ' ', name)
    # 去掉末尾的「老婆」后缀，使「创建老婆 达妮娅」与「创建老婆 达妮娅老婆」指向同一角色；
    # 长度需大于 2，否则「老婆」本身会被清成空名。
    if strip_wife_suffix and name.endswith('老婆') and len(name) > 2:
        name = name[:-2].strip()
    return name


def _create_or_get_custom_role(role_name: str) -> tuple[str, bool, str | None]:
    role_name = _clean_upload_role_name(role_name)
    if not role_name:
        return '', False, '请输入角色名，例如：创建老婆 达妮娅'

    map_path = _writable_role_map_path()
    # 对照表缺失时按空表继续：首次使用插件时文件尚不存在，这是正常路径而非错误。
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    pile_root = _writable_role_pile_root()
    role_ids = _role_ids_by_name(role_map, role_name)
    if role_ids:
        # 已存在同名角色时复用首个 ID 并确保目录存在：用户可能先建角色、后单独上传图片，
        # 此时不能重新分配 ID，否则同一角色名会分裂成两份图片堆。
        role_id = role_ids[0]
        (pile_root / role_id).mkdir(parents=True, exist_ok=True)
        return role_id, False, None

    role_id = _next_custom_role_id(role_map, pile_root)
    role_map[role_id] = role_name
    _write_custom_role_map(map_path, role_map)
    # 目录先于图片创建，使上传流程只需写入文件，无需再关心目录状态。
    (pile_root / role_id).mkdir(parents=True, exist_ok=True)
    # 对照表已变更，候选缓存中不含新角色，必须失效后才会在下一次抽卡中出现。
    _invalidate_candidate_cache()
    logger.info(f'{LOG_PREFIX} 创建自定义老婆角色: {role_name} -> {role_id}')
    return role_id, True, None


def _upload_image_refs(ev: Event) -> tuple[str, ...]:
    # 转发而非直接调用：消息中可能同时含图片、表情与文本，抽取策略属于输入解析模块的职责，
    # 本模块只消费结果，便于该策略独立测试。
    return collect_image_refs(ev)


def _image_suffix_from_source(source: str) -> str:
    return image_suffix_from_source(source)


def _detect_upload_image_suffix(data: bytes, source: str) -> str:
    return detect_image_suffix(data, source)


def _read_upload_image_bytes(source: str) -> tuple[bytes, str] | None:
    # 上限由常量统一传入：图片最终要经消息发送，超大文件会在发送阶段失败，
    # 提前在读取时拒绝可避免写入无用的磁盘文件。
    return read_image_bytes(source, UPLOAD_IMAGE_MAX_BYTES)


def _unique_upload_image_path(role_dir: Path, role_id: str, suffix: str, index: int) -> Path:
    # 文件名由「角色ID_毫秒时间戳_序号」构成，序号用于区分同一条消息中的多张图片。
    # 同毫秒并发写入仍可能重名，故循环探测并在末尾追加计数器，直至得到未占用的路径。
    stamp = int(time.time() * 1000)
    counter = 0
    while True:
        tail = f'_{counter}' if counter else ''
        path = role_dir / f'{role_id}_{stamp}_{index}{tail}{suffix}'
        if not path.exists():
            return path
        counter += 1


def _custom_image_hash_id(path: Path | str) -> str:
    return image_hash_id(path)


def _custom_role_image_map(role_id: str) -> dict[str, Path]:
    role_dir = _writable_role_pile_root() / str(role_id)
    if not role_dir.is_dir():
        return {}
    # 键为内容哈希而非文件名：用户重复上传同一张图时不会产生两条记录，
    # 删除操作也能按哈希精确定位，不受文件名变化影响。
    result: dict[str, Path] = {}
    for path in sorted(role_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            result[_custom_image_hash_id(path)] = path
    return result



def _custom_role_ids_for_name(role_name: str) -> tuple[str, ...]:
    # 每次调用都重新读取对照表而非缓存：对照表可被用户手工编辑，
    # 缓存会使其改动直到进程重启才生效。
    map_path = _writable_role_map_path()
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    return _role_ids_by_name(role_map, role_name)


def _custom_role_name_by_id(role_id: str) -> str:
    map_path = _writable_role_map_path()
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    # 查不到时回显 ID 而非空串：记录中可能残留已删除角色的 ID，
    # 回显 ID 至少能让用户知道该记录指向哪个角色。
    return role_map.get(str(role_id), str(role_id))


def _remove_custom_role_map_ids(role_ids: tuple[str, ...]) -> None:
    # 无 ID 时不读盘：空操作若仍读写对照表，会在文件缺失时产生无意义的 IO。
    if not role_ids:
        return
    map_path = _writable_role_map_path()
    if not map_path.is_file():
        return
    # 读改写之间无显式锁：本模块的删除均由命令处理器串行触发，且写入使用原子替换，
    # 最坏情况是两个并发删除各自基于旧快照写回，故仅在确有变更时才写盘以缩小窗口。
    role_id_set = {str(role_id) for role_id in role_ids}
    role_map = _load_role_map(map_path)
    kept = {key: value for key, value in role_map.items() if key not in role_id_set}
    if len(kept) != len(role_map):
        _write_custom_role_map(map_path, kept)


def _custom_role_delete_confirm_key(ev: Event) -> str:
    # 与会话和用户双重绑定：删除不可逆，不接受他人代为确认，
    # 且同一用户在不同会话下的待确认项互不影响。
    return f'{_context_key(ev)}:{_user_key(ev)}'


def _get_pending_custom_role_delete(ev: Event) -> PendingCustomRoleDelete | None:
    key = _custom_role_delete_confirm_key(ev)
    pending = CUSTOM_ROLE_DELETE_PENDING.get(key)
    if not isinstance(pending, dict):
        return None
    try:
        created_at = int(pending.get('created_at') or 0)
    except (TypeError, ValueError):
        created_at = 0
    # 超时即作废并在读取时顺带移除：删除是不可逆操作，用户在看到清单后长时间未确认，
    # 期间图库可能已被他人改动，继续按旧快照删除会误删后来上传的图片。
    if time.time() - created_at > CUSTOM_ROLE_DELETE_CONFIRM_SECONDS:
        CUSTOM_ROLE_DELETE_PENDING.pop(key, None)
        return None
    return pending


def _set_pending_custom_role_delete(ev: Event, role_id: str, role_name: str, image_count: int) -> None:
    # 容量上限 1024 与 user_id 键配合，防止异常刷屏使待确认表无界增长；
    # 超出时淘汰最早登记项，被淘汰的用户需重新发起删除。
    if len(CUSTOM_ROLE_DELETE_PENDING) >= 1024:
        oldest_key = min(
            CUSTOM_ROLE_DELETE_PENDING,
            key=lambda key: float(CUSTOM_ROLE_DELETE_PENDING[key].get('created_at') or 0),
        )
        CUSTOM_ROLE_DELETE_PENDING.pop(oldest_key, None)
    # image_count 在此固化，仅用于确认提示；真正删除时以磁盘现状重新统计，
    # 因此提示的数量与实际删除数可能不同（等待期间用户仍可上传图片）。
    CUSTOM_ROLE_DELETE_PENDING[_custom_role_delete_confirm_key(ev)] = {
        'role_id': role_id,
        'role_name': role_name,
        'image_count': image_count,
        'created_at': int(time.time()),
    }


def _clear_pending_custom_role_delete(ev: Event) -> None:
    CUSTOM_ROLE_DELETE_PENDING.pop(_custom_role_delete_confirm_key(ev), None)


def _custom_role_image_entries(role_name: str) -> tuple[str, str, list[tuple[str, Path]]] | None:
    # 此处剥离「老婆」后缀：列表与删除命令的用户输入习惯与创建命令一致。
    role_name = _clean_upload_role_name(role_name, strip_wife_suffix=True)
    # 空名或查无此角色时返回 None，由调用方转为用户提示；本函数不产生文案。
    if not role_name:
        return None
    role_ids = _custom_role_ids_for_name(role_name)
    if not role_ids:
        return None
    role_id = role_ids[0]
    image_map = _custom_role_image_map(role_id)
    # 按文件名排序而非哈希顺序：哈希无序，用户每次查看列表都会得到不同的排列，
    # 无法据此核对哪张图对应哪个 ID。
    return role_id, role_name, sorted(image_map.items(), key=lambda item: item[1].name.lower())


def _resolve_custom_role_for_delete(role_name: str) -> tuple[str, str, list[tuple[str, Path]], str | None]:
    entries = _custom_role_image_entries(role_name)
    if entries is None:
        cleaned = _clean_upload_role_name(role_name, strip_wife_suffix=True)
        # 回报清理后的名称：用户输入的原始名可能带引号或空白，回显它会让提示显得不符。
        return '', cleaned, [], f'未找到自定义老婆【{cleaned or role_name}】。'
    role_id, role_name, images = entries
    return role_id, role_name, images, None


def _delete_custom_role(role_id: str) -> int:
    role_dir = _writable_role_pile_root() / str(role_id)
    # 先统计再删除：目录一旦移除就无法再得知图片数量，而返回值要用于结果提示。
    image_count = len(_custom_role_image_map(role_id))
    if role_dir.is_dir():
        shutil.rmtree(role_dir)
    # 目录与对照表条目必须一并清理；只删其中一项会留下「有表无图」的空角色，
    # 使其在列表中可见但抽取时无图可用。
    _remove_custom_role_map_ids((str(role_id),))
    _invalidate_candidate_cache()
    return image_count


def _resolve_custom_image_for_delete(role_name: str, hash_id: str) -> tuple[str, str, Path | None, str | None]:
    role_name = _clean_upload_role_name(role_name, strip_wife_suffix=True)
    hash_id = str(hash_id or '').strip().lower()
    if not role_name:
        return '', '', None, '请输入角色名，例如：老婆删除图片达妮娅 abcd1234'
    # 严格限定为 8 位十六进制：该值随后仅用作字典键，此处收紧格式可避免任意字符串
    # 进入查找流程，也顺带拦截路径样式输入。
    if not re.fullmatch(r'[0-9a-f]{8}', hash_id):
        return '', role_name, None, '图片ID格式错误，请使用列表里显示的 8 位 ID。'

    role_ids = _custom_role_ids_for_name(role_name)
    if not role_ids:
        return '', role_name, None, f'未找到自定义老婆【{role_name}】。'

    role_id = role_ids[0]
    image_map = _custom_role_image_map(role_id)
    # 路径一律取自磁盘扫描结果，绝不按用户输入拼接：即使校验被绕过，
    # 也只能删除该角色目录内已存在的文件。
    image_path = image_map.get(hash_id)
    if image_path is None:
        return role_id, role_name, None, f'【{role_name}】未找到图片ID：{hash_id}'
    return role_id, role_name, image_path, None


def _parse_delete_custom_image_text(text: str) -> tuple[str, str]:
    # 从末尾提取 8 位十六进制 ID，其余部分视为角色名：角色名可能含空格，
    # 而 ID 位置固定，从尾部匹配比按空格切分更稳妥。
    raw = str(text or '').strip()
    match = re.search(r'([0-9a-fA-F]{8})\s*$', raw)
    if not match:
        # 没有 ID 时整体当作角色名，交由调用方给出格式提示。
        return _clean_upload_role_name(raw, strip_wife_suffix=True), ''
    hash_id = match.group(1).lower()
    role_name = _clean_upload_role_name(raw[: match.start()], strip_wife_suffix=True)
    return role_name, hash_id


def _save_upload_image_ref(role_dir: Path, role_id: str, source: str, index: int) -> Path | None:
    image_data = _read_upload_image_bytes(source)
    if image_data is None:
        return None
    data, suffix = image_data
    # 目录按需创建：角色可能由上传命令自动创建，此时目录状态不由调用方保证。
    role_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_upload_image_path(role_dir, role_id, suffix, index)
    path.write_bytes(data)
    return path



async def _send_create_custom_wife_role(bot: Bot, ev: Event) -> list[str] | None:
    # 创建涉及读改写对照表与磁盘建目录，属阻塞操作，必须投递到专用线程池，
    # 否则会冻结事件循环。
    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    role_id, created, error = await run_blocking(
        _create_or_get_custom_role,
        role_name,
    )
    if error:
        return await _safe_send(bot,error)

    # 用 created 区分「新建」与「已存在」：用户需要知道自己输入的名字是否被复用，
    # 否则会误以为每次命令都创建了新角色。
    if created:
        await _safe_send(bot,f'自定义老婆创建成功\n角色ID：{role_id}')
    else:
        await _safe_send(bot,f'自定义老婆已存在\n角色ID：{role_id}')


async def _send_upload_custom_wife_images(bot: Bot, ev: Event) -> list[str] | None:
    # 权限校验先于任何解析与落盘：上传会在用户目录写入文件，
    # 越权请求必须在产生副作用之前被拒绝。
    if not _can_upload_images(ev):
        return await _safe_send(bot, '你不在图片上传白名单中。')

    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    if not role_name:
        return await _safe_send(bot, '请输入角色名，例如：上传老婆图片 达妮娅，并附带图片。')

    image_refs = _upload_image_refs(ev)
    if not image_refs:
        return await _safe_send(bot, f'请同时发送图片和命令，例如：上传老婆图片 {role_name}')

    # 先取用图片再创建角色：若消息里根本没有图片，不应留下一个空角色。
    role_id, created, error = await run_blocking(
        _create_or_get_custom_role,
        role_name,
    )
    if error:
        return await _safe_send(bot,error)

    role_dir = _writable_role_pile_root() / role_id
    saved: list[Path] = []
    failed = 0
    # 逐张保存并累计失败数，而不是遇到失败即中止：一条消息可能同时含多张图，
    # 其中个别图片读取失败不应导致已成功写入的图片被回滚或丢弃。
    for index, image_ref in enumerate(image_refs, 1):
        path = await run_blocking(_save_upload_image_ref, role_dir, role_id, image_ref, index)
        if path is None:
            failed += 1
        else:
            saved.append(path)

    if not saved:
        return await _safe_send(bot,f'【{role_name}】上传图片失败，请确认消息里附带的是图片。')

    # 新图片可能改变该角色的候选集合，必须失效缓存才会在后续抽卡中生效。
    _invalidate_candidate_cache()
    created_text = '（已自动创建角色）' if created else ''
    # 回显图片 ID 而非路径：ID 是后续删除操作的入参，用户无法从磁盘路径推导出它。
    success_ids = [_custom_image_hash_id(path) for path in saved]
    msg = [
        f'【{role_name}】上传老婆图片成功{created_text}',
        f'角色ID：{role_id}',
        f'成功：{len(saved)} 张',
        f'图片ID：{", ".join(success_ids)}',
    ]
    # 仅在确有失败时才追加该行：成功路径下不展示 0 张失败，避免提示噪音。
    if failed:
        msg.append(f'失败：{failed} 张')
    await _safe_send(bot,'\n'.join(msg))


async def _send_custom_wife_image_list(bot: Bot, ev: Event) -> list[str] | None:
    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    # 目录扫描与哈希计算均为阻塞操作，放进线程池执行。
    entries = await run_blocking(_custom_role_image_entries, role_name)
    if entries is None:
        return await _safe_send(bot, '未找到这个自定义老婆，请先使用：创建老婆 角色名')

    role_id, role_name, images = entries
    if not images:
        return await _safe_send(bot,f'自定义老婆【{role_name}】暂未上传过图片。')

    # 以合并转发消息返回：图片较多时逐条发送会刷屏，且 ID 与图片需要成对呈现。
    nodes: list[Message | str] = []
    for hash_id, path in images:
        nodes.append(f'{role_name} 老婆图片ID：{hash_id}')
        nodes.append(await _image_message_from_path(path))
    await _safe_send(bot, MessageSegment.node(nodes))


async def _send_request_delete_custom_wife_role(bot: Bot, ev: Event) -> list[str] | None:
    # 正则命令已提取角色名，缺省时回落到全文，兼容「删除老婆 xxx」这类非正则触发路径。
    role_name = _clean_upload_role_name(ev.regex_dict.get('role') or ev.text, strip_wife_suffix=True)
    role_id, role_name, images, error = await run_blocking(
        _resolve_custom_role_for_delete,
        role_name,
    )
    if error:
        return await _safe_send(bot, error)

    # 删除不可逆，先登记待确认项并展示清单，由用户二次确认后才真正落盘。
    _set_pending_custom_role_delete(ev, role_id, role_name, len(images))
    await _safe_send(
        bot,
        f'将删除自定义老婆【{role_name}】\n'
        f'角色ID：{role_id}\n'
        f'图片数量：{len(images)}\n'
        f'确认删除请在 {CUSTOM_ROLE_DELETE_CONFIRM_SECONDS} 秒内发送：确认删除老婆\n'
        f'取消请发送：取消删除老婆',
    )


async def _send_confirm_delete_custom_wife_role(bot: Bot, ev: Event) -> list[str] | None:
    pending = _get_pending_custom_role_delete(ev)
    if pending is None:
        return await _safe_send(bot, '没有待确认删除的自定义老婆。')

    role_id = str(pending.get('role_id') or '')
    role_name = str(pending.get('role_name') or role_id)
    # 缺少 role_id 说明待确认项结构异常，此时清掉它并提示重新发起，
    # 而不是尝试删除空 ID 对应的路径。
    if not role_id:
        _clear_pending_custom_role_delete(ev)
        return await _safe_send(bot, '待删除记录无效，请重新发起删除。')

    # 实际删除数取自磁盘现状，与提示中的 image_count 可能不同（等待期间可能新增图片）。
    deleted_count = await run_blocking(_delete_custom_role, role_id)
    _clear_pending_custom_role_delete(ev)
    await _safe_send(bot, f'已删除自定义老婆【{role_name}】\n角色ID：{role_id}\n删除图片：{deleted_count} 张')


async def _send_cancel_delete_custom_wife_role(bot: Bot, ev: Event) -> list[str] | None:
    # 与拒绝赠送同理：无待确认项时不提示「已取消」，避免探测他人操作。
    if _get_pending_custom_role_delete(ev) is None:
        return await _safe_send(bot, '没有待取消的自定义老婆删除。')
    _clear_pending_custom_role_delete(ev)
    await _safe_send(bot, '已取消删除自定义老婆。')


async def _send_delete_custom_wife_image(bot: Bot, ev: Event) -> list[str] | None:
    role_name, hash_id = _parse_delete_custom_image_text(ev.text)
    role_id, role_name, image_path, error = await run_blocking(
        _resolve_custom_image_for_delete,
        role_name,
        hash_id,
    )
    if error:
        return await _safe_send(bot,error)
    if image_path is None:
        return await _safe_send(bot,f'【{role_name}】未找到图片ID：{hash_id}')

    try:
        await run_blocking(image_path.unlink)
    except OSError as exc:
        # 文件可能已被其它进程删除或权限不足；此处降级为提示而不抛出，
        # 因为对照表未受影响，角色仍可用。
        logger.warning(f'{LOG_PREFIX} 删除自定义老婆图片失败: {image_path} -> {exc}')
        return await _safe_send(bot,f'【{role_name}】图片删除失败：{hash_id}')

    # 图片集合已变化，失效候选缓存后下次抽卡才会读到新的图库。
    _invalidate_candidate_cache()
    await _safe_send(bot,f'已删除【{role_name}】老婆图片：{hash_id}')


# 以下命令注册共用同一批处理函数，差异仅在于触发词与是否走正则：
# 正则形式用于从「删除老婆 <角色名>」中提取角色名，前缀形式则用于其余固定语法。
@custom_role_sv.on_prefix(('创建老婆', '老婆创建'), block=True)
async def custom_wife_create(bot: Bot, ev: Event) -> None:
    await _send_create_custom_wife_role(bot, ev)


@image_upload_sv.on_prefix(('上传老婆图片', '老婆上传图片'), block=True)
async def custom_wife_upload(bot: Bot, ev: Event) -> None:
    await _send_upload_custom_wife_images(bot, ev)


@custom_role_sv.on_prefix(('查看老婆图片', '老婆图片列表', '老婆图片'), block=True)
async def custom_wife_image_list(bot: Bot, ev: Event) -> None:
    await _send_custom_wife_image_list(bot, ev)


@custom_role_sv.on_prefix(('删除老婆图片', '老婆删除图片', '老婆删图片'), block=True)
async def custom_wife_delete_image(bot: Bot, ev: Event) -> None:
    await _send_delete_custom_wife_image(bot, ev)


@custom_role_sv.on_fullmatch(('确认删除老婆', '老婆删除确认'), block=True)
async def custom_wife_confirm_delete(bot: Bot, ev: Event) -> None:
    await _send_confirm_delete_custom_wife_role(bot, ev)


@custom_role_sv.on_fullmatch(('取消删除老婆', '老婆删除取消'), block=True)
async def custom_wife_cancel_delete(bot: Bot, ev: Event) -> None:
    await _send_cancel_delete_custom_wife_role(bot, ev)


@custom_role_sv.on_regex(r'^(?:删除老婆|老婆删除)(?!图片|确认|取消)(?P<role>.+)$', block=True)
async def custom_wife_delete_role(bot: Bot, ev: Event) -> None:
    # 负向先行断言排除「删除老婆图片/确认/取消」：这三者由更具体的命令处理，
    # 若不排除，它们会被本条正则先行匹配并把「图片」当成角色名。
    await _send_request_delete_custom_wife_role(bot, ev)

