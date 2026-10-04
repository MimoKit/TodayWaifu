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
    _apply_no_r18,
    _image_source,
)
from .file_cache import read_url_cache, write_url_cache
from .circuit_breaker import CircuitBreaker


def _pgr_gallery_api_url() -> str:
    base = str(_cfg('DailyWifeApiUrl') or '').strip().rstrip('/')
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
    if _image_source('pgr') == 'local':
        # local 模式必须完全不触碰网络：即使远程图库可用也不得访问，本地无图时返回空，
        # 由调用方向用户提示上传图片
        return await run_blocking(_load_pgr_local_candidates)

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
    base = str(_cfg('DailyWifeApiUrl') or DEFAULT_GALLERY_BASE_URL).strip().rstrip('/')
    # 已填完整端点（含 /roles）时原样尊重：用户显式写了过滤后的地址，不应再叠 nor18
    if base.endswith('/roles'):
        return base
    return _apply_no_r18(f'{base}/api/xwuid/roles')


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
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise OSError('远程响应的 Content-Length 无效。') from exc
            if declared_length > max_bytes:
                raise OSError(f'远程响应过大（超过 {max_bytes} 字节）。')
        chunks: list[bytes] = []
        total = 0
        # 逐块读取而非一次性 read()：Content-Length 可能被省略或低报，只有按累计字节数判断
        # 才能在越界时立即中断，而不必先把整个响应读入内存。单次读取量刻意多取 1 字节，
        # 使累计值一旦超过上限即可被察觉，避免上限恰好被读满时误判为未超限。
        while chunk := resp.read(min(64 * 1024, max_bytes - total + 1)):
            total += len(chunk)
            if total > max_bytes:
                raise OSError(f'远程响应过大（超过 {max_bytes} 字节）。')
            chunks.append(chunk)
        return b''.join(chunks)


# 按主机熔断：图库整体不可用时，若仍由每个请求各自发起完整重试链，会把已经过载的
# 上游与线程池一并压垮（重试风暴）。连续失败达到阈值后直接快速失败一段时间，
# 使上游负载立即归零并留出恢复窗口，插件同时降级到本地图库。
_HTTP_BREAKER = CircuitBreaker(
    failure_threshold=CIRCUIT_FAILURE_THRESHOLD,
    cooldown_seconds=CIRCUIT_COOLDOWN_SECONDS,
)


def _circuit_key(url: str) -> str:
    return urlparse(url).netloc or url


def gallery_circuit_state() -> tuple[bool, float]:
    """返回图库主机的 (是否熔断, 距离冷却结束秒数)，供可观测性使用。

    查询必须走断路器的只读接口：`is_open` 不触发半开转移，若改用 `allow()`，仅采集指标
    这一动作就会提前消费掉探测机会，使冷却期实际失效。
    """
    key = _circuit_key(_gallery_api_url())
    return _HTTP_BREAKER.is_open(key), _HTTP_BREAKER.retry_after(key)


def _retry_delay(attempt: int) -> float:
    """返回第 `attempt` 次重试前的等待秒数：指数退避叠加抖动。

    退避上限用于约束单次等待，避免失败次数增多后等待时间无界增长；抖动的意义在于打散
    重试时刻——否则同一批失败请求会在同一瞬间一起重试，形成同步脉冲，反而加剧上游压力。
    """
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

    重试次数必须保持极小（当前配置为 1）。每次重试都是对上游的完整重放，重试次数即是
    故障期间请求量的放大倍数；重试次数偏大不仅无助于恢复，还会在零点高峰把上游与线程池
    一起推到过载，因此宁可尽早失败并降级到本地图库。

    401/403/429 属于认证、授权与限流类错误：重试无法改变结果（限流须等待窗口过去），
    故直接抛出，且**不记入熔断器** —— 这三类是客户端侧条件，不代表上游故障。若把限流
    计入熔断失败，一次突发限流就会令图库被误熔断，此后全部请求快速失败并回退本地，
    用户看到的现象从「请求过快」变成「图库已挂」，排查方向随之偏离。

    其余连续失败达到阈值后熔断一段时间，冷却期内直接快速失败，不再产生网络请求。
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
            if exc.code in {401, 403, 429}:
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
        if exc.code in {401, 403, 429}:
            raise RuntimeError(_auth_error_reason(exc, what='请求图库接口')) from exc
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
    """按给定地址拉取图库列表（战双与测试图库走此入口）。

    与 `_fetch_gallery_payload_sync` 一致，须把认证与限流类错误映射为可操作的中文提示。
    该入口原先任由 HTTPError 冒泡，调用方只能得到 `HTTP Error 403: Forbidden`，既无法
    判断是令牌问题还是 IP 被封，也没有可执行的排查方向。
    """
    try:
        body = _http_get_with_retry(url, timeout=GALLERY_HTTP_TIMEOUT_SECONDS)
    except HTTPError as exc:
        if exc.code in {401, 403, 429}:
            raise RuntimeError(_auth_error_reason(exc, what='请求图库接口')) from exc
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
        # 以本地对照表为准做准入：图库包含跨模式的全部角色，缺少这一步会把不在本模式
        # 对照表中的角色混入候选，导致抽取结果与命名规则不一致
        allowed_role_ids = tuple(role_id for role_id in role_ids if role_id in role_map)
        if not allowed_role_ids:
            continue

        # 名称取对照表而非图库返回值：展示名须与用户在配置中维护的名称一致
        name = role_map[allowed_role_ids[0]]
        if not name or _is_excluded_role(name):
            continue

        images: list[str] = []
        for image_item in item.get('images') or []:
            if isinstance(image_item, dict):
                url = str(image_item.get('url') or '').strip()
            else:
                url = str(image_item or '').strip()
            # 仅接受 http(s) 图片地址：本地路径由磁盘扫描负责，相对路径也无法在下载侧解析
            if url.startswith(('http://', 'https://')):
                images.append(url)
        if images:
            candidates.append(RoleCandidate(name=name, role_ids=allowed_role_ids, images=tuple(images)))

    logger.debug(f'{LOG_PREFIX} 成功从图库解析候选角色 {len(candidates)} 名')
    return tuple(sorted(candidates, key=lambda role: role.name))


