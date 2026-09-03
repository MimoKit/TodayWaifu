"""TodayWaifu - loli module."""
from __future__ import annotations

from .shared import *  # noqa: F403
from .image_input import (
    collect_image_refs,
    detect_image_suffix,
    image_hash_id,
    image_suffix_from_source,
    read_image_bytes,
)
from .source_cache import AsyncSourceCache


_LOLI_SOURCE_CACHE = AsyncSourceCache[tuple[str, ...]](CACHE_TTL_SECONDS, max_entries=4)


# ── 本地图片目录读取 ─────────────────────────────────────────────────────────

# 萝莉图片列表缓存：rglob 全量扫描在图多时是昂贵操作，0 点高峰每条指令都扫会拖垮核心
_LOLI_PATHS_CACHE_TTL = 300.0
_loli_paths_cache: tuple[float, tuple[Path, ...]] | None = None


def _invalidate_loli_paths_cache() -> None:
    global _loli_paths_cache
    _loli_paths_cache = None


def _loli_image_paths() -> tuple[Path, ...]:
    global _loli_paths_cache
    now = time.time()
    if _loli_paths_cache is not None and now - _loli_paths_cache[0] < _LOLI_PATHS_CACHE_TTL:
        return _loli_paths_cache[1]
    root = _loli_image_root()
    if not root.is_dir():
        paths: tuple[Path, ...] = ()
    else:
        paths = tuple(
            sorted(
                (
                    path
                    for path in root.rglob('*')
                    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
                ),
                key=lambda p: str(p).lower(),
            )
        )
    _loli_paths_cache = (now, paths)
    return paths


def _delete_loli_images() -> int:
    root = _loli_image_root()
    count = len(_loli_image_paths())
    if root.exists():
        shutil.rmtree(root) if root.is_dir() else root.unlink()
    _invalidate_loli_paths_cache()
    _LOLI_SOURCE_CACHE.invalidate()
    return count


# ── 上传辅助 ─────────────────────────────────────────────────────────────────

def _loli_image_hash_id(path: Path | str) -> str:
    return image_hash_id(path)


def _loli_upload_refs(ev: Event) -> tuple[str, ...]:
    return collect_image_refs(ev)


def _image_suffix_from_source(source: str) -> str:
    return image_suffix_from_source(source)


def _detect_image_suffix(data: bytes, source: str) -> str:
    return detect_image_suffix(data, source)


def _read_loli_image_bytes(source: str) -> tuple[bytes, str] | None:
    return read_image_bytes(source, UPLOAD_IMAGE_MAX_BYTES)


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
    _invalidate_loli_paths_cache()
    return path


def _loli_image_map() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in _loli_image_paths():
        result[_loli_image_hash_id(path)] = path
    return result


# ── 命令处理 ─────────────────────────────────────────────────────────────────

def _loli_record_name(image: str) -> str:
    return f'萝莉图{_loli_image_hash_id(image)}'


def _parse_loli_image_urls(payload: dict[str, Any]) -> tuple[str, ...]:
    if 'roles' not in payload or not isinstance(payload['roles'], list):
        raise RuntimeError('萝莉图库接口缺少 roles 列表。')

    loli_role_found = False
    image_urls: list[str] = []
    seen_urls: set[str] = set()
    for role_data in payload['roles']:
        if not isinstance(role_data, dict):
            raise RuntimeError('萝莉图库接口的 roles 项必须是对象。')
        if 'role_ids' not in role_data or not isinstance(role_data['role_ids'], list):
            raise RuntimeError('萝莉图库接口的 role_ids 必须是列表。')

        role_ids = tuple(str(role_id).strip() for role_id in role_data['role_ids'])
        if 'loli' not in role_ids:
            continue
        loli_role_found = True

        if 'images' not in role_data or not isinstance(role_data['images'], list):
            raise RuntimeError('萝莉图库接口的 images 必须是列表。')
        for image_data in role_data['images']:
            if not isinstance(image_data, dict):
                raise RuntimeError('萝莉图库接口的 images 项必须是对象。')
            if 'url' not in image_data or not isinstance(image_data['url'], str):
                raise RuntimeError('萝莉图库接口的图片缺少 url 字符串。')
            image_url = image_data['url'].strip()
            if not image_url.startswith(('http://', 'https://')):
                raise RuntimeError('萝莉图库接口返回了无效的图片 URL。')
            if image_url not in seen_urls:
                seen_urls.add(image_url)
                image_urls.append(image_url)

    if not loli_role_found:
        raise RuntimeError('萝莉图库接口缺少 role_ids=["loli"] 的角色项。')
    if not image_urls:
        raise RuntimeError('萝莉图库接口没有可用图片。')
    return tuple(image_urls)


