"""TodayWaifu - custom_role module."""
from __future__ import annotations

from .shared import *  # noqa: F403


def _role_ids_by_name(role_map: dict[str, str], role_name: str) -> tuple[str, ...]:
    target = _normalize_role_name(role_name)
    ids = [role_id for role_id, name in role_map.items() if _normalize_role_name(name) == target]
    return tuple(sorted(ids, key=lambda item: int(item) if item.isdigit() else item))


def _next_custom_role_id(role_map: dict[str, str], pile_root: Path) -> str:
    used_ids: set[int] = set()
    for role_id in role_map:
        if role_id.isdigit():
            used_ids.add(int(role_id))
    if pile_root.is_dir():
        for item in pile_root.iterdir():
            if item.is_dir() and item.name.isdigit():
                used_ids.add(int(item.name))

    role_id = max([CUSTOM_ROLE_ID_START - 1, *(item for item in used_ids if item >= CUSTOM_ROLE_ID_START)]) + 1
    while role_id in used_ids:
        role_id += 1
    return str(role_id)


def _append_role_map_line(path: Path, role_id: str, role_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding='utf-8') if path.is_file() else ''
    prefix = '' if not text or text.endswith('\n') else '\n'
    path.write_text(f'{text}{prefix}{role_id}：{role_name}\n', encoding='utf-8')


def _clean_upload_role_name(raw: str, strip_wife_suffix: bool = False) -> str:
    name = str(raw or '').strip().strip('"“”‘’')
    name = re.sub(r'\s+', ' ', name)
    if strip_wife_suffix and name.endswith('老婆') and len(name) > 2:
        name = name[:-2].strip()
    return name


def _create_or_get_custom_role(role_name: str) -> tuple[str, bool, str | None]:
    role_name = _clean_upload_role_name(role_name)
    if not role_name:
        return '', False, '请输入角色名，例如：老婆创建达妮娅老婆'

    map_path = _writable_role_map_path()
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    pile_root = _writable_role_pile_root()
    role_ids = _role_ids_by_name(role_map, role_name)
    if role_ids:
        role_id = role_ids[0]
        (pile_root / role_id).mkdir(parents=True, exist_ok=True)
        return role_id, False, None

    role_id = _next_custom_role_id(role_map, pile_root)
    _append_role_map_line(map_path, role_id, role_name)
    (pile_root / role_id).mkdir(parents=True, exist_ok=True)
    _invalidate_candidate_cache()
    logger.info(f'{LOG_PREFIX} 创建自定义老婆角色: {role_name} -> {role_id}')
    return role_id, True, None


def _upload_image_refs(ev: Event) -> tuple[str, ...]:
    refs: list[str] = []
    for content in ev.content or []:
        if content.type in {'image', 'img'} and isinstance(content.data, str):
            ref = content.data.strip()
            if ref:
                refs.append(ref)
    for item in ev.image_list or []:
        if isinstance(item, str) and item.strip():
            refs.append(item.strip())
    if isinstance(ev.image, str) and ev.image.strip():
        refs.append(ev.image.strip())
    return tuple(dict.fromkeys(refs))


def _image_suffix_from_source(source: str) -> str:
    text = str(source or '').strip()
    if text.startswith('link://'):
        text = text[7:]
    try:
        path_text = urlparse(text).path if text.startswith(('http://', 'https://')) else text
        suffix = Path(path_text.split('?', 1)[0]).suffix.lower()
    except Exception:
        suffix = ''
    return suffix if suffix in IMAGE_EXTENSIONS else ''


def _detect_upload_image_suffix(data: bytes, source: str) -> str:
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return '.gif'
    if data.startswith(b'BM'):
        return '.bmp'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    return _image_suffix_from_source(source)


def _read_upload_image_bytes(source: str) -> tuple[bytes, str] | None:
    text = str(source or '').strip()
    if not text:
        return None

    try:
        if text.startswith('data:image/') and ',' in text:
            data = base64.b64decode(text.split(',', 1)[1], validate=False)
        elif text.startswith('base64://'):
            data = base64.b64decode(text[9:], validate=False)
        else:
            if text.startswith('link://'):
                text = text[7:]
            if text.startswith(('http://', 'https://')):
                request = Request(text, headers={'User-Agent': 'Mozilla/5.0'})
                with urlopen(request, timeout=15) as response:
                    data = response.read(UPLOAD_IMAGE_MAX_BYTES + 1)
            else:
                path = Path(text)
                if not path.is_file():
                    return None
                data = path.read_bytes()
    except (OSError, ValueError, binascii.Error) as exc:
        logger.warning(f'{LOG_PREFIX} 读取上传图片失败: {exc}')
        return None

    if not data or len(data) > UPLOAD_IMAGE_MAX_BYTES:
        return None
    suffix = _detect_upload_image_suffix(data, source)
    if suffix not in IMAGE_EXTENSIONS:
        return None
    return data, suffix


