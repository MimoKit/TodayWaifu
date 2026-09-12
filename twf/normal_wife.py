"""Normal wife gallery loader and cache."""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from .shared import (
    CACHE_TTL_SECONDS,
    LOG_PREFIX,
    RoleCandidate,
    _cfg,
    _fetch_gallery_payload_from_url_sync,
    logger,
)
from .source_cache import AsyncSourceCache

_NORMAL_GALLERY_CACHE = AsyncSourceCache[dict[str, Any]](CACHE_TTL_SECONDS, max_entries=4)


def prune_normal_gallery_cache() -> None:
    _NORMAL_GALLERY_CACHE.prune()


def invalidate_normal_gallery_cache() -> None:
    _NORMAL_GALLERY_CACHE.invalidate()


def _normal_gallery_api_url() -> str:
    url = str(_cfg('DailyWifeNormalGalleryApiUrl') or _cfg('DailyWifeRandomGalleryApiUrl') or '').strip()
    return url or 'https://ceshi.mimokit.dpdns.org/api/ceshi/roles'


def _parse_normal_gallery_candidates(
    payload: dict[str, Any],
) -> tuple[RoleCandidate, ...]:
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list) or not roles_data:
        raise RuntimeError('普通老婆图库没有返回可用角色。')

    candidates: list[RoleCandidate] = []
    for item in roles_data:
        if not isinstance(item, dict):
            continue
        role_ids_data = item.get('role_ids')
        if not isinstance(role_ids_data, list):
            continue
        role_ids = tuple(
            str(role_id).strip()
            for role_id in role_ids_data
            if str(role_id).strip()
        )
        if not role_ids:
            continue

        name = str(item.get('name') or role_ids[0]).strip()
        images_data = item.get('images')
        if not name or not isinstance(images_data, list):
            continue
        images: list[str] = []
        for image_item in images_data:
            if not isinstance(image_item, dict):
                continue
            image_url = str(image_item.get('url') or '').strip()
            parsed = urlparse(image_url)
            if parsed.scheme == 'https' and parsed.netloc and image_url not in images:
                images.append(image_url)
        if images:
            candidates.append(RoleCandidate(name, role_ids, tuple(images)))

    if not candidates:
        raise RuntimeError('普通老婆图库没有返回有效的 HTTPS 图片。')
    return tuple(candidates)


async def _load_normal_wife_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    api_url = _normal_gallery_api_url()
    if not api_url:
        return None, '未配置普通老婆图库接口。'

    try:
        payload = await _NORMAL_GALLERY_CACHE.get(
            api_url,
            lambda: asyncio.to_thread(_fetch_gallery_payload_from_url_sync, api_url),
        )
        candidates = _parse_normal_gallery_candidates(payload)
        return candidates, None
    except HTTPError as exc:
        if exc.code in {401, 403}:
            message = '图库访问令牌无效或未配置。'
        else:
            message = f'请求普通老婆图库失败，HTTP {exc.code}。'
        logger.warning(f'{LOG_PREFIX} {message}')
        return None, message
    except (RuntimeError, URLError, TimeoutError, OSError) as exc:
        message = str(exc) or '读取普通老婆图库失败。'
        logger.warning(f'{LOG_PREFIX} 读取普通老婆图库失败: {message}')
        return None, message
