"""QQ 官方机器人发送支持：图床上传 + Markdown 构建。

官方机器人的媒体消息发不出本地 base64 图片，必须先把结果图上传到公网图床，
再用 Markdown 的 `![](url)` 引用。整块默认关闭，关闭时发送路径与普通平台
完全一致。

与参考实现 TodayWaifu-QQ 的关键差异：**不做平台自动检测**。是否走官方机器人
链路完全由配置页 `DailyWifeQQBotEnabled` 开关决定，不嗅探 bot_id 前缀。
"""
from __future__ import annotations

import json
import hashlib
from io import BytesIO
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .executor import run_blocking
from .constants import (
    _qqbot_cfg,
    _qqbot_cfg_int,
    _qqbot_cfg_bool,
)

# 官方机器人 Markdown 图片尺寸上限：超过会被客户端裁切，等比缩到该框内
MARKDOWN_IMAGE_MAX_WIDTH = 260
MARKDOWN_IMAGE_MAX_HEIGHT = 360

# 图床上传单次超时（CNB 固定 20s，COS 走配置项）
CNB_UPLOAD_TIMEOUT_SECONDS = 20


class QQBotImageError(RuntimeError):
    """图床上传或 Markdown 构建失败。"""


def qqbot_enabled() -> bool:
    """是否启用官方机器人发送链路（唯一判定入口，不看平台）。"""
    return _qqbot_cfg_bool('DailyWifeQQBotEnabled', False)


def _image_host() -> str:
    backend = str(_qqbot_cfg('DailyWifeQQBotImageHost') or 'cos').strip().lower()
    if backend in ('cos', 'cnb'):
        return backend
    return 'off'


def qqbot_image_host_ready() -> bool:
    """当前选中的图床后端是否已配置完整。"""
    backend = _image_host()
    if backend == 'cos':
        return bool(_cos_bucket() and _cos_secret_id() and _cos_secret_key())
    if backend == 'cnb':
        return bool(_cnb_repo() and _cnb_token())
    return False


# ---------------------------------------------------------------- COS 后端


def _cos_region() -> str:
    return str(_qqbot_cfg('DailyWifeCosRegion') or '').strip()


def _cos_bucket() -> str:
    return str(_qqbot_cfg('DailyWifeCosBucket') or '').strip()


def _cos_secret_id() -> str:
    return str(_qqbot_cfg('DailyWifeCosSecretId') or '').strip()


def _cos_secret_key() -> str:
    return str(_qqbot_cfg('DailyWifeCosSecretKey') or '').strip()


def _cos_path_prefix() -> str:
    return str(_qqbot_cfg('DailyWifeCosPathPrefix') or '').strip().strip('/')


def _cos_public_base() -> str:
    return str(_qqbot_cfg('DailyWifeCosPublicBase') or '').strip().rstrip('/')


def _cos_timeout() -> int:
    return _qqbot_cfg_int('DailyWifeCosTimeout', CNB_UPLOAD_TIMEOUT_SECONDS)


def _cos_public_url(key: str) -> str:
    base = _cos_public_base()
    if not base:
        bucket = _cos_bucket()
        region = _cos_region()
        if not bucket or not region:
            raise QQBotImageError('未配置 COS 存储桶或地域')
        base = f'https://{bucket}.cos.{region}.myqcloud.com'
    url = f'{base}/{key.lstrip("/")}'
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise QQBotImageError('COS 公网访问前缀不是有效的 https 地址')
    return url


