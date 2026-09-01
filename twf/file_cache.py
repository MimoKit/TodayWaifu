"""TodayWaifu 文件/网络资源缓存工具。

- read_file_bytes_cached / read_file_text_cached：按 (路径, mtime, 大小) 缓存文件内容，
  文件变更后自动失效，避免 0 点高峰时反复读盘。
- read_url_cache / write_url_cache：远程图库图片按 URL 哈希落盘缓存。

本模块不依赖 gsuid_core 与 twf 内其它模块，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

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


def read_url_cache(cache_root: Path, url: str) -> Optional[bytes]:
    path = url_hash_cache_path(cache_root, url)
    try:
        if path.is_file() and path.stat().st_size > 0:
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
        return True
    except OSError:
        return False
