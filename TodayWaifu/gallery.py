"""TodayWaifu 的远程图库访问与图片下载。"""
from __future__ import annotations

import json
import time
import random
import asyncio
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from gsuid_core.logger import logger

from . import state
from .paths import _role_mode, _role_map_title, _gallery_image_cache_root
from .roles import (
    _is_excluded_role,
    _load_mode_role_map,
    _normalize_role_name,
    _load_local_candidates,
    _merge_role_candidates,
    _load_nte_local_candidates,
    _load_pgr_local_candidates,
    _load_custom_upload_candidates,
)
from .state import (
    _IMAGE_INFLIGHT,
    CANDIDATE_CACHE,
    _CANDIDATE_INFLIGHT,
    _PGR_CANDIDATE_CACHE,
    _CANDIDATE_LOAD_SEMAPHORE,
    _IMAGE_DOWNLOAD_SEMAPHORE,
)
from .domain import RoleCandidate
from .executor import run_blocking
from .payloads import GalleryPayload
from .constants import (
    LOG_PREFIX,
    HTTP_RETRIES,
    CACHE_TTL_SECONDS,
    RETRY_JITTER_SECONDS,
    RETRY_MAX_DELAY_SECONDS,
    CIRCUIT_COOLDOWN_SECONDS,
    DEFAULT_GALLERY_BASE_URL,
    MAX_IMAGE_RESPONSE_BYTES,
    RETRY_BASE_DELAY_SECONDS,
    CIRCUIT_FAILURE_THRESHOLD,
    IMAGE_HTTP_TIMEOUT_SECONDS,
    MAX_GALLERY_RESPONSE_BYTES,
    GALLERY_HTTP_TIMEOUT_SECONDS,
    _cfg,
    _cfg_bool,
    _image_source,
)
from .file_cache import read_url_cache, write_url_cache
from .circuit_breaker import CircuitBreaker


def _pgr_gallery_api_url() -> str:
    base = str(_cfg('DailyWifeApiUrl') or _cfg('DailyWifePgrGalleryApiUrl') or '').strip().rstrip('/')
    if not base:
        return ''
    if '/pgr/' in base or base.endswith('/roles'):
        return base
    return f'{base}/api/pgr/roles'


def _parse_pgr_gallery_candidates(payload: GalleryPayload) -> tuple[RoleCandidate, ...]:
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list):
        return ()
    candidates: list[RoleCandidate] = []
    for item in roles_data:
        if not isinstance(item, dict):
            continue
        role_ids = tuple(str(value).strip() for value in item.get('role_ids') or [] if str(value).strip())
        if not role_ids:
            continue
        name = str(item.get('name') or role_ids[0]).strip()
        images = tuple(
            str(image.get('url') or '').strip()
            for image in item.get('images') or []
            if isinstance(image, dict) and str(image.get('url') or '').strip().startswith(('http://', 'https://'))
        )
        if name and images:
            candidates.append(RoleCandidate(name=name, role_ids=role_ids, images=images))
    return tuple(sorted(candidates, key=lambda role: role.name))


async def _load_pgr_wife_candidates() -> tuple[RoleCandidate, ...]:
    api_url = _pgr_gallery_api_url()
    if api_url:
        try:
            async def load_remote() -> tuple[RoleCandidate, ...]:
                payload = await run_blocking(_fetch_gallery_payload_from_url_sync, api_url)
                candidates = _parse_pgr_gallery_candidates(payload)
                if not candidates:
                    raise RuntimeError('战双远程图库没有可用角色。')
                return candidates

            return await _PGR_CANDIDATE_CACHE.get(api_url, load_remote)
        except (RuntimeError, OSError, TimeoutError) as exc:
            logger.warning(f'{LOG_PREFIX} 读取战双远程图库失败，回退本地图库: {exc}')
    return await run_blocking(_load_pgr_local_candidates)


def _gallery_api_url() -> str:
    base = str(_cfg('DailyWifeApiUrl') or _cfg('DailyWifeGalleryApiUrl') or DEFAULT_GALLERY_BASE_URL).strip().rstrip('/')
    if base.endswith('/roles'):
        return base
    return f'{base}/api/xwuid/roles'


def _request_headers() -> dict[str, str]:
    headers = {'User-Agent': 'TodayWaifu/1.0'}
    token = str(_cfg('DailyWifeGalleryToken') or '').strip()
    if token:
        headers['X-Gallery-Token'] = token
    return headers


