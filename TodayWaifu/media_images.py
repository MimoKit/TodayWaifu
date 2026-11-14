"""QQ 官方「素材管理」图床：把结果图托管到平台侧，换取 ``qqbot-media://`` 引用。

QQ 官方机器人不能直接下发图片二进制，只能下发带图片地址的 Markdown。开放平台的
「素材管理」接口提供了图床：图片上传后拿到 ``qqbot-media://image/v1/<token>`` 引用，
直接写进 Markdown 即可，无需自建公网图床。

代价是配额与时效都归平台管：

- 单机器人最多 100 张有效素材，超出后 prepare / complete 返回 40035007；
- 图片按「最后一次被消息引用」滑动过期，90 天未被引用即失效（40035009）。

因此本模块不只是上传器，还要维护一份本地台账（``DailyWifeMediaAsset``）：
同一张图只上传一次，配额吃紧时先删「最久未被引用」的素材。台账记的是「内容哈希 →
平台图片 ID / 引用链接 / 过期时间」，于是进程重启后依然能复用，不会每发一次图就烧掉
一张配额。

上传流程本身（签名计算、三步预签名、错误码映射）由 ``qqbot-md-sdk``（``qgbot``）
实现。本模块只补两件 SDK 明确不负责的事：一是它要求的 ``Transport``——把 SDK 的网络
调用落到插件自有线程池与标准库 ``urlopen`` 上；二是凭证获取与平台配额调度。

设计约定：

1. 失败一律抛 :class:`qgbot.media.MediaError`，由调用方（``qq_official``）决定降级，
   不在本层返回一个假的成功地址。
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

from qgbot.media import (
    PATH_LIST,
    MediaError,
    delete_image,
    upload_image,
)

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


def _cfg(key: str) -> ConfigValue:
    """读取「QQ机器人配置」页（控制台第 3 页）的配置项。

    与 ``qq_official._cfg`` 同源同语义：本页独立于第 1 页的业务配置，用错实例不会报错，
    只会静默拿到默认值——表现为开关明明打开却始终不上传素材。
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


def _headers(token: str) -> dict[str, str]:
    """请求头。凭证为空时不带 Authorization——换取凭证的接口本身不需要鉴权。"""
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'QQBot {token}'
    return headers


def _parse_response(raw: bytes, status: int) -> JsonPayload:
    """解析响应体；失败抛 :class:`MediaError`。

    平台约定：业务错误通过响应体的 ``code`` 返回，HTTP 状态码仍是 200。因此「响应体里
    出现 code」即视为失败，不能只看 HTTP 状态。
    """
    body: JsonPayload = {}
    if raw:
        try:
            decoded = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise MediaError(-1, '素材接口返回了无法解析的响应') from exc
        if not isinstance(decoded, dict):
            raise MediaError(-1, '素材接口返回了非对象响应')
        body = decoded
    code = body.get('code')
    if code is not None:
        raise MediaError(as_int(code, -1), str(body.get('message') or ''))
    if not 200 <= status < 300:
        raise MediaError(-1, f'HTTP {status}')
    return body


