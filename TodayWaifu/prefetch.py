"""零点前预热图库图片，把 00:00 的抽签变成纯缓存命中。

零点高峰的根因是「日期翻转 + 全员同时抽签」：`_daily_rng` 的种子带日期，翻转后每个
用户都会抽到**新的**角色与**新的**图片 URL，磁盘缓存整体失效，所有人的请求同时转为
网络下载。此前的优化都在提升「缓存命中时有多快」，而这一刻恰恰是缓存最冷的时刻。

本模块在零点前把候选角色的图片预下载到磁盘缓存（`gallery_image_cache`），使 00:00 的
抽签直接命中。预热是**严格有界**的：限时限量、单并发、可被取消，且只在图库模式下运行。
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
    PREFETCH_DOWNLOAD_INTERVAL_SECONDS,
    _cfg_bool,
    _image_source,
)
from .file_cache import read_url_cache


async def _prefetch_once() -> dict[str, int]:
    """把候选角色的前 N 张图预热到磁盘缓存；返回统计，绝不向上抛异常。

    异常不外抛是为了让预热失败不影响插件主流程：预热仅属优化，其失败不应中断定时任务
    或触发上层重启。统计中的 ``skipped`` 用于区分「按配置跳过」与「执行但无收获」。
    """
    stats = {'roles': 0, 'downloaded': 0, 'cached': 0, 'failed': 0, 'skipped': 0}
    if not _cfg_bool('DailyWifePrefetchEnabled', True):
        stats['skipped'] = 1
        return stats
    # 只有跟随图库的功能才需要预热，本地图片源没有网络下载
    modes = tuple(mode for mode in _prefetch_modes() if _image_source(mode) == 'gallery')
    if not modes:
        stats['skipped'] = 1
        return stats

    per_role = _cfg_int('DailyWifePrefetchImagesPerRole', 2)
    if per_role <= 0:
        stats['skipped'] = 1
        return stats

    # 延迟导入：避免与 shared 的导入顺序耦合
    from .paths import _gallery_image_cache_root
    from .gallery import _download_image, _load_candidates
    from .senders import _shrink_image_sync

    cache_root = _gallery_image_cache_root()
    deadline = time.monotonic() + PREFETCH_MAX_SECONDS
    last_download_at: float | None = None

    for mode in modes:
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
                # 非 HTTP(S) 条目是本地路径或占位符，预热只处理网络图片
                if not url.startswith(('http://', 'https://')):
                    continue
                try:
                    cached = await run_blocking(read_url_cache, cache_root, url)
                    if cached is not None:
                        stats['cached'] += 1
                        # 已下载但可能还没压过（如阈值刚改过）：顺带压好，压过的只是一次读盘
                        await run_blocking(_shrink_image_sync, cached)
                        continue
                    # 限速：零点前的预热与正常服务共享带宽和文件描述符，突发并发会波及线上请求；
                    # 间隔控制使预热以固定速率推进，代价是预热总时长被拉长，故由截止时间兜底。
                    if last_download_at is not None:
                        wait = PREFETCH_DOWNLOAD_INTERVAL_SECONDS - (time.monotonic() - last_download_at)
                        if wait > 0:
                            await asyncio.sleep(wait)
                            # 限速等待可能耗尽预算，须在真正发起下载前再次检查
                            if time.monotonic() >= deadline:
                                logger.info(f'{LOG_PREFIX} 预热达到时间上限，已停止')
                                return stats
                    last_download_at = time.monotonic()
                    data = await _download_image(url)
                    # 预热时就把压缩做掉，00:00 发送只剩一次读盘
                    await run_blocking(_shrink_image_sync, data)
                    stats['downloaded'] += 1
                except (OSError, RuntimeError, TimeoutError) as exc:
                    # 单张失败即计数跳过，不重试：重试会绕过限速并挤占剩余时间预算，
                    # 而预热本就允许部分成功，未预热的图片在发送阶段仍会正常下载
                    stats['failed'] += 1
                    logger.debug(f'{LOG_PREFIX} 预热图片失败: {url}: {exc}')
    return stats


def _prefetch_modes() -> tuple[str, ...]:
    """需要预热的模式：主池始终预热，其余按各自开关。

    未启用的模式不参与预热，以免为用户根本不会用到的图库消耗带宽与缓存空间。
    """
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
        # 正处于预热窗口（23:20 ~ 23:59），立刻补跑，而不是等 24 小时
        return 1.0
    target += timedelta(days=1)
    return (target - current).total_seconds()


async def _run_prefetch_once() -> None:
    # CancelledError 必须原样向上传播以响应停机/取消，其它异常在此吞掉并记日志：
    # 预热失败不应中断循环，否则一次网络抖动会让当晚不再尝试预热
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
        if delay <= 1.0:
            # 本日窗口内已补跑，直接等待下一日，避免每秒重复全量预热。
            # 以次日 00:00 为基准计算，跨日翻转后目标时刻才落回当天的预热时刻
            next_day = datetime.now() + timedelta(days=1)
            next_day = next_day.replace(hour=0, minute=0, second=0, microsecond=0)
            delay = seconds_until_prefetch(next_day)
        logger.debug(f'{LOG_PREFIX} 下次图库预热将在 {delay / 60:.1f} 分钟后开始')
        await asyncio.sleep(delay)
        await _run_prefetch_once()