def _http_get(url: str, *, timeout: int = 15, max_bytes: int = MAX_GALLERY_RESPONSE_BYTES) -> bytes:
    request = Request(url, headers=_request_headers())
    with urlopen(request, timeout=timeout) as resp:
        content_length = resp.headers.get('Content-Length')
        if content_length and int(content_length) > max_bytes:
            raise OSError(f'远程响应过大（超过 {max_bytes} 字节）。')
        chunks: list[bytes] = []
        total = 0
        while chunk := resp.read(min(64 * 1024, max_bytes - total + 1)):
            total += len(chunk)
            if total > max_bytes:
                raise OSError(f'远程响应过大（超过 {max_bytes} 字节）。')
            chunks.append(chunk)
        return b''.join(chunks)


# 按主机熔断：图库整体挂掉时不再让每个用户都打满重试链
_HTTP_BREAKER = CircuitBreaker(
    failure_threshold=CIRCUIT_FAILURE_THRESHOLD,
    cooldown_seconds=CIRCUIT_COOLDOWN_SECONDS,
)


def _circuit_key(url: str) -> str:
    return urlparse(url).netloc or url


def gallery_circuit_state() -> tuple[bool, float]:
    """返回图库主机的 (是否熔断, 距离冷却结束秒数)，供可观测性使用。"""
    key = _circuit_key(_gallery_api_url())
    return _HTTP_BREAKER.is_open(key), _HTTP_BREAKER.retry_after(key)


def _retry_delay(attempt: int) -> float:
    """指数退避 + 抖动，避免所有失败请求在同一时刻重试形成同步脉冲。"""
    base = RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
    return min(base, RETRY_MAX_DELAY_SECONDS) + random.uniform(0, RETRY_JITTER_SECONDS)


def _http_get_with_retry(
    url: str,
    *,
    timeout: int = GALLERY_HTTP_TIMEOUT_SECONDS,
    retries: int = HTTP_RETRIES,
    max_bytes: int = MAX_GALLERY_RESPONSE_BYTES,
) -> bytes:
    """请求远程资源，失败或超时时按指数退避重试 `retries` 次。

    401/403 属于认证或授权错误，重试无意义，直接抛出（也不喂给熔断器，
    因为那是配置问题而不是上游故障）。连续失败达到阈值后熔断一段时间，
    期间直接快速失败，不再打网络。
    """
    key = _circuit_key(url)
    if not _HTTP_BREAKER.allow(key):
        raise RuntimeError(
            f'图库接口连续失败，已熔断 {_HTTP_BREAKER.retry_after(key):.0f} 秒后重试。'
        )

    last_exc: Exception = RuntimeError(f'请求 {url} 失败。')
    for attempt in range(retries + 1):
        try:
            body = _http_get(url, timeout=timeout, max_bytes=max_bytes)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise
            last_exc = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_exc = exc
        else:
            _HTTP_BREAKER.record_success(key)
            return body
        if attempt < retries:
            delay = _retry_delay(attempt)
            logger.warning(
                f'{LOG_PREFIX} 请求远程资源失败(第{attempt + 1}/{retries}次重试): {url}，'
                f'{delay:.1f} 秒后重试: {last_exc}'
            )
            time.sleep(delay)

    _HTTP_BREAKER.record_failure(key)
    raise last_exc