def _auth_error_reason(exc: HTTPError, *, what: str) -> str:
    """把图库的 401/403/429 转成可操作的中文提示。

    图库拒绝请求的原因不止一种：令牌缺失、错误或被注销，IP 被临时封禁，请求过于频繁。
    这些情形都可能落在 403/429 上，仅凭状态码无法区分，因此需要读取响应体中的 `error`
    字段，使用户能判断应当修改配置还是稍后重试。响应体不可读或格式异常时退化为按状态码
    给出的通用提示：错误映射本身不得成为失败点。
    """
    code = getattr(exc, 'code', 0)
    detail = ''
    try:
        raw = exc.read()
        if raw:
            payload = json.loads(raw.decode('utf-8', 'replace'))
            if isinstance(payload, dict):
                detail = str(payload.get('error') or '')
    except (OSError, ValueError, AttributeError):
        detail = ''

    if code == 429 or detail in {'rate_limited', 'crawl_detected', 'quota_exceeded'}:
        if detail == 'quota_exceeded':
            return f'{what}失败(429)：今日访问配额已用尽，请明天再试或联系管理员调整配额。'
        return f'{what}失败(429)：请求过于频繁，已被图库限流，请稍后再试。'
    if detail == 'ip_banned':
        return f'{what}失败(403)：当前 IP 已被图库封禁，请联系管理员解封。'
    if code == 401:
        return f'{what}失败(401)：图库令牌无效或已被注销，请在控制台更新「图库访问令牌」(DailyWifeGalleryToken)。'
    return (
        f'{what}失败(403)：图库拒绝了本次请求，通常是「图库访问令牌」缺失、错误或已被注销；'
        f'请在控制台检查「图库访问令牌」(DailyWifeGalleryToken)。'
    )


