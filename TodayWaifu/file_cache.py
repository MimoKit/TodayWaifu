"""TodayWaifu 文件/网络资源缓存工具。

- read_file_bytes_cached / read_file_text_cached：按 (路径, mtime, 大小) 缓存文件内容，
  文件变更后自动失效，避免 0 点高峰时反复读盘。
- read_url_cache / write_url_cache：远程图库图片按 URL 哈希落盘缓存。

本模块不依赖 gsuid_core 与 TodayWaifu 内其它模块，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import os
import time
import hashlib
import tempfile
from typing import Optional
from pathlib import Path
from collections import OrderedDict

# 本地文件字节缓存上限：同时限制条目数和总字节数，避免大图把核心进程内存吃满
LOCAL_BYTES_CACHE_MAX_ENTRIES = 128
LOCAL_BYTES_CACHE_MAX_BYTES = 128 * 1024 * 1024

_LOCAL_BYTES_CACHE: 'OrderedDict[str, tuple[int, int, bytes]]' = OrderedDict()
_LOCAL_BYTES_CACHE_TOTAL_BYTES = 0


def read_file_bytes_cached(path: Path) -> bytes:
    """按 (路径, mtime_ns, 大小) 缓存文件字节；文件变更后自动重新读取。"""
    stat = path.stat()
    key = str(path)
    global _LOCAL_BYTES_CACHE_TOTAL_BYTES
    cached = _LOCAL_BYTES_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        _LOCAL_BYTES_CACHE.move_to_end(key)
        return cached[2]
    data = path.read_bytes()
    previous = _LOCAL_BYTES_CACHE.pop(key, None)
    if previous is not None:
        _LOCAL_BYTES_CACHE_TOTAL_BYTES -= len(previous[2])
    _LOCAL_BYTES_CACHE[key] = (stat.st_mtime_ns, stat.st_size, data)
    _LOCAL_BYTES_CACHE.move_to_end(key)
    _LOCAL_BYTES_CACHE_TOTAL_BYTES += len(data)
    while (
        len(_LOCAL_BYTES_CACHE) > LOCAL_BYTES_CACHE_MAX_ENTRIES
        or _LOCAL_BYTES_CACHE_TOTAL_BYTES > LOCAL_BYTES_CACHE_MAX_BYTES
    ):
        _, removed = _LOCAL_BYTES_CACHE.popitem(last=False)
        _LOCAL_BYTES_CACHE_TOTAL_BYTES -= len(removed[2])
    return data


def read_file_text_cached(path: Path, encoding: str = 'utf-8') -> str:
    """按 mtime 缓存的文本读取（角色对照表等小文件）。"""
    return read_file_bytes_cached(path).decode(encoding)


def clear_file_caches() -> None:
    """清空全部内存文件缓存（测试与调试用）。"""
    global _LOCAL_BYTES_CACHE_TOTAL_BYTES
    _LOCAL_BYTES_CACHE.clear()
    _LOCAL_BYTES_CACHE_TOTAL_BYTES = 0


def url_hash_cache_path(cache_root: Path, url: str) -> Path:
    """远程图片 URL 的磁盘缓存路径（内容寻址，URL 不变则命中）。"""
    digest = hashlib.sha256(url.encode('utf-8')).hexdigest()
    return cache_root / digest


# ── 已缓存 URL 索引 ───────────────────────────────────────────────────────────
# 零点前预热只会暖每个角色的前几张图，而抽签是 `rng.choice(role.images)`：
# 角色有 10 张图、只暖 2 张的话命中率只有 20%，剩下 80% 照样在零点走网络。
# 这里维护一个「磁盘上已经有哪张图」的内存索引，抽签时优先从中挑选，
# 让预热的命中率变成 100%（仍然随机，只是随机范围收敛到已缓存的图）。
_CACHED_URL_HASHES: set[str] = set()

# 索引只用于「优先挑缓存」，丢了不影响正确性，所以用一个很粗的上限即可
CACHED_URL_INDEX_MAX = 50000


def _remember_cached_url(url: str) -> None:
    if len(_CACHED_URL_HASHES) >= CACHED_URL_INDEX_MAX:
        # 溢出就整体丢弃：只是失去提示，会退回全量图片，不影响正确性
        _CACHED_URL_HASHES.clear()
    _CACHED_URL_HASHES.add(hashlib.sha256(url.encode('utf-8')).hexdigest())


def is_url_cached(url: str) -> bool:
    """该 URL 的图片是否已在磁盘缓存里（纯内存查询，无 I/O）。"""
    return hashlib.sha256(url.encode('utf-8')).hexdigest() in _CACHED_URL_HASHES


def prefer_cached_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    """优先返回已缓存的 URL；一张都没缓存时返回原列表。"""
    cached = tuple(url for url in urls if is_url_cached(url))
    return cached or urls


def clear_cached_url_index() -> None:
    """清空索引（测试与调试用）。"""
    _CACHED_URL_HASHES.clear()


def read_url_cache(cache_root: Path, url: str) -> Optional[bytes]:
    path = url_hash_cache_path(cache_root, url)
    try:
        if path.is_file() and path.stat().st_size > 0:
            _remember_cached_url(url)
            return path.read_bytes()
    except OSError:
        return None
    return None


def clear_expired_files(cache_root: Path, max_age_seconds: float, limit: int = 1000) -> int:
    """删除缓存目录中超过 TTL 的普通文件，返回删除数量。"""
    if max_age_seconds < 0 or limit <= 0 or not cache_root.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    try:
        for path in cache_root.iterdir():
            if removed >= limit:
                break
            if not path.is_file() or path.name.endswith('.tmp') or path.name.startswith('.'):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    _CACHED_URL_HASHES.discard(path.name)
                    removed += 1
            except OSError:
                continue
    except OSError:
        return removed
    return removed


def write_url_cache(cache_root: Path, url: str, data: bytes) -> bool:
    """原子写入 URL 磁盘缓存；失败只影响缓存，不影响主流程。"""
    if not data:
        return False
    path = url_hash_cache_path(cache_root, url)
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=cache_root,
            prefix=f'.{path.name}.',
            suffix='.tmp',
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, 'wb') as file:
                file.write(data)
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        _remember_cached_url(url)
        return True
    except OSError:
        return False


def cached_url_count() -> int:
    """索引里记录的已缓存 URL 数量（可观测性用，非磁盘真实文件数）。"""
    return len(_CACHED_URL_HASHES)