def _fetch_gallery_payload_sync() -> GalleryPayload:
    api_url = _gallery_api_url()
    if not api_url:
        raise RuntimeError('未配置图库接口地址。')
    try:
        body = _http_get_with_retry(api_url, timeout=GALLERY_HTTP_TIMEOUT_SECONDS)
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError('图库账号或密码不正确，接口返回 401。') from exc
        raise RuntimeError(f'请求图库接口失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'请求图库接口失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('请求图库接口超时。') from exc

    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('图库接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('图库接口返回格式不正确。')
    return payload


def _fetch_gallery_payload_from_url_sync(url: str) -> GalleryPayload:
    body = _http_get_with_retry(url, timeout=GALLERY_HTTP_TIMEOUT_SECONDS)
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('图库接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('图库接口返回格式不正确。')
    return payload


def _parse_role_candidates(
    payload: GalleryPayload,
    mode: str = 'wife',
    role_map: dict[str, str] | None = None,
) -> tuple[RoleCandidate, ...]:
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list):
        return ()

    role_map = role_map or _load_mode_role_map(mode)
    candidates: list[RoleCandidate] = []
    for item in roles_data:
        if not isinstance(item, dict):
            continue

        role_ids_data = item.get('role_ids') or []
        role_ids = tuple(str(role_id).strip() for role_id in role_ids_data if str(role_id).strip())
        allowed_role_ids = tuple(role_id for role_id in role_ids if role_id in role_map)
        if not allowed_role_ids:
            continue

        name = role_map[allowed_role_ids[0]]
        if not name or _is_excluded_role(name):
            continue

        images: list[str] = []
        for image_item in item.get('images') or []:
            if isinstance(image_item, dict):
                url = str(image_item.get('url') or '').strip()
            else:
                url = str(image_item or '').strip()
            if url.startswith(('http://', 'https://')):
                images.append(url)
        if images:
            candidates.append(RoleCandidate(name=name, role_ids=allowed_role_ids, images=tuple(images)))

    logger.debug(f'{LOG_PREFIX} 成功从图库解析候选角色 {len(candidates)} 名')
    return tuple(sorted(candidates, key=lambda role: role.name))


def _download_image_sync(url: str) -> bytes:
    try:
        return _http_get_with_retry(
            url, timeout=IMAGE_HTTP_TIMEOUT_SECONDS, max_bytes=MAX_IMAGE_RESPONSE_BYTES
        )
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError('图库账号或密码不正确，图片返回 401。') from exc
        raise RuntimeError(f'下载图片失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'下载图片失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('下载图片超时。') from exc


async def _download_image(url: str) -> bytes:
    """下载图库图片；按 URL 哈希落盘缓存，并合并相同 URL 的并发下载。"""
    cache_root = _gallery_image_cache_root()
    cached = await run_blocking(read_url_cache, cache_root, url)
    if cached is not None:
        logger.debug(f'{LOG_PREFIX} 命中图库图片磁盘缓存: {url}')
        return cached

    task = _IMAGE_INFLIGHT.get(url)
    if task is None:
        async def download() -> bytes:
            async with _IMAGE_DOWNLOAD_SEMAPHORE:
                second_cached = await run_blocking(read_url_cache, cache_root, url)
                if second_cached is not None:
                    return second_cached
                data = await run_blocking(_download_image_sync, url)
                await run_blocking(write_url_cache, cache_root, url, data)
                return data
        task = asyncio.create_task(download())
        _IMAGE_INFLIGHT[url] = task
    try:
        return await task
    finally:
        if task.done() and _IMAGE_INFLIGHT.get(url) is task:
            _IMAGE_INFLIGHT.pop(url, None)


async def _fallback_to_local_candidates(
    role_mode: str,
    custom_candidates: tuple[RoleCandidate, ...],
    fallback_error: str,
) -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    """图库接口失败后的兜底：优先回退本地图片目录，其次使用本地上传候选。"""
    local_candidates, local_error = await run_blocking(_load_local_candidates, role_mode)
    if local_candidates:
        logger.warning(f'{LOG_PREFIX} 图库接口不可用，已回退本地图片目录。')
        return local_candidates, None
    if custom_candidates:
        logger.warning(f'{LOG_PREFIX} 图库接口不可用，已回退本地上传候选。')
        return custom_candidates, None
    return None, local_error or fallback_error


async def _load_wuwa_candidates_uncached(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    source = _image_source()
    role_mode = _role_mode(mode)
    now = time.time()
    cache_key = f'{source}:{role_mode}'
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        logger.debug(f'{LOG_PREFIX} 使用缓存的候选角色列表: {cache_key}')
        return cached[1], None

    if source == 'local':
        candidates, error = await run_blocking(_load_local_candidates, role_mode)
        if error or not candidates:
            return None, error
        CANDIDATE_CACHE[cache_key] = (now, candidates)
        return candidates, None

    custom_candidates = await run_blocking(_load_custom_upload_candidates) if role_mode == 'wife' else ()
    try:
        role_map = _load_mode_role_map(role_mode)
        if not role_map and not custom_candidates:
            return None, f'没有找到鸣潮{_role_map_title(role_mode)}角色 ID 对照表。'
        candidates = ()
        if role_map:
            payload = await run_blocking(_fetch_gallery_payload_sync)
            candidates = _parse_role_candidates(payload, role_mode, role_map)
            gallery_role_names = {_normalize_role_name(c.name) for c in candidates}
            gallery_role_ids = {rid for c in candidates for rid in c.role_ids}
            missing_in_gallery = any(
                rid not in gallery_role_ids and _normalize_role_name(rname) not in gallery_role_names
                for rid, rname in role_map.items()
            )
            if missing_in_gallery:
                local_candidates, _ = await run_blocking(_load_local_candidates, role_mode)
                if local_candidates:
                    supplement_candidates: list[RoleCandidate] = []
                    for lc in local_candidates:
                        norm_name = _normalize_role_name(lc.name)
                        if norm_name not in gallery_role_names and not (set(lc.role_ids) & gallery_role_ids):
                            supplement_candidates.append(lc)
                    if supplement_candidates:
                        logger.info(
                            f'{LOG_PREFIX} 图库模式下为 {len(supplement_candidates)} 名对照表无图角色读取本地图片: '
                            f'{", ".join(c.name for c in supplement_candidates)}'
                        )
                        candidates = tuple(sorted((*candidates, *supplement_candidates), key=lambda r: r.name))
        candidates = _merge_role_candidates(candidates, custom_candidates)
    except (RuntimeError, OSError, TimeoutError) as exc:
        logger.warning(f'{LOG_PREFIX} 读取图库接口失败: {exc}')
        # RuntimeError 携带图库接口的友好原因；I/O 类异常沿用原通用文案，避免把底层
        # errno 细节直接暴露给用户。
        reason = str(exc) if isinstance(exc, RuntimeError) else '读取图库接口失败。'
        candidates, error = await _fallback_to_local_candidates(role_mode, custom_candidates, reason)
        if error or not candidates:
            return None, error
        CANDIDATE_CACHE[cache_key] = (now, candidates)
        return candidates, None

    if not candidates:
        return None, '图库接口里没有找到可用的角色立绘。'

    CANDIDATE_CACHE[cache_key] = (now, candidates)
    return candidates, None


async def _load_wuwa_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
    cache_key = f'{_image_source()}:{role_mode}'
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1], None
    task = _CANDIDATE_INFLIGHT.get(cache_key)
    if task is None:
        generation = state._CANDIDATE_CACHE_GENERATION
        async def load() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
            async with _CANDIDATE_LOAD_SEMAPHORE:
                result = await _load_wuwa_candidates_uncached(role_mode)
                if generation != state._CANDIDATE_CACHE_GENERATION:
                    CANDIDATE_CACHE.pop(cache_key, None)
                return result
        task = asyncio.create_task(load())
        _CANDIDATE_INFLIGHT[cache_key] = task
    try:
        return await task
    finally:
        if task.done() and _CANDIDATE_INFLIGHT.get(cache_key) is task:
            _CANDIDATE_INFLIGHT.pop(cache_key, None)


async def _load_nte_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    cache_key = 'local:nte'
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1], None
    task = _CANDIDATE_INFLIGHT.get(cache_key)
    if task is None:
        async def load() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
            async with _CANDIDATE_LOAD_SEMAPHORE:
                candidates, error = await run_blocking(_load_nte_local_candidates)
                if error or not candidates:
                    return None, error
                CANDIDATE_CACHE[cache_key] = (time.time(), candidates)
                return candidates, None
        task = asyncio.create_task(load())
        _CANDIDATE_INFLIGHT[cache_key] = task
    try:
        return await task
    finally:
        if task.done() and _CANDIDATE_INFLIGHT.get(cache_key) is task:
            _CANDIDATE_INFLIGHT.pop(cache_key, None)


async def _load_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
    if role_mode == 'normal':
        from .normal_wife import _load_normal_wife_candidates
        return await _load_normal_wife_candidates()
    if role_mode == 'wife' and _cfg_bool('DailyWifeNormalEnabled', False):
        from .normal_wife import _load_normal_wife_candidates
        return await _load_normal_wife_candidates()
    if role_mode == 'nte':
        return await _load_nte_candidates()

    candidates, error = await _load_wuwa_candidates(role_mode)
    if error or not candidates:
        return None, error
    return candidates, None
