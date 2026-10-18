"""QQ 官方机器人模式：结果图上传腾讯云 COS，并以 Markdown 艾特触发者发送。

本模块把「官方机器人」与「普通个人账号（OB11）」的差异收敛在一处，命令与投递模块
只需表达「要发什么图、配什么文案」。总开关为配置页「QQ机器人配置」的首项
``DailyWifeQqBotMode``：

- 开启：把结果图上传到腾讯云 COS，取回可公开访问的图片地址，拼成 Markdown 发送。
  QQ 官方机器人不允许直接下发图片资源，只能下发 Markdown 中的图片链接，故必须先有
  一个平台可访问的 HTTPS 图床。
- 关闭：返回 False，交由既有 OB11 发送路径处理（``base64://`` 图片段）。

设计约定：

1. 上传失败一律返回 False 并降级为普通发送，而不是让整条命令失败。图床是外部依赖，
   其可用性不应等同于插件可用性。
2. 上传是阻塞 IO，必须经 ``run_blocking`` 投递到插件自有线程池，不得在事件循环上执行。
3. COS 客户端按配置惰性构建并缓存；配置变更后由 ``_client_signature`` 检测并重建。
"""
from __future__ import annotations

import re
import json
import hashlib
import mimetypes
from io import BytesIO
from uuid import uuid4
from typing import Union, Protocol
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Message
from gsuid_core.segment import MessageSegment

from .executor import run_blocking
from .payloads import ConfigValue
from .constants import LOG_PREFIX, _cfg_bool as _main_cfg_bool
from ..daily_wife_config import DailyWifeQqBotConfig

# GsCore 侧 QQ 官方机器人的 bot_id 前缀：群聊为 qqgroup，频道为 qqguild。
# 个人账号（OB11）为 onebot，不在其中。
QQ_OFFICIAL_BOT_IDS = {'qqgroup', 'qqguild'}

# 表情包后端的请求上限：超时按生成较慢的 GIF 预留；体积上限 12MB，
# 与旧实现的取值一致，超过即判定为异常响应而非正常表情包。
MEME_GENERATOR_TIMEOUT_SECONDS = 30
MEME_GENERATOR_MAX_BYTES = 12 * 1024 * 1024

# Markdown 图片尺寸用于给渲染端一个显示约束，取值与旧实现保持一致：
# 以 260x360 为展示上限等比缩放，仅缩小不放大，避免小图被拉伸。
MARKDOWN_IMAGE_MAX_WIDTH = 260
MARKDOWN_IMAGE_MAX_HEIGHT = 360

# 无法识别尺寸时的兜底展示尺寸。
MARKDOWN_IMAGE_FALLBACK_SIZE = (260, 360)

# 图片扩展名 → MIME 类型；COS 需要显式的 ContentType 才能被浏览器与 QQ 正确按图片渲染。
_EXTENSION_CONTENT_TYPES = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'gif': 'image/gif',
    'webp': 'image/webp',
}

# Pillow 的格式名 → 扩展名。仅支持 QQ Markdown 可稳定渲染的常见位图格式。
_PILLOW_FORMAT_EXTENSIONS = {
    'JPEG': 'jpg',
    'PNG': 'png',
    'GIF': 'gif',
    'WEBP': 'webp',
}

ClientSignature = tuple[str, str, str, str]


def _cfg(key: str) -> ConfigValue:
    """读取「QQ机器人配置」页（控制台第 3 页）的配置项。

    本页配置独立于第 1 页的 ``DailyWifeConfig``：两个实例各持一份 ``all_config_list``
    条目，用错实例取键不会报错，只会打一条 warning 并返回默认值——表现为总开关明明
    打开却始终走 OB11。故本模块所有配置读取统一经此入口。
    """
    return DailyWifeQqBotConfig.get_config(key).data


def _cfg_bool(key: str, default: bool = False) -> bool:
    """按宽松语义解析布尔配置项，无法识别时回落到默认值。

    与 ``constants._cfg_bool`` 同语义，但读取的是本页配置实例。WebConsole 的布尔项
    回传形态可能是 bool / int / 字符串，故逐一归一。
    """
    value = _cfg(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'true', '1', 'yes', 'y', 'on', 'enable', 'enabled', '开启'}:
            return True
        if text in {'false', '0', 'no', 'n', 'off', 'disable', 'disabled', '关闭'}:
            return False
    return default