def _unique_upload_image_path(role_dir: Path, role_id: str, suffix: str, index: int) -> Path:
    stamp = int(time.time() * 1000)
    counter = 0
    while True:
        tail = f'_{counter}' if counter else ''
        path = role_dir / f'{role_id}_{stamp}_{index}{tail}{suffix}'
        if not path.exists():
            return path
        counter += 1


def _custom_image_hash_id(path: Path | str) -> str:
    return hashlib.sha256(Path(path).name.encode()).hexdigest()[:8]


def _custom_role_image_map(role_id: str) -> dict[str, Path]:
    role_dir = _writable_role_pile_root() / str(role_id)
    if not role_dir.is_dir():
        return {}
    result: dict[str, Path] = {}
    for path in sorted(role_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            result[_custom_image_hash_id(path)] = path
    return result


def _custom_role_ids_for_name(role_name: str) -> tuple[str, ...]:
    map_path = _writable_role_map_path()
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    return _role_ids_by_name(role_map, role_name)


def _custom_role_name_by_id(role_id: str) -> str:
    map_path = _writable_role_map_path()
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    return role_map.get(str(role_id), str(role_id))


def _remove_custom_role_map_ids(role_ids: tuple[str, ...]) -> None:
    if not role_ids:
        return
    map_path = _writable_role_map_path()
    if not map_path.is_file():
        return
    role_id_set = {str(role_id) for role_id in role_ids}
    kept: list[str] = []
    for line in map_path.read_text(encoding='utf-8').splitlines():
        match = ROLE_MAP_RE.match(line)
        if match and match.group(1) in role_id_set:
            continue
        kept.append(line)
    text = '\n'.join(kept)
    if text:
        text += '\n'
    map_path.write_text(text, encoding='utf-8')


def _custom_role_delete_confirm_key(ev: Event) -> str:
    return f'{_context_key(ev)}:{_user_key(ev)}'


def _get_pending_custom_role_delete(ev: Event) -> dict[str, Any] | None:
    key = _custom_role_delete_confirm_key(ev)
    pending = CUSTOM_ROLE_DELETE_PENDING.get(key)
    if not isinstance(pending, dict):
        return None
    try:
        created_at = int(pending.get('created_at') or 0)
    except (TypeError, ValueError):
        created_at = 0
    if time.time() - created_at > CUSTOM_ROLE_DELETE_CONFIRM_SECONDS:
        CUSTOM_ROLE_DELETE_PENDING.pop(key, None)
        return None
    return pending


def _set_pending_custom_role_delete(ev: Event, role_id: str, role_name: str, image_count: int) -> None:
    CUSTOM_ROLE_DELETE_PENDING[_custom_role_delete_confirm_key(ev)] = {
        'role_id': role_id,
        'role_name': role_name,
        'image_count': image_count,
        'created_at': int(time.time()),
    }


def _clear_pending_custom_role_delete(ev: Event) -> None:
    CUSTOM_ROLE_DELETE_PENDING.pop(_custom_role_delete_confirm_key(ev), None)


def _custom_role_image_entries(role_name: str) -> tuple[str, str, list[tuple[str, Path]]] | None:
    role_name = _clean_upload_role_name(role_name, strip_wife_suffix=True)
    if not role_name:
        return None
    role_ids = _custom_role_ids_for_name(role_name)
    if not role_ids:
        return None
    role_id = role_ids[0]
    image_map = _custom_role_image_map(role_id)
    return role_id, role_name, sorted(image_map.items(), key=lambda item: item[1].name.lower())


def _resolve_custom_role_for_delete(role_name: str) -> tuple[str, str, list[tuple[str, Path]], str | None]:
    entries = _custom_role_image_entries(role_name)
    if entries is None:
        cleaned = _clean_upload_role_name(role_name, strip_wife_suffix=True)
        return '', cleaned, [], f'未找到自定义老婆【{cleaned or role_name}】。'
    role_id, role_name, images = entries
    return role_id, role_name, images, None


def _delete_custom_role(role_id: str) -> int:
    role_dir = _writable_role_pile_root() / str(role_id)
    image_count = len(_custom_role_image_map(role_id))
    if role_dir.is_dir():
        shutil.rmtree(role_dir)
    _remove_custom_role_map_ids((str(role_id),))
    _invalidate_candidate_cache()
    return image_count


def _resolve_custom_image_for_delete(role_name: str, hash_id: str) -> tuple[str, str, Path | None, str | None]:
    role_name = _clean_upload_role_name(role_name, strip_wife_suffix=True)
    hash_id = str(hash_id or '').strip().lower()
    if not role_name:
        return '', '', None, '请输入角色名，例如：老婆删除图片达妮娅 abcd1234'
    if not re.fullmatch(r'[0-9a-f]{8}', hash_id):
        return '', role_name, None, '图片ID格式错误，请使用列表里显示的 8 位 ID。'

    role_ids = _custom_role_ids_for_name(role_name)
    if not role_ids:
        return '', role_name, None, f'未找到自定义老婆【{role_name}】。'

    role_id = role_ids[0]
    image_map = _custom_role_image_map(role_id)
    image_path = image_map.get(hash_id)
    if image_path is None:
        return role_id, role_name, None, f'【{role_name}】未找到图片ID：{hash_id}'
    return role_id, role_name, image_path, None


def _parse_delete_custom_image_text(text: str) -> tuple[str, str]:
    raw = str(text or '').strip()
    match = re.search(r'([0-9a-fA-F]{8})\s*$', raw)
    if not match:
        return _clean_upload_role_name(raw, strip_wife_suffix=True), ''
    hash_id = match.group(1).lower()
    role_name = _clean_upload_role_name(raw[: match.start()], strip_wife_suffix=True)
    return role_name, hash_id


def _save_upload_image_ref(role_dir: Path, role_id: str, source: str, index: int) -> Path | None:
    image_data = _read_upload_image_bytes(source)
    if image_data is None:
        return None
    data, suffix = image_data
    role_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_upload_image_path(role_dir, role_id, suffix, index)
    path.write_bytes(data)
    return path


async def _send_create_custom_wife_role(bot: Bot, ev: Event):
    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    role_id, created, error = _create_or_get_custom_role(role_name)
    if error:
        return await _send_prefixed(bot,error)

    if created:
        await _send_prefixed(bot,f'自定义老婆创建成功\n角色ID：{role_id}')
    else:
        await _send_prefixed(bot,f'自定义老婆已存在\n角色ID：{role_id}')


async def _send_upload_custom_wife_images(bot: Bot, ev: Event):
    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    if not role_name:
        return await _send_prefixed(bot,'请输入角色名，例如：老婆上传图片达妮娅 并附带图片')

    image_refs = _upload_image_refs(ev)
    if not image_refs:
        return await _send_prefixed(bot,f'请同时发送图片及命令，例如：老婆上传图片{role_name}')

    role_id, created, error = _create_or_get_custom_role(role_name)
    if error:
        return await _send_prefixed(bot,error)

    role_dir = _writable_role_pile_root() / role_id
    saved: list[Path] = []
    failed = 0
    for index, image_ref in enumerate(image_refs, 1):
        path = await asyncio.to_thread(_save_upload_image_ref, role_dir, role_id, image_ref, index)
        if path is None:
            failed += 1
        else:
            saved.append(path)

    if not saved:
        return await _send_prefixed(bot,f'【{role_name}】上传图片失败，请确认消息里附带的是图片。')

    _invalidate_candidate_cache()
    created_text = '（已自动创建角色）' if created else ''
    success_ids = [_custom_image_hash_id(path) for path in saved]
    msg = [
        f'【{role_name}】上传老婆图片成功{created_text}',
        f'角色ID：{role_id}',
        f'成功：{len(saved)} 张',
        f'图片ID：{", ".join(success_ids)}',
    ]
    if failed:
        msg.append(f'失败：{failed} 张')
    await _send_prefixed(bot,'\n'.join(msg))


async def _send_custom_wife_image_list(bot: Bot, ev: Event):
    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    entries = _custom_role_image_entries(role_name)
    if entries is None:
        return await _send_prefixed(bot,'未找到这个自定义老婆，请先使用：老婆创建角色名老婆')

    role_id, role_name, images = entries
    if not images:
        return await _send_prefixed(bot,f'自定义老婆【{role_name}】暂未上传过图片。')

    nodes: list[Any] = []
    for hash_id, path in images:
        nodes.append(f'{role_name} 老婆图片ID：{hash_id}')
        nodes.append(MessageSegment.image(path))
    await _send_prefixed(bot, MessageSegment.node(nodes))


async def _send_request_delete_custom_wife_role(bot: Bot, ev: Event):
    role_name = _clean_upload_role_name(ev.regex_dict.get('role') or ev.text, strip_wife_suffix=True)
    role_id, role_name, images, error = _resolve_custom_role_for_delete(role_name)
    if error:
        return await _send_prefixed(bot, error)

    _set_pending_custom_role_delete(ev, role_id, role_name, len(images))
    await _send_prefixed(
        bot,
        f'将删除自定义老婆【{role_name}】\n'
        f'角色ID：{role_id}\n'
        f'图片数量：{len(images)}\n'
        f'确认删除请在 {CUSTOM_ROLE_DELETE_CONFIRM_SECONDS} 秒内发送：老婆删除确认\n'
        f'取消请发送：老婆删除取消',
    )


async def _send_confirm_delete_custom_wife_role(bot: Bot, ev: Event):
    pending = _get_pending_custom_role_delete(ev)
    if pending is None:
        return await _send_prefixed(bot, '没有待确认删除的自定义老婆。')

    role_id = str(pending.get('role_id') or '')
    role_name = str(pending.get('role_name') or role_id)
    if not role_id:
        _clear_pending_custom_role_delete(ev)
        return await _send_prefixed(bot, '待删除记录无效，请重新发起删除。')

    deleted_count = _delete_custom_role(role_id)
    _clear_pending_custom_role_delete(ev)
    await _send_prefixed(bot, f'已删除自定义老婆【{role_name}】\n角色ID：{role_id}\n删除图片：{deleted_count} 张')


async def _send_cancel_delete_custom_wife_role(bot: Bot, ev: Event):
    if _get_pending_custom_role_delete(ev) is None:
        return await _send_prefixed(bot, '没有待取消的自定义老婆删除。')
    _clear_pending_custom_role_delete(ev)
    await _send_prefixed(bot, '已取消删除自定义老婆。')


async def _send_delete_custom_wife_image(bot: Bot, ev: Event):
    role_name, hash_id = _parse_delete_custom_image_text(ev.text)
    role_id, role_name, image_path, error = _resolve_custom_image_for_delete(role_name, hash_id)
    if error:
        return await _send_prefixed(bot,error)
    if image_path is None:
        return await _send_prefixed(bot,f'【{role_name}】未找到图片ID：{hash_id}')

    try:
        image_path.unlink()
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 删除自定义老婆图片失败: {image_path} -> {exc}')
        return await _send_prefixed(bot,f'【{role_name}】图片删除失败：{hash_id}')

    _invalidate_candidate_cache()
    await _send_prefixed(bot,f'已删除【{role_name}】老婆图片：{hash_id}')


@upload_sv.on_prefix('老婆创建', block=True)
async def custom_wife_create(bot: Bot, ev: Event):
    await _send_create_custom_wife_role(bot, ev)


@upload_sv.on_prefix('老婆上传图片', block=True)
async def custom_wife_upload(bot: Bot, ev: Event):
    await _send_upload_custom_wife_images(bot, ev)


@upload_sv.on_prefix(('老婆图片列表', '老婆图片'), block=True)
async def custom_wife_image_list(bot: Bot, ev: Event):
    await _send_custom_wife_image_list(bot, ev)


@upload_sv.on_prefix(('老婆删除图片', '老婆删图片'), block=True)
async def custom_wife_delete_image(bot: Bot, ev: Event):
    await _send_delete_custom_wife_image(bot, ev)


@upload_sv.on_fullmatch('老婆删除确认', block=True)
async def custom_wife_confirm_delete(bot: Bot, ev: Event):
    await _send_confirm_delete_custom_wife_role(bot, ev)


@upload_sv.on_fullmatch('老婆删除取消', block=True)
async def custom_wife_cancel_delete(bot: Bot, ev: Event):
    await _send_cancel_delete_custom_wife_role(bot, ev)


@upload_sv.on_regex(r'^老婆删除(?!图片|确认|取消)(?P<role>.+)$', block=True)
async def custom_wife_delete_role(bot: Bot, ev: Event):
    await _send_request_delete_custom_wife_role(bot, ev)


