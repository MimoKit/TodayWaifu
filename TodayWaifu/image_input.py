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
    text = str(source or "").strip()
    if text.startswith("link://"):
        text = text[7:]
    path_text = urlparse(text).path if text.startswith(("http://", "https://")) else text
    suffix = Path(path_text.split("?", 1)[0]).suffix.lower()
    return suffix if suffix in IMAGE_EXTENSIONS else ""


# Bound decompressed work as well as transport bytes, including animated images.
MAX_IMAGE_PIXELS = 16_000_000
# 上传者是主人或白名单用户，放宽像素上限以容纳超宽幅面（如 13670x7215 壁纸）；
# 仍保留上限，避免解压炸弹把内存打满。
UPLOAD_IMAGE_MAX_PIXELS = 120_000_000
MAX_IMAGE_FRAMES = 100
_IMAGE_FORMAT_SUFFIXES = {
    "JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
    "GIF": ".gif", "BMP": ".bmp",
}


def detect_image_suffix(data: bytes, source: str, max_pixels: int = MAX_IMAGE_PIXELS) -> str:
    """Validate actual image contents; a source filename is never proof of type."""
    try:
        with Image.open(BytesIO(data)) as image:
            suffix = _IMAGE_FORMAT_SUFFIXES.get(image.format or "", "")
            if not suffix or image.width * image.height > max_pixels:
                return ""
            image.verify()
        # verify() alone does not decode JPEG pixels (and consumes PNG streams).
        with Image.open(BytesIO(data)) as image:
            pixels = 0
            for frame in range(MAX_IMAGE_FRAMES):
                image.seek(frame)
                pixels += image.width * image.height
                if pixels > max_pixels:
                    return ""
                image.load()
                try:
                    image.seek(frame + 1)
                except EOFError:
                    return suffix
            return ""
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        return ""


def read_image_bytes(
    source: str,
    max_bytes: int,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> tuple[bytes, str] | None:
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
    if not data or len(data) > max_bytes:
        return None
    suffix = detect_image_suffix(data, source, max_pixels)
    if suffix not in IMAGE_EXTENSIONS:
        return None
    return data, suffix


def image_hash_id(path: Path | str) -> str:
    return hashlib.sha256(Path(path).name.encode()).hexdigest()[:8]