class CosClient(Protocol):
    """本模块实际用到的腾讯云 COS 客户端能力子集。

    ``cos-python-sdk-v5`` 不随包提供类型标注，直接引用其类型会让本模块的类型检查
    失去意义。此处只声明真正调用的两个方法：上传对象与生成预签名下载地址。以协议
    而非 ``Any`` 或 ``type: ignore`` 表达，既保留类型链，也允许测试注入替身对象。
    """

    def put_object(
        self,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str,
    ) -> object: ...

    def get_presigned_url(
        self,
        Bucket: str,
        Key: str,
        Method: str,
        Expired: int,
    ) -> str: ...


def _qq_bot_mode_enabled() -> bool:
    """总开关：是否启用 QQ 官方机器人模式。"""
    return _cfg_bool('DailyWifeQqBotMode', False)


def _cos_region() -> str:
    return str(_cfg('DailyWifeQqBotCosRegion') or '').strip()


def _cos_bucket() -> str:
    return str(_cfg('DailyWifeQqBotCosBucket') or '').strip()


def _cos_secret_id() -> str:
    return str(_cfg('DailyWifeQqBotCosSecretId') or '').strip()


def _cos_secret_key() -> str:
    return str(_cfg('DailyWifeQqBotCosSecretKey') or '').strip()


def _cos_path_prefix() -> str:
    return str(_cfg('DailyWifeQqBotCosPathPrefix') or '').strip().strip('/')


def _cos_sign_expired() -> int:
    """图片链接有效期（秒），非法值回落为 7 天。

    该值同时是 COS 侧签名有效期：QQ 拉图时刻与生成时刻之间存在延迟，过短会导致
    用户收到「图片已过期」，故下限由配置项自身保证，此处只做非正数防护。
    """
    value = _cfg('DailyWifeQqBotCosSignExpired')
    if isinstance(value, bool) or not isinstance(value, int):
        return 604800
    return value if value > 0 else 604800


def _cos_configured() -> bool:
    """COS 四个必填项是否齐备；缺任一项都视为未配置。"""
    return bool(_cos_region() and _cos_bucket() and _cos_secret_id() and _cos_secret_key())


def _client_signature() -> ClientSignature:
    """当前 COS 凭据指纹，用于判断缓存客户端是否仍然有效。"""
    return (_cos_region(), _cos_bucket(), _cos_secret_id(), _cos_secret_key())


_COS_CLIENT: CosClient | None = None
_COS_CLIENT_SIGNATURE: ClientSignature | None = None


def _get_cos_client() -> CosClient:
    """按当前配置构建或复用 COS 客户端。

    客户端内部持有连接池，逐次新建会平白丢弃复用收益；而凭据是运行时可变配置，
    故以签名比对决定是否重建，使用户改完配置无需重启插件。
    """
    global _COS_CLIENT, _COS_CLIENT_SIGNATURE
    signature = _client_signature()
    if _COS_CLIENT is not None and _COS_CLIENT_SIGNATURE == signature:
        return _COS_CLIENT

    # 惰性导入：官方模式未启用时，本插件不应因缺少 COS SDK 而加载失败。
    from qcloud_cos import CosConfig, CosS3Client

    config = CosConfig(
        Region=signature[0],
        SecretId=signature[2],
        SecretKey=signature[3],
        Scheme='https',
    )
    _COS_CLIENT = CosS3Client(config)
    _COS_CLIENT_SIGNATURE = signature
    return _COS_CLIENT


def _is_official_qq_bot(bot: Bot) -> bool:
    """判断本次事件是否来自 QQ 官方机器人。

    平台名取自 ``real_bot_id``（形如 ``qqgroup:12345``），回落到 ``bot_id``：
    部分适配器只填充其一，两者都取可使判断对适配器版本差异保持稳定。
    """
    platform = str(bot.ev.real_bot_id or bot.ev.bot_id or '')
    return platform.split(':', 1)[0].strip().lower() in QQ_OFFICIAL_BOT_IDS