def _request_sync(
    url: str,
    method: str,
    token: str,
    timeout: float,
    payload: JsonPayload | None = None,
) -> tuple[int, JsonPayload]:
    """发起一次 JSON 请求并解析响应（阻塞，须在线程池调用）。

    POST / GET / DELETE 共用一条解析路径：失败一律抛 :class:`MediaError`，成功返回
    ``(状态码, 响应体)``。
    """
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = Request(url, data=body, headers=_headers(token), method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, _parse_response(response.read(), response.status)
    except HTTPError as exc:
        # _parse_response 对非 2xx 一定抛错，故此处无需再补一条 raise。
        _parse_response(exc.read(), exc.code)
        raise MediaError(-1, f'HTTP {exc.code}') from exc
    except URLError as exc:
        raise MediaError(-1, f'网络不可达：{exc.reason}') from exc


def _put_bytes_sync(url: str, data: bytes, content_type: str, timeout: float) -> tuple[int, dict[str, str]]:
    """用预签名地址直传图片，返回带 ETag 的响应头（阻塞，须在线程池调用）。

    Content-Type 必须与 prepare 时声明的完全一致，否则对象存储直接 403。
    """
    request = Request(
        url,
        data=data,
        headers={'Content-Type': content_type},
        method='PUT',
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            etag = str(response.headers.get('ETag') or '')
    except HTTPError as exc:
        raise MediaError(exc.code, f'上传到对象存储失败（HTTP {exc.code}）') from exc
    except URLError as exc:
        raise MediaError(-1, f'图片直传失败：{exc.reason}') from exc
    cleaned = etag.strip().strip('"').lower()
    # 少数代理会剥掉 ETag，此时用本地算出的 MD5 兜底：平台只拿它与对象实际 ETag 比对。
    return status, {'ETag': cleaned or hashlib.md5(data).hexdigest()}


class _MediaTransport:
    """``qgbot.media.Transport`` 的插件实现：线程池 + 标准库 ``urlopen``。

    SDK 不碰网络，只把「该发什么」算好；凭证与连接策略留给调用方。凭证因此绑在实例
    上——SDK 的接口签名里没有鉴权参数，而每个机器人的配额与令牌都是独立的。
    """

    def __init__(self, token: str) -> None:
        self._token = token
        self._base = _api_base()

    async def post_json(self, path: str, body: JsonPayload) -> tuple[int, JsonPayload]:
        return await run_blocking(
            _request_sync, f'{self._base}{path}', 'POST', self._token, MEDIA_HTTP_TIMEOUT, body
        )

    async def put_bytes(self, url: str, data: bytes, content_type: str) -> tuple[int, dict[str, str]]:
        return await run_blocking(_put_bytes_sync, url, data, content_type, MEDIA_UPLOAD_TIMEOUT)

    async def delete(self, path: str) -> tuple[int, JsonPayload]:
        return await run_blocking(
            _request_sync, f'{self._base}{path}', 'DELETE', self._token, MEDIA_HTTP_TIMEOUT
        )


def _fetch_token_sync(appid: str, secret: str, base: str) -> tuple[str, int]:
    """用 AppID / AppSecret 换取 access_token，返回 (凭证, 有效秒数)。"""
    _status, data = _request_sync(
        f'{base}/app/getAppAccessToken',
        'POST',
        '',
        MEDIA_HTTP_TIMEOUT,
        {'appId': appid, 'clientSecret': secret},
    )
    token = str(data.get('access_token') or '')
    if not token:
        raise MediaError(-1, '未取得 access_token')
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


def _list_images_sync(base: str, token: str, limit: int) -> tuple[list[JsonPayload], int]:
    """列出当前机器人的有效素材，返回 (条目列表, 平台计数)。

    条目按原始响应返回而不是映射成 SDK 的 ``MediaImage``：腾挪配额要按 ``expires_at``
    排序，而 SDK 的图片模型不含过期时间——那层映射在这里只会丢掉决策所需的信息。
    """
    images: list[JsonPayload] = []
    total = 0
    cursor = ''
    for _ in range(MEDIA_LIST_MAX_PAGES):
        path = PATH_LIST
        if cursor:
            path = f'{path}?cursor={quote(cursor, safe="")}'
        _status, data = _request_sync(f'{base}{path}', 'GET', token, MEDIA_HTTP_TIMEOUT)
        page = data.get('images')
        if isinstance(page, list):
            for item in page:
                if isinstance(item, dict):
                    images.append(item)
        total = as_int(data.get('total'), len(images))
        cursor = str(data.get('next_cursor') or '')
        # 已够判断配额即停止翻页：平台计数才是准的，条目只用于挑最旧的删。
        if not cursor or len(images) >= limit:
            break
    return images, total


async def _ensure_free_slot(token: str, limit: int, reserve: int) -> None:
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
    transport = _MediaTransport(token)
    for image_id in victims:
        try:
            # SDK 把 image_id 原样拼进路径，故在此先做一次 URL 编码：
            # 平台若给出带特殊字符的 ID，裸拼会得到 404 而不是删除成功。
            await delete_image(image_id=quote(image_id, safe=''), transport=transport)
        except MediaError as exc:
            if exc.code != _ERROR_IMAGE_GONE:
                logger.warning(f'{LOG_PREFIX} 删除 QQ 素材失败 {image_id}: {exc.message}')
                continue
        deleted.append(image_id)
    if deleted:
        await DailyWifeMediaAsset.forget(_app_id(), deleted)
        logger.info(f'{LOG_PREFIX} QQ 素材配额清理：删除 {len(deleted)} 张最久未用素材')


async def _upload_once(transport: _MediaTransport, image: bytes, extension: str, digest: str) -> tuple[str, str, int]:
    """跑一遍 SDK 的三步上传，返回 (image_id, 引用链接, 有效期秒数)。"""
    try:
        media = await upload_image(
            data=image,
            file_name=media_file_name(extension, digest),
            transport=transport,
        )
    except ValueError as exc:
        # SDK 在本地就拦下超限 / 空内容 / 不支持的格式，统一收敛成本层的失败类型。
        raise MediaError(-1, str(exc)) from exc
    return media.image_id, media.url, media.ttl


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
        raise MediaError(-1, f'QQ 素材库不支持 {extension} 格式')
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
        transport = _MediaTransport(token)
        await _ensure_free_slot(token, limit, reserve)
        try:
            image_id, url, ttl = await _upload_once(transport, image, extension, digest)
        except MediaError as exc:
            if exc.code != _ERROR_QUOTA_EXCEEDED:
                raise
            # 台账与平台计数不一致（例如别处上传过素材）时会走到这里：再清一轮后重试一次。
            logger.warning(f'{LOG_PREFIX} 素材配额已满，清理后重试一次')
            await _ensure_free_slot(token, limit, reserve)
            image_id, url, ttl = await _upload_once(transport, image, extension, digest)
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
    """启用但缺凭据时提醒一次：否则会静默不发图，表现为「开关打开了却没生效」。"""
    global _WARNED_UNCONFIGURED
    if _WARNED_UNCONFIGURED or not media_upload_enabled():
        return
    if media_configured():
        return
    _WARNED_UNCONFIGURED = True
    logger.warning(
        f'{LOG_PREFIX} 已启用 QQ 素材图床，但未填写 AppID / AppSecret，'
        'QQ 官方机器人将无法发送图片'
    )


# 供测试与外部诊断引用，避免把内部常量名写死在别处。
__all__ = [
    'MediaError',
    'ensure_media_url',
    'media_configured',
    'media_quota_snapshot',
    'media_upload_enabled',
    'warn_if_enabled_without_credentials',
]
