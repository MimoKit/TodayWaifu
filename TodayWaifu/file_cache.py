"""TodayWaifu 的文件与网络资源缓存层。

本地文件缓存以 (路径, mtime_ns, 大小) 判定条目是否仍然有效：源文件被替换或重写时
判据随之变化，因此可以在零点高峰省去重复读盘，而无须承担读到陈旧内容的风险。

远程图库图片以 URL 为键落盘，缓存键取「归一化后的 URL」的哈希：签名轮换既不导致
同一张图片被重复下载，也不导致磁盘上出现多份副本。驱逐由 TTL 与总容量两条策略共同
约束，避免图库 URL 变化时缓存无界增长。

本模块不依赖 gsuid_core 与 TodayWaifu 内其它模块，可独立加载（测试用 importlib
直接加载）。
"""
from __future__ import annotations

import os
import time
import hashlib
import tempfile
from typing import Optional
from pathlib import Path
from collections import OrderedDict
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

# 本地文件字节缓存上限：条目数与总字节数双重约束，两者取较严者触发淘汰，
# 避免单张大图或大量小文件任一维度失控后占满核心进程内存
LOCAL_BYTES_CACHE_MAX_ENTRIES = 128
LOCAL_BYTES_CACHE_MAX_BYTES = 128 * 1024 * 1024

_LOCAL_BYTES_CACHE: 'OrderedDict[str, tuple[int, int, bytes]]' = OrderedDict()
_LOCAL_BYTES_CACHE_TOTAL_BYTES = 0


def read_file_bytes_cached(path: Path) -> bytes:
    """读取文件字节，命中判据为 (路径, mtime_ns, 大小)。

    采用 mtime_ns 而非秒级 mtime：同一秒内被重写的文件必须判为失效，否则会返回上一版
    内容。命中时 move_to_end 维持 LRU 顺序，使淘汰总是从最久未使用的一端开始。

    重新读取时先扣除同键旧条目的字节数再加入新值，否则反复改写同一路径会令总量计数
    虚高并触发无效驱逐。
    """
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
    """以文本形式读取缓存文件，用于角色对照表等体积小、读取频繁的资源。

    解码发生在缓存命中之后，字节层与编码层不共享状态，因此同一路径以不同编码读取
    不会相互串扰；代价是每次命中都会重新解码，仅适用于小文件。
    """
    return read_file_bytes_cached(path).decode(encoding)


def clear_file_caches() -> None:
    """清空内存缓存，供测试与调试隔离状态使用。

    字典与字节计数必须同时归零，否则后续淘汰会以虚高的总量为依据，提前驱逐仍有效的
    条目。
    """
    global _LOCAL_BYTES_CACHE_TOTAL_BYTES
    _LOCAL_BYTES_CACHE.clear()
    _LOCAL_BYTES_CACHE_TOTAL_BYTES = 0


# 图库表达「一次性授权」的查询参数集合：其取值随签名轮换而变，但指向的资源未变。
# 覆盖短期签名（如 ?e=<过期时间>&s=<签名>）与把令牌直接放入查询串的图库；比较时不区分大小写。
_VOLATILE_QUERY_PARAMS = frozenset({'e', 's', 'token', 'sig', 'signature', 'expires', 'expire'})


def canonical_url(url: str) -> str:
    """剔除随时间变化的授权参数，得到可长期复用的资源标识。

    图库常为图片 URL 附加短期签名（`?e=<过期>&s=<签名>`）：签名按周期轮换，图片本身
    并未改变。若直接以原始 URL 作缓存键，签名一换即导致整库缓存失效——零点前的预热
    全部作废，磁盘还会按新键把同一张图片重复保存一份。

    仅剔除 `_VOLATILE_QUERY_PARAMS` 中已知的易变参数，其余参数原样保留，以免把语义
    不同的资源错误归并为同一缓存键；不含易变参数时原样返回，故旧式无签名 URL 的缓存
    键与历史完全一致，已缓存的图片不会因此失效。

    解析失败（非法 URL）时返回原字符串：宁可退化为较弱的归一化，也不因异常中断调用方。
    """
    text = str(url or '')
    if not text:
        return text
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.query:
        return text
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(key, value) for key, value in pairs if key.lower() not in _VOLATILE_QUERY_PARAMS]
    if len(kept) == len(pairs):
        return text
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def _cache_key(url: str) -> str:
    """计算图片缓存键，为归一化 URL 的 SHA-256 摘要。

    以摘要而非原文作键，可避免 URL 中的路径分隔符与超长查询串直接进入文件系统路径，
    同时使键长固定、便于作为磁盘文件名。
    """
    return hashlib.sha256(canonical_url(url).encode('utf-8')).hexdigest()


def url_hash_cache_path(cache_root: Path, url: str) -> Path:
    """定位远程图片的落盘路径（内容寻址）。

    同一张图片无论签名如何轮换都映射到同一文件，因此缓存不会因签名变化而重复膨胀；
    文件名即缓存键，便于清理时反向剔除内存索引。
    """
    return cache_root / _cache_key(url)


