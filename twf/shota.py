"""TodayWaifu - shota module."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from .domain import WifeRecord
from .image_input import (
    collect_image_refs,
    detect_image_suffix,
    image_hash_id,
    image_suffix_from_source,
    read_image_bytes,
)
from .shared import (
    IMAGE_EXTENSIONS,
    LOG_PREFIX,
    UPLOAD_IMAGE_MAX_BYTES,
    Bot,
    Event,
    MessageSegment,
    _cfg,
    _cfg_bool,
    _daily_context_lock,
    _daily_rng,
    _http_get_with_retry,
    _is_legacy_remote_loli_record,
    _load_daily_context,
    _record_from_dict,
    _record_to_dict,
    _safe_send,
    _save_daily_records,
    _send_loli_result_image,
    _send_loli_text,
    _shota_enabled,
    _shota_image_root,
    _user_key,
    image_upload_sv,
    logger,
    shota_manage_sv,
    shota_sv,
)
from .source_cache import AsyncSourceCache

_SHOTA_SOURCE_CACHE = AsyncSourceCache[tuple[str, ...]](300, max_entries=4)

# ── 本地图片目录读取 ─────────────────────────────────────────────────────────

_SHOTA_PATHS_CACHE_TTL = 300.0
_shota_paths_cache: tuple[float, tuple[Path, ...]] | None = None


def _invalidate_shota_paths_cache() -> None:
    global _shota_paths_cache
    _shota_paths_cache = None


def _shota_image_paths() -> tuple[Path, ...]:
    global _shota_paths_cache
    now = time.time()
    if _shota_paths_cache is not None and now - _shota_paths_cache[0] < _SHOTA_PATHS_CACHE_TTL:
        return _shota_paths_cache[1]
    root = _shota_image_root()
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
    _shota_paths_cache = (now, paths)
    return paths


def _delete_shota_images() -> int:
    root = _shota_image_root()
    count = len(_shota_image_paths())
    if root.exists():
        shutil.rmtree(root) if root.is_dir() else root.unlink()
    _invalidate_shota_paths_cache()
    _SHOTA_SOURCE_CACHE.invalidate()
    return count


# ── 上传辅助 ─────────────────────────────────────────────────────────────────

def _shota_image_hash_id(path: Path | str) -> str:
    return image_hash_id(path)


def _shota_upload_refs(ev: Event) -> tuple[str, ...]:
    return collect_image_refs(ev)


def _read_shota_image_bytes(source: str) -> tuple[bytes, str] | None:
    return read_image_bytes(source, UPLOAD_IMAGE_MAX_BYTES)


def _unique_shota_path(root: Path, suffix: str, index: int) -> Path:
    stamp = int(time.time() * 1000)
    counter = 0
    while True:
        tail = f'_{counter}' if counter else ''
        path = root / f'shota_{stamp}_{index}{tail}{suffix}'
        if not path.exists():
            return path
        counter += 1


def _save_shota_image(source: str, index: int) -> Path | None:
    result = _read_shota_image_bytes(source)
    if result is None:
        return None
    data, suffix = result
    root = _shota_image_root()
    root.mkdir(parents=True, exist_ok=True)
    path = _unique_shota_path(root, suffix, index)
    path.write_bytes(data)
    _invalidate_shota_paths_cache()
    return path


def _shota_image_map() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in _shota_image_paths():
        result[_shota_image_hash_id(path)] = path
    return result


# ── 命令处理 ─────────────────────────────────────────────────────────────────

def _shota_record_name(image: str) -> str:
    return f'正太图{_shota_image_hash_id(image)}'


def _parse_shota_image_urls(payload: dict[str, Any]) -> tuple[str, ...]:
    if 'roles' not in payload or not isinstance(payload['roles'], list):
        raise RuntimeError('正太图库接口缺少 roles 列表。')

    shota_role_found = False
    image_urls: list[str] = []
    seen_urls: set[str] = set()
    for role_data in payload['roles']:
        if not isinstance(role_data, dict):
            raise RuntimeError('正太图库接口的 roles 项必须是对象。')
        if 'role_ids' not in role_data or not isinstance(role_data['role_ids'], list):
            raise RuntimeError('正太图库接口的 role_ids 必须是列表。')

        role_ids = tuple(str(role_id).strip() for role_id in role_data['role_ids'])
        if 'shota' not in role_ids and 'zt' not in role_ids:
            continue
        shota_role_found = True

        if 'images' not in role_data or not isinstance(role_data['images'], list):
            raise RuntimeError('正太图库接口的 images 必须是列表。')
        for image_data in role_data['images']:
            if not isinstance(image_data, dict):
                raise RuntimeError('正太图库接口的 images 项必须是对象。')
            if 'url' not in image_data or not isinstance(image_data['url'], str):
                raise RuntimeError('正太图库接口的图片缺少 url 字符串。')
            image_url = image_data['url'].strip()
            if not image_url.startswith(('http://', 'https://')):
                raise RuntimeError('正太图库接口返回了无效的图片 URL。')
            if image_url not in seen_urls:
                seen_urls.add(image_url)
                image_urls.append(image_url)

    if not shota_role_found:
        raise RuntimeError('正太图库接口缺少 role_ids=["shota"] 的角色项。')
    if not image_urls:
        raise RuntimeError('正太图库接口没有可用图片。')
    return tuple(image_urls)


def _fetch_shota_image_urls_sync(api_url: str) -> tuple[str, ...]:
    try:
        body = _http_get_with_retry(api_url, timeout=15)
    except HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError('请求正太图库接口失败(403)：图库接口需要访问令牌，请在控制台配置「图库访问令牌」(DailyWifeGalleryToken)。') from exc
        raise RuntimeError(f'请求正太图库接口失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'请求正太图库接口失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('请求正太图库接口超时。') from exc
    except OSError as exc:
        raise RuntimeError(f'请求正太图库接口失败：{exc}') from exc

    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('正太图库接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('正太图库接口返回内容必须是 JSON 对象。')
    return _parse_shota_image_urls(payload)


def _shota_unavailable_text(record_data: dict[str, Any]) -> str | None:
    state = record_data.get('state')
    if not state:
        from .daily_state import wife_state
        state = wife_state(record_data)

    if state == 'lost_stolen':
        robber = record_data['stolen_by'] if 'stolen_by' in record_data else ''
        if 'stolen_by_name' in record_data and record_data['stolen_by_name']:
            robber = record_data['stolen_by_name']
        return f'你的正太已经被{robber}抢走了，今天就先忍忍吧~'
    if state == 'lost_gifted':
        receiver = record_data['gifted_to'] if 'gifted_to' in record_data else ''
        if 'gifted_to_name' in record_data and record_data['gifted_to_name']:
            receiver = record_data['gifted_to_name']
        return f'你的正太已经送给{receiver}了，今天就先忍忍吧~'
    if state == 'divorced':
        return '你今天已经和正太离婚了，明天再来吧~'
    return None


async def _roll_shota_record(
    ev: Event,
    user_key: str,
) -> tuple[WifeRecord | None, str | None]:
    custom_url = str(_cfg('DailyWifeShotaApiUrl') or _cfg('DailyShotaApiUrl') or 'https://zt.mimokit.dpdns.org').strip()
    remote_error: str | None = None
    if custom_url:
        logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日正太列表，接口: {custom_url}')
        try:
            image_urls = await _SHOTA_SOURCE_CACHE.get(
                custom_url,
                lambda: asyncio.to_thread(_fetch_shota_image_urls_sync, custom_url),
            )
        except RuntimeError as exc:
            remote_error = str(exc)
            logger.warning(f'{LOG_PREFIX} 远程正太接口失败，回退本地图片: {exc}')
        else:
            image_url = _daily_rng(ev, user_key, 'shota').choice(image_urls)
            return (
                WifeRecord(
                    name=_shota_record_name(image_url),
                    role_ids=('shota',),
                    image=image_url,
                    record_type='shota',
                ),
                None,
            )

    images = await asyncio.to_thread(_shota_image_paths)
    if not images:
        return None, remote_error or '暂无图片'
    image = _daily_rng(ev, user_key, 'shota').choice(images)
    logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日正太，选中本地图片: {image}')
    return (
        WifeRecord(
            name=_shota_record_name(str(image)),
            role_ids=(_shota_image_hash_id(image),),
            image=str(image),
            record_type='shota',
        ),
        None,
    )


async def _send_shota_record(
    bot: Bot,
    ev: Event,
    record: WifeRecord,
    text: str = '你今天的正太来啦！',
) -> None:
    await _send_loli_result_image(
        bot,
        record.image,
        text,
        ev.user_id,
        ev.group_id is not None,
    )


async def _send_shota_image(bot: Bot, ev: Event) -> None:
    context = await _load_daily_context(ev)
    user_key = _user_key(ev)
    current = context.get('shotas', {}).get(user_key)
    if isinstance(current, dict):
        unavailable_text = _shota_unavailable_text(current)
        if unavailable_text is not None:
            return await _send_loli_text(bot, unavailable_text)
        record = _record_from_dict(current)
        if record is not None and not _is_legacy_remote_loli_record(record):
            return await _send_shota_record(bot, ev, record)

    record, error = await _roll_shota_record(ev, user_key)
    if record is None:
        return await _send_loli_text(bot, error or '暂无图片')

    response_text: str | None = None
    selected_record = record
    async with _daily_context_lock(ev):
        save_context = await _load_daily_context(ev)
        existing = save_context.get('shotas', {}).get(user_key)
        if isinstance(existing, dict):
            unavailable_text = _shota_unavailable_text(existing)
            if unavailable_text is not None:
                response_text = unavailable_text
            else:
                existing_record = _record_from_dict(existing)
                if existing_record is not None and not _is_legacy_remote_loli_record(existing_record):
                    selected_record = existing_record
                else:
                    if existing_record is not None:
                        replacement = _record_to_dict(record, ev, user_key)
                        updated_existing = dict(existing)
                        for key in ('name', 'role_ids', 'image', 'record_type', 'updated_at'):
                            updated_existing[key] = replacement[key]
                        await _save_daily_records(ev, [('shotas', user_key, updated_existing)])
                    else:
                        await _save_daily_records(
                            ev,
                            [('shotas', user_key, _record_to_dict(record, ev, user_key))],
                        )
        else:
            await _save_daily_records(
                ev,
                [('shotas', user_key, _record_to_dict(record, ev, user_key))],
            )

    if response_text is not None:
        return await _send_loli_text(bot, response_text)
    await _send_shota_record(bot, ev, selected_record)


async def _send_upload_shota(bot: Bot, ev: Event) -> None:
    from .shared import _can_upload_images
    if not _can_upload_images(ev):
        return await _send_loli_text(bot, '你不在图片上传白名单中。')

    refs = _shota_upload_refs(ev)
    if not refs:
        return await _send_loli_text(bot, '请同时发送图片和命令，例如：上传正太图片 [图片]')

    saved: list[Path] = []
    failed = 0
    for i, ref in enumerate(refs, 1):
        path = await asyncio.to_thread(_save_shota_image, ref, i)
        if path is None:
            failed += 1
        else:
            saved.append(path)

    if not saved:
        return await _send_loli_text(bot, '上传失败，请确认消息里附带的是图片。')

    ids = [_shota_image_hash_id(p) for p in saved]
    lines = [f'正太图片上传成功，共 {len(saved)} 张', f'图片ID：{", ".join(ids)}']
    if failed:
        lines.append(f'失败：{failed} 张')
    await _send_loli_text(bot, '\n'.join(lines))


async def _send_shota_image_list(bot: Bot, ev: Event) -> None:
    image_map = await asyncio.to_thread(_shota_image_map)
    if not image_map:
        return await _send_loli_text(bot, '本地还没有正太图片，使用「上传正太图片」添加图片。')
    nodes: list[Any] = []
    for hash_id, path in image_map.items():
        nodes.append(f'正太图片ID：{hash_id}')
        nodes.append(MessageSegment.image(path))
    await _safe_send(bot, MessageSegment.node(nodes))


async def _send_delete_shota(bot: Bot, ev: Event) -> None:
    hash_id = str(ev.text or '').strip().lower()
    if not hash_id:
        logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 触发删除全部正太图片命令')
        count = await asyncio.to_thread(_delete_shota_images)
        return await _send_loli_text(bot, f'已删除全部正太图片，共 {count} 张。')
    if not re.fullmatch(r'[0-9a-f]{8}', hash_id):
        return await _send_loli_text(bot, '请提供 8 位图片ID，例如：删除正太图片 abcd1234\n不加ID则删除全部')
    image_map = await asyncio.to_thread(_shota_image_map)
    path = image_map.get(hash_id)
    if path is None:
        return await _send_loli_text(bot, f'未找到图片ID：{hash_id}')
    try:
        await asyncio.to_thread(path.unlink)
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 删除正太图片失败: {path} -> {exc}')
        return await _send_loli_text(bot, f'删除失败：{hash_id}')
    _invalidate_shota_paths_cache()
    _SHOTA_SOURCE_CACHE.invalidate()
    await _send_loli_text(bot, f'已删除正太图片：{hash_id}')


# ── 触发器注册 ────────────────────────────────────────────────────────────────

@shota_sv.on_fullmatch(
    '今日正太',
    block=True,
    to_ai="""随机抽取当前用户今天的正太图片。
    当用户说“今日正太”“抽一张正太”“我今天的正太是谁”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_shota(bot: Bot, ev: Event):
    if not _shota_enabled():
        return
    await _send_shota_image(bot, ev)


@image_upload_sv.on_command(('上传正太图片', '今日正太上传', '正太上传图片'), block=True)
async def upload_shota(bot: Bot, ev: Event):
    await _send_upload_shota(bot, ev)


@shota_manage_sv.on_fullmatch(
    ('查看正太图片', '今日正太列表', '正太图片列表'),
    block=True,
    to_ai="""查看今日正太图库列表。
    当用户说“查看正太图片”“正太图片列表”“有哪些正太图”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def list_shota(bot: Bot, ev: Event):
    await _send_shota_image_list(bot, ev)


@shota_manage_sv.on_command('删除正太图片', block=True)
async def delete_shota(bot: Bot, ev: Event):
    await _send_delete_shota(bot, ev)
