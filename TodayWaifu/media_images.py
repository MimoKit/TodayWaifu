"""QQ 官方「素材管理」图床：把结果图托管到平台侧，换取 ``qqbot-media://`` 引用。

QQ 官方机器人不能直接下发图片二进制，只能下发带图片地址的 Markdown。此前必须自建腾讯云
COS 才能满足这个要求；平台的「素材管理」接口提供了另一条路：图片上传到平台后拿到
``qqbot-media://image/v1/<token>`` 引用，直接写进 Markdown 即可，无需公网图床。

代价是配额与时效都归平台管：

- 单机器人最多 100 张有效素材，超出后 prepare / complete 返回 40035007；
- 图片按「最后一次被消息引用」滑动过期，90 天未被引用即失效（40035009）。

因此本模块不只是上传器，还要维护一份本地台账（``DailyWifeMediaAsset``）：
同一张图只上传一次，配额吃紧时先删「最久未被引用」的素材。台账记的是「内容哈希 →
平台图片 ID / 引用链接 / 过期时间」，于是进程重启后依然能复用，不会每发一次图就烧掉
一张配额。

设计约定：

1. 失败一律抛 :class:`MediaApiError`，由调用方（``qq_official``）决定降级，不在本层
   返回一个假的成功地址。
2. 网络与加解密属于阻塞操作，必须经 ``run_blocking`` 投递到插件线程池。
3. 上传串行化：并发上传会同时消耗配额，容易在计数上互相踩踏。
"""
from __future__ import annotations

import json
import time
import asyncio
import hashlib
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from gsuid_core.logger import logger

from .models import DailyWifeMediaAsset
from .executor import run_blocking
from .payloads import ConfigValue
from .constants import LOG_PREFIX
from .media_plan import (
    PLATFORM_MEDIA_TTL_SECONDS,
    as_int,
    is_enabled,
    clamp_quota,
    should_reuse,
    plan_evictions,
    media_file_name,
    supported_extension,
    content_type_for_extension,
)
from ..daily_wife_config import DailyWifeQqBotConfig

# 单次请求超时：换取凭证与普通查询都很快，给 20 秒足够覆盖跨境抖动。
MEDIA_HTTP_TIMEOUT = 20.0

# 直传图片的超时：单张最大 10MB，弱网下需要更宽的窗口。
MEDIA_UPLOAD_TIMEOUT = 60.0

# 列表翻页上限：上限 100 张、每页 50 条时 3 页足够；多余页数只是兜底。
MEDIA_LIST_MAX_PAGES = 4

# 凭证提前量：距过期不足 60 秒就换新的，避免卡在临界点上被平台判过期。
TOKEN_REFRESH_MARGIN = 60.0

# 凭证缓存：按 AppID 分开，多机器人部署时互不影响。
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

# 上传串行化锁：配额是按机器人算的，并发上传会同时判断「还有空位」而一起上传。
_UPLOAD_LOCK = asyncio.Lock()

# 「已启用但没填凭据」只警告一次，避免每张图都刷一条相同的日志。
_WARNED_UNCONFIGURED = False

# 平台返回「图片不存在或已被删除」：清理时遇到说明别人已删，跳过即可。
_ERROR_IMAGE_GONE = 40035008

# 平台返回「图片数量超过上限」：说明本地台账与平台计数不一致，需要先清理再重试。
_ERROR_QUOTA_EXCEEDED = 40035007

JsonPayload = dict[str, Any]