def _download_image_sync(url: str) -> bytes:
    try:
        return _http_get_with_retry(
            url, timeout=IMAGE_HTTP_TIMEOUT_SECONDS, max_bytes=MAX_IMAGE_RESPONSE_BYTES
        )
    except HTTPError as exc:
        if exc.code in {401, 403, 429}:
            raise RuntimeError(_auth_error_reason(exc, what='下载图片')) from exc
        raise RuntimeError(f'下载图片失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'下载图片失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('下载图片超时。') from exc


async def _download_image(url: str) -> bytes:
    """下载图库图片；按 URL 归一化后的哈希落盘缓存，并合并相同 URL 的并发下载。

    归一化由 `file_cache` 负责：图库为每张图附加的短期签名随周期轮换，若以原始 URL 为
    缓存键，签名一变即视为新资源，既使预热成果全部失效，也会在磁盘上重复保存同一张图。

    并发合并以 URL 为键共享同一个 Task，使同一张图只下载一次；合并的前提是下载任务
    独立于任何等待方，因此单个等待者超时或取消不会中断共享任务，其余等待者仍能取到
    结果。信号量在任务内部获取，故合并后的请求只占用一个下载名额。

    任务句柄的清理限定为「自身已完成且仍是表中当前项」：若期间已被其它协程替换，
    清除操作会误删新任务，导致后续请求无法合并。
    """
    cache_root = _gallery_image_cache_root()
    cached = await run_blocking(read_url_cache, cache_root, url)
    if cached is not None:
        logger.debug(f'{LOG_PREFIX} 命中图库图片磁盘缓存: {url}')
        return cached

    task = _IMAGE_INFLIGHT.get(url)
    if task is None:
        async def download() -> bytes:
            async with _IMAGE_DOWNLOAD_SEMAPHORE:
                # 进入信号量后复查磁盘缓存：等待名额期间同一张图可能已由其它任务写盘，
                # 此处命中可省去一次重复下载
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
    """图库接口失败后的兜底：优先回退本地图片目录，其次使用本地上传候选。

    回退顺序按数据完整度排列：本地图片目录覆盖角色更全，上传候选仅为用户补传的少量角色。
    两者皆空时原样返回图库侧错误原因，使调用方能把真实故障告知用户，而不是笼统报「没有
    可用角色」。
    """
    local_candidates, local_error = await run_blocking(_load_local_candidates, role_mode)
    if local_candidates:
        logger.warning(f'{LOG_PREFIX} 图库接口不可用，已回退本地图片目录。')
        return local_candidates, None
    if custom_candidates:
        logger.warning(f'{LOG_PREFIX} 图库接口不可用，已回退本地上传候选。')
        return custom_candidates, None
    return None, local_error or fallback_error


async def _load_wuwa_candidates_uncached(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
    source = _image_source(role_mode)
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
            # 图库缺图时用本地图片补位：对照表中的角色可能尚未收录于图库，若不做补齐，
            # 这些角色会从候选集中静默消失，用户侧表现为「某些角色永远抽不到」。
            # 判据同时比对名称与 ID，避免因命名写法不同而重复补入同一角色。
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
        # RuntimeError 携带图库接口的友好原因；I/O 类异常沿用通用文案，避免把底层 errno
        # 细节直接暴露给用户。
        reason = str(exc) if isinstance(exc, RuntimeError) else '读取图库接口失败。'
        candidates, error = await _fallback_to_local_candidates(role_mode, custom_candidates, reason)
        if error or not candidates:
            return None, error
        CANDIDATE_CACHE[cache_key] = (now, candidates)
        return candidates, None

    if not candidates:
        return None, '图库接口里没有找到可用的角色立绘。'

    # 仅缓存成功结果：把「无候选」也写入缓存会使一次瞬时故障在 TTL 内持续生效
    CANDIDATE_CACHE[cache_key] = (now, candidates)
    return candidates, None


async def _load_wuwa_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
    cache_key = f'{_image_source(role_mode)}:{role_mode}'
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1], None
    task = _CANDIDATE_INFLIGHT.get(cache_key)
    if task is None:
        generation = state._CANDIDATE_CACHE_GENERATION
        async def load() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
            async with _CANDIDATE_LOAD_SEMAPHORE:
                result = await _load_wuwa_candidates_uncached(role_mode)
                # 代次用于识别失效竞态：失效钩子会递增代次并清空缓存，若加载期间代次已变，
                # 说明本次结果早于用户的数据变更，写回会把已删除角色重新灌入缓存，
                # 因此此处须回退为清空而非保留。
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
    source = _image_source('nte')
    # 键使用 nte: 前缀而非 local:nte：后者与 `_load_local_candidates` 使用的缓存键相同，
    # 两个加载器写入的数据结构并不一致，共用键会相互覆盖
    cache_key = f'nte:{source}'
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1], None
    task = _CANDIDATE_INFLIGHT.get(cache_key)
    if task is None:
        generation = state._CANDIDATE_CACHE_GENERATION
        async def load() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
            async with _CANDIDATE_LOAD_SEMAPHORE:
                # 是否允许远程兜底由取图来源决定：gallery 模式下本地缺图可回退官方 CDN，
                # local 模式必须保持纯本地
                candidates, error = await run_blocking(_load_nte_local_candidates, source == 'gallery')
                if error or not candidates:
                    return None, error
                CANDIDATE_CACHE[cache_key] = (time.time(), candidates)
                # 同 `_load_wuwa_candidates`：代次变化说明结果早于失效动作，必须丢弃
                if generation != state._CANDIDATE_CACHE_GENERATION:
                    CANDIDATE_CACHE.pop(cache_key, None)
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
    if role_mode == 'pgr':
        # 战双必须走专属加载器：此前该模式落入鸣潮分支，因缺少对应 ID 对照表而必然报错，
        # 零点预热在此模式下完全空转
        pgr_candidates = await _load_pgr_wife_candidates()
        if not pgr_candidates:
            return None, '战双老婆图库里还没有可用图片。'
        return pgr_candidates, None

    candidates, error = await _load_wuwa_candidates(role_mode)
    if error or not candidates:
        return None, error
    return candidates, None