def _fetch_loli_image_urls_sync(api_url: str) -> tuple[str, ...]:
    try:
        body = _http_get_with_retry(api_url, timeout=15)
    except HTTPError as exc:
        raise RuntimeError(f'请求萝莉图库接口失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'请求萝莉图库接口失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('请求萝莉图库接口超时。') from exc
    except OSError as exc:
        raise RuntimeError(f'请求萝莉图库接口失败：{exc}') from exc

    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('萝莉图库接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('萝莉图库接口返回格式不正确。')
    return _parse_loli_image_urls(payload)


def _is_legacy_remote_loli_record(record: WifeRecord) -> bool:
    return record.record_type == 'loli' and record.role_ids == ('接口',)


def _loli_unavailable_text(record_data: dict[str, Any]) -> str | None:
    state = _wife_state(record_data)
    if state == 'lost_stolen':
        robber = record_data['stolen_by'] if 'stolen_by' in record_data else ''
        if 'stolen_by_name' in record_data and record_data['stolen_by_name']:
            robber = record_data['stolen_by_name']
        return f'你的萝莉已经被{robber}抢走了，今天就先忍忍吧~'
    if state == 'lost_gifted':
        receiver = record_data['gifted_to'] if 'gifted_to' in record_data else ''
        if 'gifted_to_name' in record_data and record_data['gifted_to_name']:
            receiver = record_data['gifted_to_name']
        return f'你的萝莉已经送给{receiver}了，今天就先忍忍吧~'
    if state == 'divorced':
        return '你今天已经和萝莉离婚了，明天再来吧~'
    return None


async def _roll_loli_record(
    ev: Event,
    user_key: str,
) -> tuple[WifeRecord | None, str | None]:
    custom_url = str(_cfg('DailyWifeLoliApiUrl') or '').strip()
    remote_error: str | None = None
    if custom_url:
        logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日萝莉列表，接口: {custom_url}')
        try:
            image_urls = await _LOLI_SOURCE_CACHE.get(
                custom_url,
                lambda: asyncio.to_thread(_fetch_loli_image_urls_sync, custom_url),
            )
        except RuntimeError as exc:
            remote_error = str(exc)
            logger.warning(f'{LOG_PREFIX} 远程萝莉接口失败，回退本地图片: {exc}')
        else:
            image_url = _daily_rng(ev, user_key, 'loli').choice(image_urls)
            return (
                WifeRecord(
                    name=_loli_record_name(image_url),
                    role_ids=('loli',),
                    image=image_url,
                    record_type='loli',
                ),
                None,
            )

    images = await asyncio.to_thread(_loli_image_paths)
    if not images:
        return None, remote_error or '暂无图片'
    image = _daily_rng(ev, user_key, 'loli').choice(images)
    logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日萝莉，选中本地图片: {image}')
    return (
        WifeRecord(
            name=_loli_record_name(str(image)),
            role_ids=(_loli_image_hash_id(image),),
            image=str(image),
            record_type='loli',
        ),
        None,
    )


async def _send_loli_record(
    bot: Bot,
    ev: Event,
    record: WifeRecord,
    text: str = '你今天的萝莉来啦！',
) -> None:
    await _send_loli_result_image(
        bot,
        record.image,
        text,
        ev.user_id,
        ev.group_id is not None,
    )


async def _send_loli_image(bot: Bot, ev: Event) -> None:
    context = await _load_daily_context(ev)
    user_key = _user_key(ev)
    current = context['lolis'][user_key] if user_key in context['lolis'] else None
    if isinstance(current, dict):
        unavailable_text = _loli_unavailable_text(current)
        if unavailable_text is not None:
            return await _send_loli_text(bot, unavailable_text)
        record = _record_from_dict(current)
        if record is not None and not _is_legacy_remote_loli_record(record):
            return await _send_loli_record(bot, ev, record)
        if record is not None:
            logger.warning(
                f'{LOG_PREFIX} 用户 {user_key} 的旧版萝莉记录只保存了随机接口地址，'
                '将迁移为当天固定图片 URL'
            )

    record, error = await _roll_loli_record(ev, user_key)
    if record is None:
        return await _send_loli_text(bot, error or '暂无图片')

    # 网络请求期间可能有其它协程完成写入，因此保存前持锁重新加载并复用最新记录。
    response_text: str | None = None
    selected_record = record
    async with _daily_context_lock(ev):
        save_context = await _load_daily_context(ev)
        existing = (
            save_context['lolis'][user_key]
            if user_key in save_context['lolis']
            else None
        )
        if isinstance(existing, dict):
            unavailable_text = _loli_unavailable_text(existing)
            if unavailable_text is not None:
                response_text = unavailable_text
            else:
                existing_record = _record_from_dict(existing)
                if (
                    existing_record is not None
                    and not _is_legacy_remote_loli_record(existing_record)
                ):
                    selected_record = existing_record
                else:
                    if existing_record is not None:
                        # 仅更新记录本体，保留 stolen_from / gifted_from 等来源状态。
                        replacement = _record_to_dict(record, ev, user_key)
                        updated_existing = dict(existing)
                        for key in ('name', 'role_ids', 'image', 'record_type', 'updated_at'):
                            updated_existing[key] = replacement[key]
                        await _save_daily_records(ev, [('lolis', user_key, updated_existing)])
                    else:
                        await _save_daily_records(
                            ev,
                            [('lolis', user_key, _record_to_dict(record, ev, user_key))],
                        )
        else:
            # 首抽当天还没有萝莉记录，必须落库，否则离婚/被抢/赠送流程读不到记录。
            await _save_daily_records(
                ev,
                [('lolis', user_key, _record_to_dict(record, ev, user_key))],
            )

    if response_text is not None:
        return await _send_loli_text(bot, response_text)
    await _send_loli_record(bot, ev, selected_record)


async def _send_upload_loli(bot: Bot, ev: Event) -> None:
    if not _can_upload_images(ev):
        return await _send_loli_text(bot, '你不在图片上传白名单中。')

    refs = _loli_upload_refs(ev)
    if not refs:
        return await _send_loli_text(bot, '请同时发送图片和命令，例如：上传萝莉图片 [图片]')

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
    image_map = await asyncio.to_thread(_loli_image_map)
    if not image_map:
        return await _send_loli_text(bot, '本地还没有萝莉图片，使用「上传萝莉图片」添加图片。')
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
    image_map = await asyncio.to_thread(_loli_image_map)
    path = image_map.get(hash_id)
    if path is None:
        return await _send_loli_text(bot, f'未找到图片ID：{hash_id}')
    try:
        await asyncio.to_thread(path.unlink)
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 删除萝莉图片失败: {path} -> {exc}')
        return await _send_loli_text(bot, f'删除失败：{hash_id}')
    _invalidate_loli_paths_cache()
    _LOLI_SOURCE_CACHE.invalidate()
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
    if not _loli_enabled():  # noqa: F405
        return
    await _send_loli_image(bot, ev)


@image_upload_sv.on_command(('上传萝莉图片', '今日萝莉上传', '萝莉上传图片'), block=True)
async def upload_loli(bot: Bot, ev: Event):
    await _send_upload_loli(bot, ev)


@loli_manage_sv.on_fullmatch(
    ('查看萝莉图片', '今日萝莉列表', '萝莉图片列表'),
    block=True,
    to_ai="""查看今日萝莉图库列表。
    当用户说“查看萝莉图片”“萝莉图片列表”“有哪些萝莉图”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def list_loli(bot: Bot, ev: Event):
    await _send_loli_image_list(bot, ev)


@loli_manage_sv.on_command('删除萝莉图片', block=True)
async def delete_loli(bot: Bot, ev: Event):
    await _send_delete_loli(bot, ev)
