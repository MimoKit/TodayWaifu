"""图片体积压缩，以及压缩结果的磁盘缓存。

零点高峰的 CPU 消耗集中在 Pillow 的解码与 WebP 编码：实测单张 8.9MB 立绘压到 2MB
需 1.3 秒，8 张并行在 4 核机器上需 3.8 秒，该进程（连同 Core 与其它插件）的 CPU
会被同时抢占。这段耗时全部位于 C 实现内部，用 Cython 重写 Python 层无法收回任何
时间，因此只能压缩「工作量」：

- 按 (原图内容哈希, 阈值, 格式) 将压缩结果落盘缓存：同一张图只压一次，后续发送
  仅需一次读盘。
- 先按体积比例估算目标边长再编码，通常 1~2 次编码即可达标，而非逐档尝试
  （旧实现最坏需 30 次）。
- 以线程信号量限制并发压缩数量，为 Core 保留 CPU 核。

本模块只依赖标准库与 Pillow，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import io
import os
import math
import hashlib
import tempfile
import threading
from pathlib import Path

from PIL import Image

# 输出最长边上限沿用旧实现：超过 1920 无助于显示质量，却显著放大编码耗时与传输量；
# 下限 640 用于兜底，避免极端阈值下边长被反复折半到不可读
MAX_SIDE = 1920
MIN_SIDE = 640
QUALITIES = (85, 65, 45)

# 估算目标边长时预留的余量：体积与像素数并非严格线性，按等比例估算会略微高估可压缩幅度
_SIZE_SAFETY = 0.9

# 同时进行的压缩数量上限：至少为 1，最多占用一半 CPU 核，避免压缩把全部核占满而饿死 Core
MAX_CONCURRENT_SHRINKS = max(1, (os.cpu_count() or 2) // 2)
_SHRINK_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_SHRINKS)

SHRINK_CACHE_PREFIX = 'shrink_'


def _output_format() -> str:
    # 优先 WebP：同等画质下体积更小，且保留 alpha 通道；不支持时才退回 JPEG
    return 'WEBP' if 'WEBP' in Image.SAVE else 'JPEG'


def _prepare(opened: Image.Image, fmt: str) -> Image.Image:
    has_alpha = opened.mode in ('RGBA', 'LA', 'PA') or (
        opened.mode == 'P' and 'transparency' in opened.info
    )
    if fmt == 'WEBP':
        # WebP 支持 alpha，保留透明通道
        return opened.convert('RGBA' if has_alpha else 'RGB')
    if has_alpha:
        # JPEG 会丢 alpha，合成到白底，避免透明区域变黑
        rgba = opened.convert('RGBA')
        working = Image.new('RGB', rgba.size, (255, 255, 255))
        working.paste(rgba, mask=rgba.split()[-1])
        return working
    return opened.convert('RGB')


def _encode(image: Image.Image, fmt: str, quality: int) -> bytes:
    # method=4 是 WebP 编码耗时与体积的折中档；JPEG 开启 optimize 换取更小体积
    buffer = io.BytesIO()
    if fmt == 'WEBP':
        image.save(buffer, format='WEBP', quality=quality, method=4)
    else:
        image.save(buffer, format='JPEG', quality=quality, optimize=True)
    return buffer.getvalue()


def _resized(image: Image.Image, side: int) -> Image.Image:
    # 未超限时直接复用原对象，省去一次整图拷贝；thumbnail 就地缩放，故先复制
    if max(image.size) <= side:
        return image
    copy = image.copy()
    copy.thumbnail((side, side))
    return copy


def shrink_image_bytes(raw: bytes, limit: int) -> bytes:
    """把图片压到 `limit` 字节以内；无需压缩、动图、解码失败或压不动时原样返回。

    原样返回是为保证发送链路不被压缩失败拖累：动图逐帧压缩代价过高且会丢失动画，
    解码异常（含 DecompressionBombError）说明数据不可用或存在风险，二者都应交由
    调用方按原图处理。`limit` 非正数视为不限制体积，直接放行。
    """
    if limit <= 0 or len(raw) <= limit:
        return raw
    fmt = _output_format()
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            if bool(getattr(opened, 'is_animated', False)):
                return raw
            working = _prepare(opened, fmt)
    except Exception:
        # 解码失败（含 DecompressionBombError）一律原样返回，不影响发送
        return raw

    side = min(MAX_SIDE, max(working.size))
    while True:
        candidate = _resized(working, side)
        data = b''
        for quality in QUALITIES:
            data = _encode(candidate, fmt, quality)
            if len(data) <= limit:
                return data
        if side <= MIN_SIDE:
            return raw
        # 体积大致与像素数成正比：按最后一次（最低画质）的结果估算下一档边长，
        # 至少缩小 20%，保证循环快速收敛
        ratio = math.sqrt(limit / len(data) * _SIZE_SAFETY)
        side = max(MIN_SIDE, int(side * min(ratio, 0.8)))


def shrink_cache_path(cache_root: Path, raw: bytes, limit: int) -> Path:
    # 键包含内容哈希、阈值与输出格式：任一变化都代表不同的压缩结果，不可互相复用
    digest = hashlib.sha256(raw).hexdigest()
    return cache_root / f'{SHRINK_CACHE_PREFIX}{_output_format().lower()}_{limit}_{digest}'


def _write_atomic(path: Path, data: bytes) -> None:
    # 同目录临时文件 + 原子替换：并发读者永远看到完整的旧内容或新内容，不会读到半截文件
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def shrink_image_cached(raw: bytes, limit: int, cache_root: Path | None) -> bytes:
    """带磁盘缓存与并发上限的压缩（阻塞，须在线程池里调用）。

    缓存仅用于加速：读写失败都退回直接压缩，不影响发送。
    原图本身已达标时不落盘，避免无意义地复制一份。
    并发上限的作用是限制同一时刻的解码与编码数量，防止压缩占满 CPU 而拖慢 Core；
    阻塞语义要求调用方在线程池中执行，否则会卡住事件循环。
    """
    if limit <= 0 or len(raw) <= limit:
        return raw
    path = shrink_cache_path(cache_root, raw, limit) if cache_root is not None else None
    if path is not None:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path.read_bytes()
        except OSError:
            pass
    with _SHRINK_SLOTS:
        data = shrink_image_bytes(raw, limit)
    if path is not None and data is not raw:
        try:
            _write_atomic(path, data)
        except OSError:
            pass
    return data
