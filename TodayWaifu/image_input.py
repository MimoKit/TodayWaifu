from __future__ import annotations

import base64
import hashlib
import binascii
from io import BytesIO
from typing import Protocol, runtime_checkable
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


@runtime_checkable
class MessageContent(Protocol):
    """消息段：type 决定类别，data 按类别承载字符串引用。"""

    type: str
    data: object


@runtime_checkable
class ImageBearingEvent(Protocol):
    """图片字段协议：字段可能整体缺失（历史兼容 b36eaa8），故各字段均为可选。"""

    content: list[MessageContent] | None
    image_list: list[object] | None
    image: str | None


def collect_image_refs(event: ImageBearingEvent) -> tuple[str, ...]:
    # 历史兼容（b36eaa8）：部分适配器上报的事件缺 image/image_list 字段，按“无图片”处理。
    # 三类来源可能对同一张图重复上报，故用 dict.fromkeys 去重并保留首次出现顺序；
    # 顺序稳定是必要的：调用方按序号落盘文件名，重排会让同一批上传得出不同结果。
    refs: list[str] = []
    for content in getattr(event, "content", None) or []:
        if content.type in {"image", "img"} and isinstance(content.data, str):
            ref = content.data.strip()
            if ref:
                refs.append(ref)
    for item in getattr(event, "image_list", None) or []:
        if isinstance(item, str) and item.strip():
            refs.append(item.strip())
    image = getattr(event, "image", None)
    if isinstance(image, str) and image.strip():
        refs.append(image.strip())
    return tuple(dict.fromkeys(refs))


def image_suffix_from_source(source: str) -> str:
    # 仅信任来源声明的后缀，且限定在 IMAGE_EXTENSIONS 白名单内：该函数的用途是
    # 在真正读取字节之前给出候选后缀，因此无法验证内容，只能用于命名等非安全场景。
    # 不在白名单内的后缀返回空串，交由调用方回退到内容探测，避免写出无法解码的文件。
    text = str(source or "").strip()
    if text.startswith("link://"):
        text = text[7:]
    path_text = urlparse(text).path if text.startswith(("http://", "https://")) else text
    suffix = Path(path_text.split("?", 1)[0]).suffix.lower()
    return suffix if suffix in IMAGE_EXTENSIONS else ""


# Bound decompressed work as well as transport bytes, including animated images.
# 传输字节数不足以约束解压后的工作量：一张小体积的动图或高压缩比 PNG 可以在
# 解码阶段膨胀到远超上限。故像素与帧数分别设限，任一超限即拒绝，避免单个
# 恶意或异常输入耗尽内存与 CPU（Pillow 的 DecompressionBomb 亦在此范围外兜底）。
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_FRAMES = 100
_IMAGE_FORMAT_SUFFIXES = {
    "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
    "GIF": ".gif", "BMP": ".bmp",
}


def detect_image_suffix(data: bytes, source: str) -> str:
    """Validate actual image contents; a source filename is never proof of type.

    校验包含两步：`verify()` 只检查文件结构的完整性（对 JPEG 并不解码像素，
    且会消耗 PNG 的流），因此必须重新打开并逐帧 `load()` 才能真正触发解码，
    否则「结构合法但像素损坏」的文件会被误判为可用。任一环节失败、格式不在
    白名单内、或解码过程中的累计像素越界，均返回空串表示拒绝。
    """
    try:
        with Image.open(BytesIO(data)) as image:
            suffix = _IMAGE_FORMAT_SUFFIXES.get(image.format or "", "")
            if not suffix or image.width * image.height > MAX_IMAGE_PIXELS:
                return ""
            image.verify()
        # verify() alone does not decode JPEG pixels (and consumes PNG streams).
        with Image.open(BytesIO(data)) as image:
            pixels = 0
            for frame in range(MAX_IMAGE_FRAMES):
                image.seek(frame)
                pixels += image.width * image.height
                # 累计像素而非单帧像素：多帧累加才反映真实的解码工作量。
                if pixels > MAX_IMAGE_PIXELS:
                    return ""
                image.load()
                try:
                    image.seek(frame + 1)
                except EOFError:
                    return suffix
            # 帧数达到 MAX_IMAGE_FRAMES 仍未结束，说明帧数超限：此时按拒绝处理，
            # 避免为超长动图付出无上限的解码代价。
            return ""
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        return ""


def read_image_bytes(source: str, max_bytes: int) -> tuple[bytes, str] | None:
    # 统一的失败语义：任何一项不可用（空来源、超限、格式不符、网络或文件错误）
    # 都返回 None 而不抛异常，由调用方计入单张失败并继续处理同批其他图片。
    text = str(source or "").strip()
    if not text or max_bytes <= 0:
        return None
    try:
        if text.startswith("data:image/") or text.startswith("base64://"):
            if text.startswith("data:image/"):
                header, separator, encoded = text.partition(",")
                if not separator or not header.endswith(";base64"):
                    return None
            else:
                encoded = text[9:]
            # Reject before decoding (which allocates the entire decoded buffer).
            # A base64 quartet can represent up to three bytes; account for
            # padding as well so payloads just over max_bytes never reach the
            # decoder.
            # 校验在解码之前完成：解码会一次性分配完整缓冲区，若先解码再判断
            # 长度，超限负载仍会先占满内存，体积上限便形同虚设。
            encoded_length = len(encoded)
            if encoded_length == 0 or encoded_length % 4:
                return None
            padding = len(encoded) - len(encoded.rstrip("="))
            decoded_length = (encoded_length // 4) * 3 - padding
            if (
                padding > 2
                or decoded_length < 0
                or decoded_length > max_bytes
                or encoded_length > 4 * ((max_bytes + 2) // 3)
            ):
                return None
            data = base64.b64decode(encoded, validate=True)
        else:
            if text.startswith("link://"):
                text = text[7:]
            if text.startswith(("http://", "https://")):
                # 多读一个字节用于判定是否超限；超时 15 秒，避免远端挂起占住调用方。
                request = Request(text, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(request, timeout=15) as response:
                    data = response.read(max_bytes + 1)
            else:
                path = Path(text)
                if not path.is_file():
                    return None
                with path.open("rb") as file:
                    data = file.read(max_bytes + 1)
    except (OSError, ValueError, binascii.Error, HTTPError, URLError, TimeoutError):
        return None
    # 传输错误与超限都归并为 None：调用方只需区分「可用」与「不可用」，
    # 具体原因已由底层异常路径记录，此处不额外区分以免扩大接口面。
    if not data or len(data) > max_bytes:
        return None
    suffix = detect_image_suffix(data, source)
    # 后缀由内容探测而非来源给出：来源声明的后缀不可信，探测失败即视为不支持。
    if suffix not in IMAGE_EXTENSIONS:
        return None
    return data, suffix


def image_hash_id(path: Path | str) -> str:
    # 摘要在落盘文件名上进行而非文件内容：标识只需在同一存储内区分条目，
    # 目录前缀不参与可避免迁移或缓存目录变化后已有记录引用的标识失效；
    # 截取前 8 位十六进制是在碰撞概率与存储、展示长度之间的折中。
    return hashlib.sha256(Path(path).name.encode()).hexdigest()[:8]