def _upload_cos_sync(image: bytes, extension: str, content_type: str) -> tuple[str, tuple[int, int]]:
    """上传到腾讯云 COS，返回 (公网 URL, 原图尺寸)。

    SDK 是同步阻塞实现，调用方必须经 `run_blocking` 进入插件独立线程池。
    """
    bucket = _cos_bucket()
    region = _cos_region()
    secret_id = _cos_secret_id()
    secret_key = _cos_secret_key()
    if not bucket or not secret_id or not secret_key:
        raise QQBotImageError('未配置 COS 存储桶 / SecretId / SecretKey')

    from qcloud_cos import CosConfig, CosS3Client
    from qcloud_cos.cos_exception import CosException

    config = CosConfig(
        Region=region,
        SecretId=secret_id,
        SecretKey=secret_key,
        Scheme='https',
        Timeout=_cos_timeout(),
    )
    client = CosS3Client(config)

    digest = hashlib.sha256(image).hexdigest()[:32]
    filename = f'todaywaifu-{digest}.{extension}'
    prefix = _cos_path_prefix()
    key = f'{prefix}/{filename}' if prefix else filename

    try:
        client.put_object(
            Bucket=bucket,
            Body=image,
            Key=key,
            ContentType=content_type,
            ACL='public-read',
            EnableMD5=False,
        )
    except (CosException, OSError) as exc:
        raise QQBotImageError(f'COS 上传失败: {exc}') from exc

    return _cos_public_url(key), _image_size(image)


# ---------------------------------------------------------------- CNB 后端


def _cnb_api_base() -> str:
    return str(_qqbot_cfg('DailyWifeCnbApiBase') or 'https://api.cnb.cool').strip().rstrip('/')


def _cnb_public_base() -> str:
    return str(_qqbot_cfg('DailyWifeCnbPublicBase') or 'https://cnb.cool').strip().rstrip('/')


def _cnb_repo() -> str:
    return str(_qqbot_cfg('DailyWifeCnbRepo') or '').strip().strip('/')


def _cnb_token() -> str:
    return str(_qqbot_cfg('DailyWifeCnbToken') or '').strip()


