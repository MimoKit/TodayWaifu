"""零点前预热图库图片，把 00:00 的抽签变成纯缓存命中。

零点高峰的本质是「日期翻转 + 全员同时抽签」：`_daily_rng` 的种子带日期，
翻转后每个用户都会抽到**新的**角色和**新的**图片 URL，磁盘缓存全部失效，
于是所有人的请求同时变成网络下载。之前的优化都在优化「缓存命中时有多快」，
而这一刻恰恰是缓存最冷的时刻。

这里在零点前把候选角色的图片预先下载到磁盘缓存（`gallery_image_cache`），
让 00:00 的抽签直接命中。预热是**严格有界**的：限时限量、单并发、可被取消，
且只在图库模式下运行。
"""
from __future__ import annotations

import time
import asyncio
from datetime import datetime, timedelta

from gsuid_core.logger import logger

from .executor import run_blocking
from .constants import (
    LOG_PREFIX,
    PREFETCH_HOUR,
    PREFETCH_MINUTE,
    PREFETCH_MAX_SECONDS,
    PREFETCH_STARTUP_DELAY_SECONDS,
    _cfg_bool,
    _image_source,
)
from .file_cache import read_url_cache


async def _prefetch_once() -> dict[str, int]:
    """把候选角色的前 N 张图预热到磁盘缓存；返回统计，绝不向上抛异常。"""
    stats = {'roles': 0, 'downloaded': 0, 'cached': 0, 'failed': 0, 'skipped': 0}
    if not _cfg_bool('DailyWifePrefetchEnabled', True):
        stats['skipped'] = 1
        return stats
    if _image_source() != 'gallery':
        # 本地图片源没有网络下载，不需要预热
        stats['skipped'] = 1
        return stats

    per_role = _cfg_int('DailyWifePrefetchImagesPerRole', 2)
    if per_role <= 0:
        stats['skipped'] = 1
        return stats

    # 延迟导入：避免与 shared 的导入顺序耦合
    from .paths import _gallery_image_cache_root
    from .gallery import _download_image, _load_candidates

    cache_root = _gallery_image_cache_root()
    deadline = time.monotonic() + PREFETCH_MAX_SECONDS

    for mode in _prefetch_modes():
        if time.monotonic() >= deadline:
            break
        try:
            candidates, error = await _load_candidates(mode)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            logger.warning(f'{LOG_PREFIX} 预热 {mode} 候选失败: {exc}')
            continue
        if error or not candidates:
            continue

        for role in candidates:
            if time.monotonic() >= deadline:
                logger.info(f'{LOG_PREFIX} 预热达到时间上限，已停止')
                return stats
            stats['roles'] += 1
            for url in role.images[:per_role]:
                if not url.startswith(('http://', 'https://')):
                    continue
                try:
                    if await run_blocking(read_url_cache, cache_root, url) is not None:
                        stats['cached'] += 1
                        continue
                    await _download_image(url)
                    stats['downloaded'] += 1
                except (OSError, RuntimeError, TimeoutError) as exc:
                    stats['failed'] += 1
                    logger.debug(f'{LOG_PREFIX} 预热图片失败: {url}: {exc}')
    return stats


def _prefetch_modes() -> tuple[str, ...]:
    """需要预热的模式：主池始终预热，其余按各自开关。"""
    modes = ['wife']
    if _cfg_bool('DailyWifeNteEnabled', False):
        modes.append('nte')
    if _cfg_bool('DailyWifePgrEnabled', False):
        modes.append('pgr')
    return tuple(modes)


def _cfg_int(key: str, default: int) -> int:
    from .constants import _cfg

    try:
        return int(_cfg(key))
    except (TypeError, ValueError):
        return default


def seconds_until_prefetch(now: datetime | None = None) -> float:
    """距离下一次预热时刻还有多少秒。

    已过当晚预热时刻但仍在预热窗口内时返回一个很小的值，让刚启动的进程立刻补跑。
    """
    current = now or datetime.now()
    target = current.replace(hour=PREFETCH_HOUR, minute=PREFETCH_MINUTE, second=0, microsecond=0)
    if current < target:
        return (target - current).total_seconds()
    if current.hour == PREFETCH_HOUR and current.minute >= PREFETCH_MINUTE:
        # 正处于预热窗口（23:50 ~ 23:59），立刻补跑，而不是等 24 小时
        return 1.0
    target += timedelta(days=1)
    return (target - current).total_seconds()


async def _run_prefetch_once() -> None:
    started = time.monotonic()
    try:
        stats = await _prefetch_once()
    except asyncio.CancelledError:
        raise
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        logger.warning(f'{LOG_PREFIX} 图库预热失败: {exc}')
        return
    logger.info(
        f'{LOG_PREFIX} 图库预热完成，耗时 {time.monotonic() - started:.1f} 秒: '
        f'角色 {stats["roles"]}，新下载 {stats["downloaded"]}，'
        f'已缓存 {stats["cached"]}，失败 {stats["failed"]}'
    )


async def _prefetch_loop() -> None:
    # 启动后先补跑一次：重启可能发生在零点之后，那时缓存未必完整。
    # 已缓存的图会被直接跳过，所以这次补跑通常很便宜。
    await asyncio.sleep(PREFETCH_STARTUP_DELAY_SECONDS)
    await _run_prefetch_once()

    while True:
        delay = seconds_until_prefetch()
        logger.debug(f'{LOG_PREFIX} 下次图库预热将在 {delay / 60:.1f} 分钟后开始')
        await asyncio.sleep(delay)
        await _run_prefetch_once()