def _is_official_qq_event(ev: object) -> bool:
    """按事件判断是否来自 QQ 官方机器人（无需 Bot 实例）。

    列表类命令只有 ``Event``、拿不到 ``Bot``，故不能复用 ``_is_official_qq_bot``。
    这里额外看 ``WS_BOT_ID``：部分适配器只填该字段，漏掉会让官方机器人退回转发消息
    形态，表现为「列表变成一条合并转发」。
    """
    for attr in ('real_bot_id', 'bot_id', 'WS_BOT_ID'):
        value = getattr(ev, attr, '')
        text = str(value or '').split(':', 1)[0].strip().lower()
        if text in QQ_OFFICIAL_BOT_IDS:
            return True
    return False


def _blockquote_markdown(text: str) -> str:
    """把纯文本逐行转成 Markdown 引用块。

    列表内容整体套引用块而非直接发送：官方机器人的 Markdown 里，裸文本的多行排版
    会被折叠成一段，引用块才能保留换行与缩进。空行保留为单独的 ``>``，否则会断开
    引用块并让后续行失去样式。

    只做形态转换，条目文本由调用方传入，确保 Markdown 与纯文本两种形态内容一致。
    """
    lines = text.splitlines() or ['']
    return '\n'.join(f'> {line}' if line else '>' for line in lines)


def _image_extension_and_size(image: bytes) -> tuple[str, tuple[int, int]]:
    """识别图片扩展名与像素尺寸；格式不受支持时抛 RuntimeError。

    尺寸与格式都只有解码后才知道，而 Pillow 解码属于 CPU 工作，调用方须经线程池投递。
    """
    with Image.open(BytesIO(image)) as opened:
        image_format = str(opened.format or '').upper()
        size = opened.size
    extension = _PILLOW_FORMAT_EXTENSIONS.get(image_format)
    if extension is None:
        raise RuntimeError(f'QQ 机器人图床不支持 {image_format or "未知"} 图片格式')
    return extension, size


def _content_type_for_extension(extension: str) -> str:
    known = _EXTENSION_CONTENT_TYPES.get(extension)
    if known is not None:
        return known
    guessed, _encoding = mimetypes.guess_type(f'image.{extension}')
    return guessed or 'application/octet-stream'


def _markdown_image_size(size: tuple[int, int]) -> tuple[int, int]:
    """把原图尺寸等比缩放到 Markdown 展示上限内。

    仅缩小不放大：放大只会让低分辨率图片更模糊，并增加渲染端负担。任一边非正时
    说明尺寸不可信，直接回落到兜底值。
    """
    width, height = size
    if width <= 0 or height <= 0:
        return MARKDOWN_IMAGE_FALLBACK_SIZE
    scale = min(
        MARKDOWN_IMAGE_MAX_WIDTH / width,
        MARKDOWN_IMAGE_MAX_HEIGHT / height,
        1.0,
    )
    return max(1, round(width * scale)), max(1, round(height * scale))


def _build_object_key(image: bytes, extension: str) -> str:
    """以图片内容哈希生成对象键，使同一张图重复上传落在同一地址。

    内容寻址让重复发送天然命中同一对象，既省流量也让 QQ 侧缓存继续有效；以时间戳
    或随机数命名会使同图每次都是新资源，缓存收益归零。
    """
    digest = hashlib.sha256(image).hexdigest()[:32]
    filename = f'todaywaifu-{digest}.{extension}'
    prefix = _cos_path_prefix()
    return f'{prefix}/{filename}' if prefix else filename


