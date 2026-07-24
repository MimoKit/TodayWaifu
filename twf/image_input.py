from __future__ import annotations

import base64
import binascii
import hashlib
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def collect_image_refs(event: Any) -> tuple[str, ...]:
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


def detect_image_suffix(data: bytes, source: str) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return image_suffix_from_source(source)


def read_image_bytes(source: str, max_bytes: int) -> tuple[bytes, str] | None:
    text = str(source or "").strip()
    if not text:
        return None
    try:
        if text.startswith("data:image/") and "," in text:
            data = base64.b64decode(text.split(",", 1)[1], validate=False)
        elif text.startswith("base64://"):
            data = base64.b64decode(text[9:], validate=False)
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
    suffix = detect_image_suffix(data, source)
    if suffix not in IMAGE_EXTENSIONS:
        return None
    return data, suffix


def image_hash_id(path: Path | str) -> str:
    return hashlib.sha256(Path(path).name.encode()).hexdigest()[:8]
