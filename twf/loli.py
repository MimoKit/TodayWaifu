"""TodayWaifu - loli module."""
from __future__ import annotations

from .shared import *  # noqa: F403


# ── 本地图片目录读取 ─────────────────────────────────────────────────────────

def _loli_image_paths() -> tuple[Path, ...]:
    root = _loli_image_root()
    if not root.is_dir():
        return ()
    images = [
        path
        for path in root.rglob('*')
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return tuple(sorted(images, key=lambda p: str(p).lower()))


def _delete_loli_images() -> int:
    root = _loli_image_root()
    count = len(_loli_image_paths())
    if root.exists():
        shutil.rmtree(root) if root.is_dir() else root.unlink()
    return count


# ── 上传辅助 ─────────────────────────────────────────────────────────────────

def _loli_image_hash_id(path: Path | str) -> str:
    return hashlib.sha256(Path(path).name.encode()).hexdigest()[:8]


def _loli_upload_refs(ev: Event) -> tuple[str, ...]:
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


def _detect_image_suffix(data: bytes, source: str) -> str:
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


def _read_loli_image_bytes(source: str) -> tuple[bytes, str] | None:
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
                req = Request(text, headers={'User-Agent': 'Mozilla/5.0'})
                with urlopen(req, timeout=15) as resp:
                    data = resp.read(UPLOAD_IMAGE_MAX_BYTES + 1)
            else:
                path = Path(text)
                if not path.is_file():
                    return None
                data = path.read_bytes()
    except (OSError, ValueError, binascii.Error) as exc:
        logger.warning(f'{LOG_PREFIX} 读取萝莉图片失败: {exc}')
        return None
    if not data or len(data) > UPLOAD_IMAGE_MAX_BYTES:
        return None
    suffix = _detect_image_suffix(data, source)
    if suffix not in IMAGE_EXTENSIONS:
        return None
    return data, suffix


def _unique_loli_path(root: Path, suffix: str, index: int) -> Path:
    stamp = int(time.time() * 1000)
    counter = 0
    while True:
        tail = f'_{counter}' if counter else ''
        path = root / f'loli_{stamp}_{index}{tail}{suffix}'
        if not path.exists():
            return path
        counter += 1


def _save_loli_image(source: str, index: int) -> Path | None:
    result = _read_loli_image_bytes(source)
    if result is None:
        return None
    data, suffix = result
    root = _loli_image_root()
    root.mkdir(parents=True, exist_ok=True)
    path = _unique_loli_path(root, suffix, index)
    path.write_bytes(data)
    return path


def _loli_image_map() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in _loli_image_paths():
        result[_loli_image_hash_id(path)] = path
    return result


# ── 命令处理 ─────────────────────────────────────────────────────────────────

def _loli_record_name(image: str) -> str:
    return f'萝莉图{_loli_image_hash_id(image)}'


async def _send_loli_record(bot: Bot, record: WifeRecord, text: str = '你今天的萝莉来啦！') -> None:
    await _safe_send(bot, [_with_loli_reply_prefix(text), MessageSegment.image(record.image)])


async def _send_loli_image(bot: Bot, ev: Event) -> None:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    user_key = _user_key(ev)
    current = context['lolis'].get(user_key)
    if isinstance(current, dict):
        state = _wife_state(current)
        if state == 'lost_stolen':
            stolen_by_name = current.get('stolen_by_name') or current.get('stolen_by')
            return await _send_loli_text(bot, f'你的萝莉已经被{stolen_by_name}抢走了，今天就先忍忍吧~')
        if state == 'lost_gifted':
            gifted_to_name = current.get('gifted_to_name') or current.get('gifted_to')
            return await _send_loli_text(bot, f'你的萝莉已经送给{gifted_to_name}了，今天就先忍忍吧~')
        if state == 'divorced':
            return await _send_loli_text(bot, '你今天已经和萝莉离婚了，明天再来吧~')
        record = _record_from_dict(current)
        if record is not None:
            return await _send_loli_record(bot, record)

    custom_url = str(_cfg('DailyWifeLoliApiUrl') or '').strip()
    if custom_url:
        logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日萝莉，接口: {custom_url}')
        try:
            data = await asyncio.to_thread(lambda: _http_get(custom_url, timeout=15))
        except Exception:
            return await _send_loli_text(bot, '暂无图片')
        record = WifeRecord(
            name='萝莉',
            role_ids=('接口',),
            image=custom_url,
            record_type='loli',
        )
        save_data = _load_wife_data()
        save_context = _get_today_context(save_data, ev)
        save_context['lolis'][user_key] = _record_to_dict(record, ev, user_key)
        _save_wife_data(save_data)
        await _safe_send(bot, [_with_loli_reply_prefix('你今天的萝莉是'), MessageSegment.image(data)])
        return

    images = _loli_image_paths()
    if not images:
        return await _send_loli_text(bot, '暂无图片')
    image = random.choice(images)
    logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日萝莉，发送本地图片: {image}')
    record = WifeRecord(
        name=_loli_record_name(str(image)),
        role_ids=(_loli_image_hash_id(image),),
        image=str(image),
        record_type='loli',
    )
    save_data = _load_wife_data()
    save_context = _get_today_context(save_data, ev)
    save_context['lolis'][user_key] = _record_to_dict(record, ev, user_key)
    _save_wife_data(save_data)
    await _send_loli_record(bot, record)


async def _send_upload_loli(bot: Bot, ev: Event) -> None:
    refs = _loli_upload_refs(ev)
    if not refs:
        return await _send_loli_text(bot, '请同时发送图片和命令，例如：今日萝莉上传 [图片]')

    saved: list[Path] = []
    failed = 0
    for i, ref in enumerate(refs, 1):
        path = await asyncio.to_thread(_save_loli_image, ref, i)
        if path is None:
            failed += 1
        else:
            saved.append(path)

    if not saved:
        return await _send_loli_text(bot, '上传失败，请确认消息里附带的是图片。')

    ids = [_loli_image_hash_id(p) for p in saved]
    lines = [f'萝莉图片上传成功，共 {len(saved)} 张', f'图片ID：{", ".join(ids)}']
    if failed:
        lines.append(f'失败：{failed} 张')
    await _send_loli_text(bot, '\n'.join(lines))


async def _send_loli_image_list(bot: Bot, ev: Event) -> None:
    image_map = _loli_image_map()
    if not image_map:
        return await _send_loli_text(bot, '本地还没有萝莉图片，使用「今日萝莉上传」上传图片。')
    nodes: list[Any] = []
    for hash_id, path in image_map.items():
        nodes.append(f'萝莉图片ID：{hash_id}')
        nodes.append(MessageSegment.image(path))
    await _safe_send(bot, MessageSegment.node(nodes))


async def _send_delete_loli(bot: Bot, ev: Event) -> None:
    hash_id = str(ev.text or '').strip().lower()
    if not hash_id:
        logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 触发删除全部萝莉图片命令')
        count = await asyncio.to_thread(_delete_loli_images)
        return await _send_loli_text(bot, f'已删除全部萝莉图片，共 {count} 张。')
    if not re.fullmatch(r'[0-9a-f]{8}', hash_id):
        return await _send_loli_text(bot, '请提供 8 位图片ID，例如：删除萝莉图片 abcd1234\n不加ID则删除全部')
    image_map = _loli_image_map()
    path = image_map.get(hash_id)
    if path is None:
        return await _send_loli_text(bot, f'未找到图片ID：{hash_id}')
    try:
        path.unlink()
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 删除萝莉图片失败: {path} -> {exc}')
        return await _send_loli_text(bot, f'删除失败：{hash_id}')
    await _send_loli_text(bot, f'已删除萝莉图片：{hash_id}')


# ── 触发器注册 ────────────────────────────────────────────────────────────────

@loli_sv.on_fullmatch(
    '今日萝莉',
    block=True,
    to_ai="""随机抽取当前用户今天的萝莉图片。
    当用户说“今日萝莉”“抽一张萝莉”“我今天的萝莉是谁”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_loli(bot: Bot, ev: Event):
    await _send_loli_image(bot, ev)


@loli_manage_sv.on_command(('今日萝莉上传', '萝莉上传图片'), block=True)
async def upload_loli(bot: Bot, ev: Event):
    await _send_upload_loli(bot, ev)


@loli_manage_sv.on_fullmatch(
    ('今日萝莉列表', '萝莉图片列表'),
    block=True,
    to_ai="""查看今日萝莉图库列表。
    当用户说“今日萝莉列表”“萝莉图片列表”“有哪些萝莉图”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def list_loli(bot: Bot, ev: Event):
    await _send_loli_image_list(bot, ev)


@loli_manage_sv.on_command('删除萝莉图片', block=True)
async def delete_loli(bot: Bot, ev: Event):
    await _send_delete_loli(bot, ev)