class MediaApiError(RuntimeError):
    """素材接口调用失败；``code`` 为平台业务错误码，-1 表示本地/网络层失败。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f'QQ 素材接口错误 {code}: {message}')
        self.code = code
        self.message = message


def _cfg(key: str) -> ConfigValue:
    """读取「QQ机器人配置」页（控制台第 3 页）的配置项。

    与 ``qq_official._cfg`` 同源同语义：本页独立于第 1 页的业务配置，用错实例不会报错，
    只会静默拿到默认值——表现为开关明明打开却始终走 COS。
    """
    return DailyWifeQqBotConfig.get_config(key).data


def _app_id() -> str:
    return str(_cfg('DailyWifeQqBotMediaAppId') or '').strip()


def _app_secret() -> str:
    return str(_cfg('DailyWifeQqBotMediaAppSecret') or '').strip()


def _api_base() -> str:
    base = str(_cfg('DailyWifeQqBotMediaApiBase') or '').strip().rstrip('/')
    return base or 'https://api.bot.qq.com'


def media_upload_enabled() -> bool:
    """素材图床是否启用（配置项级开关，不代表凭据齐全）。"""
    return is_enabled(_cfg('DailyWifeQqBotMediaEnabled'))


def media_configured() -> bool:
    """启用且已填好 AppID / AppSecret。"""
    return bool(_app_id() and _app_secret())


def _error_from_body(detail: str, status: int) -> MediaApiError:
    """把平台的错误响应体解析成带业务码的异常。"""
    try:
        parsed = json.loads(detail)
    except ValueError:
        return MediaApiError(-1, f'HTTP {status}: {detail[:200]}')
    if isinstance(parsed, dict):
        code = parsed.get('code')
        if code is not None:
            return MediaApiError(as_int(code, -1), str(parsed.get('message') or ''))
    return MediaApiError(-1, f'HTTP {status}')


def _request_json(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: JsonPayload | None,
    timeout: float,
) -> JsonPayload:
    """发起一次 JSON 请求并解析响应；失败抛 :class:`MediaApiError`（阻塞，须在线程池调用）。

    平台约定：业务错误通过响应体的 ``code`` 返回，HTTP 状态码仍是 200。因此「响应体里
    出现 code」即视为失败，不能只看 HTTP 状态。
    """
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')
        raise _error_from_body(detail, exc.code) from exc
    except URLError as exc:
        raise MediaApiError(-1, f'网络不可达：{exc.reason}') from exc
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MediaApiError(-1, '素材接口返回了无法解析的响应') from exc
    if not isinstance(parsed, dict):
        raise MediaApiError(-1, '素材接口返回了非对象响应')
    code = parsed.get('code')
    if code is not None:
        raise MediaApiError(as_int(code, -1), str(parsed.get('message') or ''))
    return parsed


def _fetch_token_sync(appid: str, secret: str, base: str) -> tuple[str, int]:
    """用 AppID / AppSecret 换取 access_token，返回 (凭证, 有效秒数)。"""
    data = _request_json(
        'POST',
        f'{base}/app/getAppAccessToken',
        {'Content-Type': 'application/json'},
        {'appId': appid, 'clientSecret': secret},
        MEDIA_HTTP_TIMEOUT,
    )
    token = str(data.get('access_token') or '')
    if not token:
        raise MediaApiError(-1, '未取得 access_token')
    # 文档的返回示例里 expires_in 是字符串 "7200"，故统一按宽松数字解析。
    return token, as_int(data.get('expires_in'), 7200)


async def _access_token(appid: str) -> str:
    """取凭证（带进程内缓存）。临过期 60 秒内不再使用缓存。"""
    cached = _TOKEN_CACHE.get(appid)
    if cached is not None and cached[1] - TOKEN_REFRESH_MARGIN > time.time():
        return cached[0]
    token, ttl = await run_blocking(_fetch_token_sync, appid, _app_secret(), _api_base())
    _TOKEN_CACHE[appid] = (token, time.time() + ttl)
    return token


def _auth_headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'QQBot {token}',
        'Content-Type': 'application/json',
    }


def _prepare_sync(base: str, token: str, image: bytes, extension: str, file_name: str) -> tuple[str, str]:
    """申请上传任务，返回 (upload_id, 预签名 URL)。"""
    content_type = content_type_for_extension(extension)
    data = _request_json(
        'POST',
        f'{base}/v2/media-images/prepare',
        _auth_headers(token),
        {
            'file_name': file_name,
            'file_size': len(image),
            'content_type': content_type,
            'md5': hashlib.md5(image).hexdigest(),
            'sha1': hashlib.sha1(image).hexdigest(),
        },
        MEDIA_HTTP_TIMEOUT,
    )
    upload_id = str(data.get('upload_id') or '')
    presigned_url = str(data.get('presigned_url') or '')
    if not upload_id or not presigned_url:
        raise MediaApiError(-1, '申请上传未返回 upload_id / presigned_url')
    return upload_id, presigned_url


def _put_object_sync(presigned_url: str, image: bytes, extension: str) -> str:
    """用预签名 URL 直传图片，返回 ETag（去引号后的 MD5）。"""
    content_type = content_type_for_extension(extension)
    # Content-Type 必须与 prepare 时声明的完全一致，否则 COS 直接 403。
    request = Request(
        presigned_url,
        data=image,
        headers={'Content-Type': content_type},
        method='PUT',
    )
    try:
        with urlopen(request, timeout=MEDIA_UPLOAD_TIMEOUT) as response:
            etag = response.headers.get('ETag') or ''
    except HTTPError as exc:
        raise _error_from_body(exc.read().decode('utf-8', 'replace'), exc.code) from exc
    except URLError as exc:
        raise MediaApiError(-1, f'图片直传失败：{exc.reason}') from exc
    cleaned = etag.strip().strip('"').lower()
    # 少数代理会剥掉 ETag，此时用本地算出的 MD5 兜底：平台只拿它与对象实际 ETag 比对。
    return cleaned or hashlib.md5(image).hexdigest()


def _complete_sync(base: str, token: str, upload_id: str, md5: str) -> tuple[str, str, int]:
    """完成上传，返回 (image_id, 引用链接, 有效期秒数)。"""
    data = _request_json(
        'POST',
        f'{base}/v2/media-images/complete',
        _auth_headers(token),
        {'upload_id': upload_id, 'md5': md5},
        MEDIA_HTTP_TIMEOUT,
    )
    image_id = str(data.get('image_id') or '')
    url = str(data.get('url') or '')
    if not image_id or not url:
        raise MediaApiError(-1, '完成上传未返回 image_id / url')
    return image_id, url, as_int(data.get('ttl'), 90 * 24 * 60 * 60)


def _list_images_sync(base: str, token: str, limit: int) -> tuple[list[JsonPayload], int]:
    """列出当前机器人的有效素材，返回 (条目列表, 平台计数)。"""
    images: list[JsonPayload] = []
    total = 0
    cursor = ''
    for _ in range(MEDIA_LIST_MAX_PAGES):
        url = f'{base}/v2/media-images'
        if cursor:
            url = f'{url}?cursor={quote(cursor, safe="")}'
        data = _request_json('GET', url, _auth_headers(token), None, MEDIA_HTTP_TIMEOUT)
        page = data.get('images')
        if isinstance(page, list):
            for item in page:
                if isinstance(item, dict):
                    images.append(item)
        total = as_int(data.get('total'), len(images))
        cursor = str(data.get('next_cursor') or '')
        # 已够判断配额即停止翻页：platform 计数才是准的，条目只用于挑最旧的删。
        if not cursor or len(images) >= limit:
            break
    return images, total


def _delete_sync(base: str, token: str, image_id: str) -> None:
    _request_json(
        'DELETE',
        f'{base}/v2/media-images/{quote(image_id, safe="")}/delete',
        _auth_headers(token),
        None,
        MEDIA_HTTP_TIMEOUT,
    )


async def _ensure_free_slot(appid: str, token: str, limit: int, reserve: int) -> None:
    """配额吃紧时删掉最久未被引用的素材，为本次上传腾出一张。

    判断依据取平台返回的列表与计数，而不是本地台账：台账只记本插件上传过的图，而配额
    是整台机器人共享的，别处上传的素材同样占位。
    """
    images, total = await run_blocking(_list_images_sync, _api_base(), token, limit)
    if total < limit - reserve:
        return
    victims = plan_evictions(images, limit, reserve)
    if not victims:
        logger.warning(f'{LOG_PREFIX} QQ 素材已达上限但无可删除条目（平台计数 {total}）')
        return
    deleted: list[str] = []
    for image_id in victims:
        try:
            await run_blocking(_delete_sync, _api_base(), token, image_id)
        except MediaApiError as exc:
            if exc.code != _ERROR_IMAGE_GONE:
                logger.warning(f'{LOG_PREFIX} 删除 QQ 素材失败 {image_id}: {exc.message}')
                continue
        deleted.append(image_id)
    if deleted:
        await DailyWifeMediaAsset.forget(appid, deleted)
        logger.info(f'{LOG_PREFIX} QQ 素材配额清理：删除 {len(deleted)} 张最久未用素材')


async def _upload_once(appid: str, token: str, image: bytes, extension: str, digest: str) -> tuple[str, str, int]:
    base = _api_base()
    file_name = media_file_name(extension, digest)
    upload_id, presigned_url = await run_blocking(_prepare_sync, base, token, image, extension, file_name)
    etag = await run_blocking(_put_object_sync, presigned_url, image, extension)
    return await run_blocking(_complete_sync, base, token, upload_id, etag)


def _image_meta(image: bytes) -> tuple[str, tuple[int, int]]:
    """识别扩展名与像素尺寸；复用 qq_official 的 Pillow 实现，保持两条链路一致。"""
    from .qq_official import _image_extension_and_size

    return _image_extension_and_size(image)


async def ensure_media_url(image: bytes) -> tuple[str, tuple[int, int]]:
    """返回可写进 Markdown 的素材引用与原图尺寸。

    同一张图（按内容哈希判定）在有效期内只上传一次：命中台账直接复用，避免重复占用
    配额；过期或查无记录才真正上传。
    """
    extension, size = await run_blocking(_image_meta, image)
    if not supported_extension(extension):
        raise MediaApiError(-1, f'QQ 素材库不支持 {extension} 格式')
    appid = _app_id()
    digest = hashlib.md5(image).hexdigest()
    now = int(time.time())

    asset = await DailyWifeMediaAsset.get_by_hash(appid, digest)
    if asset is not None and should_reuse(asset.expires_at, now):
        # 复用即等于「这条消息引用了它」，平台会顺延有效期，本地记的过期时间要同步跟上
        await DailyWifeMediaAsset.touch(asset.id, now, now + PLATFORM_MEDIA_TTL_SECONDS)
        return asset.url, size

    limit, reserve = clamp_quota(
        _cfg('DailyWifeQqBotMediaQuota'),
        _cfg('DailyWifeQqBotMediaReserve'),
    )
    async with _UPLOAD_LOCK:
        # 等锁期间可能已有别的任务把同一张图传好了，重复检查避免双份占用配额。
        asset = await DailyWifeMediaAsset.get_by_hash(appid, digest)
        if asset is not None and should_reuse(asset.expires_at, now):
            await DailyWifeMediaAsset.touch(asset.id, now, now + PLATFORM_MEDIA_TTL_SECONDS)
            return asset.url, size

        token = await _access_token(appid)
        await _ensure_free_slot(appid, token, limit, reserve)
        try:
            image_id, url, ttl = await _upload_once(appid, token, image, extension, digest)
        except MediaApiError as exc:
            if exc.code != _ERROR_QUOTA_EXCEEDED:
                raise
            # 台账与平台计数不一致（例如别处上传过素材）时会走到这里：再清一轮后重试一次。
            logger.warning(f'{LOG_PREFIX} 素材配额已满，清理后重试一次')
            await _ensure_free_slot(appid, token, limit, reserve)
            image_id, url, ttl = await _upload_once(appid, token, image, extension, digest)
        await DailyWifeMediaAsset.upsert(
            appid=appid,
            content_hash=digest,
            image_id=image_id,
            url=url,
            expires_at=now + ttl,
            created_at=now,
            last_used_at=now,
        )
    logger.info(f'{LOG_PREFIX} QQ 素材上传完成：{image_id}（{len(image) / 1024:.0f}KB）')
    return url, size


async def media_quota_snapshot() -> tuple[int, int]:
    """查询平台侧 (有效素材数, 上限)，用于状态展示与排障。"""
    limit, _reserve = clamp_quota(_cfg('DailyWifeQqBotMediaQuota'), _cfg('DailyWifeQqBotMediaReserve'))
    if not media_configured():
        return 0, limit
    token = await _access_token(_app_id())
    _images, total = await run_blocking(_list_images_sync, _api_base(), token, limit)
    return total, limit


def warn_if_enabled_without_credentials() -> None:
    """启用但缺凭据时提醒一次：否则会静默回落到 COS，表现为「开关打开了却没生效」。"""
    global _WARNED_UNCONFIGURED
    if _WARNED_UNCONFIGURED or not media_upload_enabled():
        return
    if media_configured():
        return
    _WARNED_UNCONFIGURED = True
    logger.warning(f'{LOG_PREFIX} 已启用 QQ 素材图床，但未填写 AppID / AppSecret，回落到腾讯云 COS')


# 供测试与外部诊断引用，避免把内部常量名写死在别处。
__all__ = [
    'MediaApiError',
    'ensure_media_url',
    'media_configured',
    'media_quota_snapshot',
    'media_upload_enabled',
    'warn_if_enabled_without_credentials',
]