def _upload_image_sync(image: bytes) -> tuple[str, tuple[int, int]]:
    """上传图片到 COS 并返回 (公网地址, 原图尺寸)。仅供线程池调用。

    返回带签名的 URL：存储桶默认为私有读写，直接拼接裸地址会得到 403，QQ 服务器
    无法拉取图片。签名地址在有效期内可匿名访问，满足平台取图需求，同时不要求把
    整个存储桶开放为公有读。
    """
    if not _cos_configured():
        raise RuntimeError('未配置腾讯云 COS（地域 / 存储桶 / SecretId / SecretKey）')

    extension, size = _image_extension_and_size(image)
    object_key = _build_object_key(image, extension)
    client = _get_cos_client()

    client.put_object(
        Bucket=_cos_bucket(),
        Key=object_key,
        Body=image,
        ContentType=_content_type_for_extension(extension),
    )
    public_url = client.get_presigned_url(
        Bucket=_cos_bucket(),
        Key=object_key,
        Method='GET',
        Expired=_cos_sign_expired(),
    )
    parsed = urlparse(public_url)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise RuntimeError('COS 没有生成有效的公网图片地址')
    return public_url, size


async def _upload_image(image: bytes) -> tuple[str, tuple[int, int]]:
    return await run_blocking(_upload_image_sync, image)


def _markdown_escape(text: str) -> str:
    """转义 Markdown 元字符，避免角色名或文案破坏结构与 @ 解析。"""
    out: list[str] = []
    for char in text:
        if char in '\\`*_[]()#>-!':
            out.append('\\')
        out.append(char)
    return ''.join(out)


def _build_image_markdown(
    image_url: str,
    size: tuple[int, int],
    text: str | None,
    user_id: str | int | None,
    is_group: bool,
) -> str:
    """拼装「艾特触发者 + 文案 + 图片」的 Markdown 正文。

    @ 只有群聊才有意义：私聊下发 ``<@id>`` 会被 QQ 原样显示成一段无意义文本。
    """
    parts: list[str] = []
    if is_group and user_id is not None:
        parts.append(f'<@{user_id}>')
    if text:
        parts.append(_markdown_escape(text))
    width, height = _markdown_image_size(size)
    parts.append(f'![image #{width}px #{height}px]({image_url})')
    return '\n\n'.join(parts)


async def _try_send_official_qq_image(
    bot: Bot,
    image: Union[bytes, bytearray],
    text: str | None,
    user_id: str | int | None,
    is_group: bool,
) -> bool:
    """官方机器人模式下尝试走「COS 上传 + Markdown」发送。

    返回 True 表示已由本路径发送完毕，调用方不得再走普通发送（否则用户收到两条）；
    返回 False 表示应降级为普通图片发送，涵盖三种情形：总开关关闭、事件来源不是官方
    机器人、上传链路失败。降级而非抛错，是为了让图床故障只影响观感，不影响功能。

    异常在此处收敛：本函数是「可选增强」的边界，其失败不应使命令整体失败。
    """
    if not _qq_bot_mode_enabled():
        return False
    if not _is_official_qq_bot(bot):
        return False
    try:
        public_url, size = await _upload_image(bytes(image))
        mention_user = user_id if _main_cfg_bool('DailyWifeAtUser', True) else None
        markdown = _build_image_markdown(public_url, size, text, mention_user, is_group)
        await bot.send(MessageSegment.markdown(markdown))
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} QQ 机器人图床 Markdown 发送失败，降级为普通图片: {exc}')
        return False
    return True


def _markdown_message(markdown: str) -> list[Message]:
    """构造 Markdown 消息段；独立成函数以便测试直接断言其结构。"""
    return MessageSegment.markdown(markdown)


def _official_markdown_inline_text(text: str, fallback: str = '这位群友') -> str:
    """把昵称压成单行并转义，供标题与正文内联使用。

    昵称可能含换行或 Markdown 元字符，直接插入会破坏标题结构或注入样式。
    """
    value = re.sub(r'\s+', ' ', str(text or '')).strip() or fallback
    return re.sub(r'([\\`*_\[\]()#>\-!])', r'\\\1', value)


