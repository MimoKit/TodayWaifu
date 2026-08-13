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
from collections import OrderedDict
from pathlib import Path
from typing import Optional

# 本地文件字节缓存上限（条目数），超出后淘汰最久未使用的条目
LOCAL_BYTES_CACHE_MAX_ENTRIES = 128

_LOCAL_BYTES_CACHE: 'OrderedDict[str, tuple[int, int, bytes]]' = OrderedDict()


def read_file_bytes_cached(path: Path) -> bytes:
    """按 (路径, mtime_ns, 大小) 缓存文件字节；文件变更后自动重新读取。"""
    stat = path.stat()
    key = str(path)
    cached = _LOCAL_BYTES_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        _LOCAL_BYTES_CACHE.move_to_end(key)
        return cached[2]
    data = path.read_bytes()
    _LOCAL_BYTES_CACHE[key] = (stat.st_mtime_ns, stat.st_size, data)
    _LOCAL_BYTES_CACHE.move_to_end(key)
    while len(_LOCAL_BYTES_CACHE) > LOCAL_BYTES_CACHE_MAX_ENTRIES:
        _LOCAL_BYTES_CACHE.popitem(last=False)
    return data


def read_file_text_cached(path: Path, encoding: str = 'utf-8') -> str:
    """按 mtime 缓存的文本读取（角色对照表等小文件）。"""
    return read_file_bytes_cached(path).decode(encoding)


def clear_file_caches() -> None:
    """清空全部内存文件缓存（测试与调试用）。"""
    _LOCAL_BYTES_CACHE.clear()


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