def _upload_cnb_sync(image: bytes, extension: str, content_type: str) -> tuple[str, tuple[int, int]]:
    """上传到 CNB：先申请上传地址，再 PUT 图片本体。"""
    repo = _cnb_repo()
    token = _cnb_token()
    if not repo or not token:
        raise QQBotImageError('未配置 CNB 仓库或令牌')

    api_base = _cnb_api_base()
    digest = hashlib.sha256(image).hexdigest()[:32]
    filename = f'todaywaifu-{digest}.{extension}'
    headers = {
        'User-Agent': 'TodayWaifu/1.0',
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    request = Request(
        f'{api_base}/{repo}/-/upload/imgs',
        data=json.dumps({'name': filename, 'size': len(image)}).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    try:
        with urlopen(request, timeout=CNB_UPLOAD_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except HTTPError as exc:
        raise QQBotImageError(f'CNB 上传初始化失败，HTTP {exc.code}') from exc
    except URLError as exc:
        raise QQBotImageError(f'CNB 上传初始化失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise QQBotImageError('CNB 上传初始化超时') from exc

    try:
        payload = json.loads(raw.decode('utf-8'))
        upload_url = str(payload['upload_url']).strip()
        asset_path = str(payload['assets']['path']).strip()
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QQBotImageError('CNB 上传初始化返回格式不正确') from exc

    parsed_upload_url = urlparse(upload_url)
    if parsed_upload_url.scheme != 'https' or not parsed_upload_url.netloc:
        raise QQBotImageError('CNB 没有返回有效上传地址')

    upload_request = Request(
        upload_url,
        data=image,
        headers={'Content-Type': content_type},
        method='PUT',
    )
    try:
        with urlopen(upload_request, timeout=CNB_UPLOAD_TIMEOUT_SECONDS) as response:
            response.read()
    except HTTPError as exc:
        raise QQBotImageError(f'CNB 图片上传失败，HTTP {exc.code}') from exc
    except URLError as exc:
        raise QQBotImageError(f'CNB 图片上传失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise QQBotImageError('CNB 图片上传超时') from exc

    public_url = f'{_cnb_public_base()}/{asset_path.lstrip("/")}'
    parsed = urlparse(public_url)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise QQBotImageError('CNB 没有生成有效图片地址')
    return public_url, _image_size(image)


# ---------------------------------------------------------------- 公共上传


_IMAGE_FORMATS = {
    'JPEG': ('jpg', 'image/jpeg'),
    'PNG': ('png', 'image/png'),
    'GIF': ('gif', 'image/gif'),
    'WEBP': ('webp', 'image/webp'),
}


def _open_image(image: bytes):
    from PIL import Image

    return Image.open(BytesIO(image))


def _image_info(image: bytes) -> tuple[str, str]:
    """识别图片格式，返回 (扩展名, Content-Type)。"""
    with _open_image(image) as opened:
        image_format = str(opened.format or '').upper()
    info = _IMAGE_FORMATS.get(image_format)
    if info is None:
        raise QQBotImageError(f'图床不支持 {image_format or "未知"} 图片格式')
    return info


def _image_size(image: bytes) -> tuple[int, int]:
    with _open_image(image) as opened:
        return opened.size


def upload_image_sync(image: bytes) -> tuple[str, tuple[int, int]]:
    """按配置把图片上传到图床，返回 (公网 URL, 尺寸)。"""
    extension, content_type = _image_info(image)
    backend = _image_host()
    if backend == 'cos':
        return _upload_cos_sync(image, extension, content_type)
    if backend == 'cnb':
        return _upload_cnb_sync(image, extension, content_type)
    raise QQBotImageError('未启用图床后端（DailyWifeQQBotImageHost=off）')


async def upload_image(image: bytes) -> tuple[str, tuple[int, int]]:
    """异步上传入口：走插件独立线程池，不占用 Core 默认 executor。"""
    return await run_blocking(upload_image_sync, image)


def _markdown_size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    if width <= 0 or height <= 0:
        return MARKDOWN_IMAGE_MAX_WIDTH, MARKDOWN_IMAGE_MAX_HEIGHT
    scale = min(
        MARKDOWN_IMAGE_MAX_WIDTH / width,
        MARKDOWN_IMAGE_MAX_HEIGHT / height,
        1.0,
    )
    return max(1, round(width * scale)), max(1, round(height * scale))


def build_image_markdown(
    image_url: str,
    size: tuple[int, int],
    text: str | None,
    user_id: str | int | None,
    is_group: bool,
) -> str:
    """构建「艾特 + 文案 + 图片」的 Markdown 正文。"""
    parts: list[str] = []
    if is_group and user_id is not None and _qqbot_cfg_bool('DailyWifeQQBotAtUser', True):
        parts.append(f'<@{user_id}>')
    if text:
        parts.append(text)
    width, height = _markdown_size(size)
    parts.append(f'![image #{width}px #{height}px]({image_url})')
    return '\n\n'.join(parts)


# ---------------------------------------------------------------- 内联按钮


def _command_button(label: str, command: str):
    from gsuid_core.message_models import Button

    return Button(
        text=label,
        pressed_text=label,
        data=command,
        style=1,
        action=2,
        permisson=2,
        unsupport_tips='请升级 QQ 后再使用按钮',
    )


def build_marry_member_keyboard() -> list[list[object]] | None:
    """「娶群友」结果下方的内联按钮；开关关闭时返回 None。"""
    if not _qqbot_cfg_bool('DailyWifeQQBotKeyboard', True):
        return None
    return [
        [
            _command_button('摸头', '摸头'),
            _command_button('离婚', '离婚'),
        ],
        [
            _command_button('今日老婆', '今日老婆'),
            _command_button('今日萝莉', '今日萝莉'),
        ],
    ]


def meme_generator_url() -> str:
    return str(_qqbot_cfg('DailyWifeMemeGeneratorUrl') or '').strip().rstrip('/')


def meme_generator_ready() -> bool:
    return bool(meme_generator_url())