# ── 已缓存 URL 索引 ───────────────────────────────────────────────────────────
# 零点前的预热只覆盖每个角色的前几张图，而抽签为 `rng.choice(role.images)`：
# 角色有 10 张图、仅预热 2 张时命中率只有 20%，其余 80% 仍在零点访问网络。
# 此索引记录磁盘上已存在哪些图，抽签时优先从中挑选，使预热命中率达到 100%
# （随机性保留，仅将随机范围收敛到已缓存集合）。
_CACHED_URL_HASHES: set[str] = set()

# 索引仅用于「优先挑选已缓存项」，丢失不影响正确性，故上限可取得较宽松
CACHED_URL_INDEX_MAX = 50000


def _remember_cached_url(url: str) -> None:
    if len(_CACHED_URL_HASHES) >= CACHED_URL_INDEX_MAX:
        # 溢出时整体丢弃：仅失去优化提示，会退回全量图片，不影响正确性
        _CACHED_URL_HASHES.clear()
    _CACHED_URL_HASHES.add(_cache_key(url))


def is_url_cached(url: str) -> bool:
    """判断该 URL 对应的图片是否已落盘，为纯内存查询，不产生 I/O。

    归一化规则与磁盘缓存一致，因此列表下发新签名的 URL 时，仍能命中此前按旧签名下载
    的同一张图片；索引缺失只会退化为「按原列表随机」，不会产生错误结果。
    """
    return _cache_key(url) in _CACHED_URL_HASHES


def prefer_cached_urls(urls: tuple[str, ...]) -> tuple[str, ...]:
    """返回其中已缓存的 URL，无一命中时返回原列表。

    必须保证返回值非空：调用方直接以其为抽签候选集，空元组会使随机选取抛出异常。
    """
    cached = tuple(url for url in urls if is_url_cached(url))
    return cached or urls


def clear_cached_url_index() -> None:
    """清空已缓存 URL 索引，供测试与调试使用。"""
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
    """按 TTL 驱逐缓存目录中的常规文件，返回实际删除数量。

    仅处理普通文件，跳过临时文件（`.tmp`）与隐藏文件：前者可能正被并发写入，后者多为
    外部工具的中间产物。单次调用受 `limit` 约束，避免清理动作长时间占用 I/O；单个文件
    删除失败仅跳过该文件，不中断整轮清理。删除成功后同步剔除内存索引，使索引不会指向
    已不存在的文件。
    """
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
    """以临时文件加原子替换的方式写入 URL 缓存，返回是否写入成功。

    先写 `.tmp` 再 `os.replace`，使读端要么看到完整的旧文件、要么看到完整的新文件，
    不会读到半截内容。任何 OSError 均被吞掉并返回 False：缓存写入属于降级路径，
    不应因磁盘问题导致主流程（如发送图片）失败。
    """
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
    """返回索引中记录的已缓存 URL 数量，用于可观测性。

    该值不等于磁盘上的真实文件数：索引可能因外部删除而偏大，也可能因进程重启而清零。
    """
    return len(_CACHED_URL_HASHES)


def enforce_cache_size_budget(cache_root: Path, max_bytes: int, limit: int = 5000) -> int:
    """把缓存目录的总字节数压缩到 `max_bytes` 以内，按 mtime 从旧到新删除。

    图库图片缓存原先只有「按天过期」一条清理策略，缺少总量上限：只要图库返回的 URL
    持续变化（带签名或时间戳），缓存就会无界增长直至占满磁盘。此处作为兜底，以最旧
    优先的顺序释放空间；每删除一个文件即同步剔除内存索引。单次删除数量受 `limit`
    约束，避免清理过程长时间占用 I/O。

    目录不可读或参数非法时返回 0：容量治理属于后台维护动作，失败不应中断调用方。
    """
    if max_bytes <= 0 or limit <= 0 or not cache_root.is_dir():
        return 0

    entries: list[tuple[float, int, Path]] = []
    total = 0
    try:
        for path in cache_root.iterdir():
            if not path.is_file() or path.name.endswith('.tmp') or path.name.startswith('.'):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
    except OSError:
        return 0

    if total <= max_bytes:
        return 0

    entries.sort(key=lambda item: item[0])
    removed = 0
    for _mtime, size, path in entries:
        if total <= max_bytes or removed >= limit:
            break
        try:
            path.unlink()
        except OSError:
            continue
        _CACHED_URL_HASHES.discard(path.name)
        total -= size
        removed += 1
    return removed


def cache_dir_bytes(cache_root: Path) -> int:
    """统计缓存目录当前占用的总字节数，供可观测性与容量决策使用。

    过滤规则与驱逐逻辑保持一致（排除临时文件与隐藏文件），故该统计值可直接与容量
    阈值比较；目录不可读时返回 0，调用方据此不会误判为超限而触发驱逐。
    """
    if not cache_root.is_dir():
        return 0
    total = 0
    try:
        for path in cache_root.iterdir():
            if not path.is_file() or path.name.endswith('.tmp') or path.name.startswith('.'):
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total