def _build_petpet_notice_markdown(member_name: str) -> str:
    """摸头的提示 Markdown：正文与合规提醒分两条消息发出，此处只出提示。

    提醒内容是平台合规所需，不是装饰：表情包使用群成员头像生成，可能触发审核，
    必须在发送前明确告知使用者。
    """
    escaped = _official_markdown_inline_text(member_name)
    return (
        f'## 摸了摸 {escaped} 的头\n\n'
        '> **头像合规提醒**\n'
        '> \n'
        '> 这张表情包使用本次「娶群友」缓存的群成员头像生成。\n'
        '> 如果头像异常、侵权或涉嫌违规，可能触发 QQ 平台审核或被举报；请确认头像合规后再使用。\n\n'
        '---\n\n'
        '表情包见下一条消息。'
    )


def _build_petpet_notice_text(member_name: str) -> str:
    """非官方机器人平台的摸头提示纯文本版本。"""
    return (
        f'摸了摸 {member_name} 的头。\n'
        '头像合规提醒：这张表情包使用本次「娶群友」缓存的群成员头像生成；'
        '如果头像异常、侵权或涉嫌违规，可能触发 QQ 平台审核或被举报，请确认头像合规后再使用。'
    )


def _meme_generator_url() -> str:
    return str(_cfg('DailyWifeMemeGeneratorUrl') or '').strip().rstrip('/')


def _meme_generator_configured() -> bool:
    return bool(_meme_generator_url())


def _multipart_form_data(
    fields: dict[str, str],
    files: list[tuple[str, str, str, bytes]],
) -> tuple[bytes, str]:
    """手工拼装 multipart/form-data 请求体。

    此处不引入 requests/httpx 的 multipart 能力：本插件其余网络调用统一走标准库
    ``urlopen``（见 gallery 模块），为一处调用引入新依赖会扩大依赖面，而该报文结构
    固定且简单。boundary 用 uuid4 保证与图片二进制不冲突。
    """
    boundary = f'----TodayWaifu{uuid4().hex}'
    body_parts: list[bytes] = []
    for name, value in fields.items():
        body_parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8')
        )
    for name, filename, content_type, content in files:
        # 文件名与头部字段均来自本模块构造，但仍剔除引号与换行：这两个字符会截断
        # multipart 头部，使整个报文被后端判为非法。
        safe_filename = filename.replace('"', '_').replace('\r', '_').replace('\n', '_')
        body_parts.append(
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="{name}"; filename="{safe_filename}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'.encode('utf-8')
            + content
            + b'\r\n'
        )
    body_parts.append(f'--{boundary}--\r\n'.encode('utf-8'))
    return b''.join(body_parts), boundary


def _generate_petpet_meme_sync(avatar: bytes) -> bytes:
    """调用 meme-generator 后端生成摸头表情包并返回图片字节。仅供线程池调用。

    后端返回的可能是 GIF，因此不能按静态图处理，只校验 content-type 与体积。
    """
    base_url = _meme_generator_url()
    if not base_url:
        raise RuntimeError('未配置表情包后端地址')

    extension, _size = _image_extension_and_size(avatar)
    content_type = _content_type_for_extension(extension)
    body, boundary = _multipart_form_data(
        {'args': json.dumps({'user_infos': [], 'circle': False})},
        [('images', f'avatar.{extension}', content_type, avatar)],
    )
    request = Request(
        f'{base_url}/memes/petpet/',
        data=body,
        headers={
            'User-Agent': 'TodayWaifu/1.0',
            'Content-Type': f'multipart/form-data; boundary={boundary}',
        },
        method='POST',
    )
    try:
        with urlopen(request, timeout=MEME_GENERATOR_TIMEOUT_SECONDS) as response:
            response_type = str(response.headers.get('content-type') or '').lower()
            result = response.read(MEME_GENERATOR_MAX_BYTES + 1)
    except HTTPError as exc:
        raise RuntimeError(f'表情包后端返回 HTTP {exc.code}') from exc
    except URLError as exc:
        raise RuntimeError(f'表情包后端请求失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('表情包后端请求超时') from exc

    if not result or len(result) > MEME_GENERATOR_MAX_BYTES:
        raise RuntimeError('表情包后端返回内容为空或过大')
    if not response_type.startswith('image/'):
        raise RuntimeError('表情包后端没有返回图片')
    return result


async def _generate_petpet_meme(avatar: bytes) -> bytes:
    return await run_blocking(_generate_petpet_meme_sync, avatar)
