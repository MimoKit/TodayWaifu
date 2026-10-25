"""零点前预热图库图片，把 00:00 的抽签变成纯缓存命中。

日期翻转后每人重新抽签，新一天抽到的角色与图片与前一天无关，磁盘缓存对新一天整体
失效，所有人的请求会同时转为网络下载。此前的优化都在提升「缓存命中时有多快」，而这
一刻恰恰是缓存最冷的时刻。

本模块在零点前把候选角色的图片预下载到磁盘缓存（`gallery_image_cache`），使 00:00 的
抽签直接命中。预热是**严格有界**的：限时限量、单并发、可被取消、只在图库模式下运行，
并且**同一天最多成功一次** —— 重启不会让整库重新下载一遍。
"""

from __future__ import annotations

import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

from gsuid_core.logger import logger

from .executor import run_blocking
from .constants import (
    LOG_PREFIX,
    PREFETCH_HOUR,
    PREFETCH_MINUTE,
    PREFETCH_TASK_NAME,
    PREFETCH_MAX_SECONDS,
    PREFETCH_STATE_FILE_NAME,
    PREFETCH_STARTUP_DELAY_SECONDS,
    PREFETCH_DOWNLOAD_INTERVAL_SECONDS,
    _cfg_bool,
    _image_source,
    _now_shanghai,
)
from .file_cache import has_url_cache

# 预热统计的固定形状：所有键都必须先出现，调用方无需再判断键是否存在。
_EMPTY_STATS = {
    'roles': 0,
    'downloaded': 0,
    'cached': 0,
    'failed': 0,
    'skipped': 0,
    'truncated': 0,
}


async def _prefetch_once() -> dict[str, int]:
    """把候选角色的前 N 张图预热到磁盘缓存；返回统计，绝不向上抛异常。

    异常不外抛是为了让预热失败不影响插件主流程：预热仅属优化，其失败不应中断定时任务
    或触发上层重启。统计中的 ``skipped`` 用于区分「按配置跳过」与「执行但无收获」，
    ``truncated`` 表示撞到时间上限（本轮没走完，下次窗口可继续）。
    """
    stats = dict(_EMPTY_STATS)
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

    # 预算同时受墙钟上限与零点约束：跨过零点的预热已无意义，还会与早高峰抢带宽
    budget = min(float(PREFETCH_MAX_SECONDS), seconds_until_midnight())
    if budget <= 0:
        stats['skipped'] = 1
        return stats

    # 延迟导入：避免与 shared 的导入顺序耦合
    from .paths import _gallery_image_cache_root
    from .gallery import _download_image, _load_candidates
    from .senders import _shrink_image_sync

    cache_root = _gallery_image_cache_root()
    deadline = time.monotonic() + budget
    last_download_at: float | None = None

    for mode in modes:
        if time.monotonic() >= deadline:
            stats['truncated'] = 1
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
                logger.info(f'{LOG_PREFIX} 预热达到时间上限，本轮未走完')
                stats['truncated'] = 1
                return stats
            stats['roles'] += 1
            for url in role.images[:per_role]:
                # 非 HTTP(S) 条目是本地路径或占位符，预热只处理网络图片
                if not url.startswith(('http://', 'https://')):
                    continue
                try:
                    # 只探测落盘与否，不读内容：命中时若把整张图读进内存再压一遍，
                    # 一次重启补跑就会按张数累积出可观的读盘与 CPU
                    if await run_blocking(has_url_cache, cache_root, url):
                        stats['cached'] += 1
                        continue
                    # 限速：零点前的预热与正常服务共享带宽和文件描述符，突发并发会波及线上请求；
                    # 间隔控制使预热以固定速率推进，代价是预热总时长被拉长，故由截止时间兜底。
                    if last_download_at is not None:
                        wait = PREFETCH_DOWNLOAD_INTERVAL_SECONDS - (time.monotonic() - last_download_at)
                        if wait > 0:
                            await asyncio.sleep(wait)
                            # 限速等待可能耗尽预算，须在真正发起下载前再次检查
                            if time.monotonic() >= deadline:
                                logger.info(f'{LOG_PREFIX} 预热达到时间上限，本轮未走完')
                                stats['truncated'] = 1
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


def in_prefetch_window(now: datetime | None = None) -> bool:
    """当前是否处于零点前的预热窗口内（窗口结束于零点）。"""
    current = now or _now_shanghai()
    if current.hour != PREFETCH_HOUR:
        return False
    return current.minute >= PREFETCH_MINUTE


def seconds_until_prefetch(now: datetime | None = None) -> float:
    """距离下一次预热时刻还有多少秒。

    已进入当晚预热窗口时返回一个很小的值，让刚启动的进程立刻补跑。
    """
    current = now or _now_shanghai()
    target = current.replace(hour=PREFETCH_HOUR, minute=PREFETCH_MINUTE, second=0, microsecond=0)
    if current < target:
        return (target - current).total_seconds()
    if in_prefetch_window(current):
        # 正处于预热窗口（23:20 ~ 23:59），立刻补跑，而不是等 24 小时
        return 1.0
    target += timedelta(days=1)
    return (target - current).total_seconds()


def seconds_until_midnight(now: datetime | None = None) -> float:
    """距离下一个零点还有多少秒，用于把预热预算截断在零点前。"""
    current = now or _now_shanghai()
    midnight = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0.0, (midnight - current).total_seconds())


def next_sleep_seconds(now: datetime | None = None) -> float:
    """调度循环下一次该睡多久（从当前时刻起算）。

    已在窗口内时直接算到**明天**的窗口起点：本日窗口的机会已经用掉，沿用窗口内的极小
    延迟会立刻再跑一轮，形成连续预热。此处必须相对 `current` 计算——若先把基准挪到次日
    零点再取差值，`asyncio.sleep` 仍从当前时刻开始计时，醒来会落在窗口之前。
    """
    current = now or _now_shanghai()
    delay = seconds_until_prefetch(current)
    if delay > 1.0:
        return delay
    tomorrow_start = (current + timedelta(days=1)).replace(
        hour=PREFETCH_HOUR, minute=PREFETCH_MINUTE, second=0, microsecond=0
    )
    return (tomorrow_start - current).total_seconds()


def _state_path() -> Path:
    from .paths import _custom_upload_data_root

    return _custom_upload_data_root() / PREFETCH_STATE_FILE_NAME


def _state_read(path: Path) -> dict[str, str]:
    """读取预热状态；文件缺失、损坏或字段类型不符一律视为「今天没预热过」。"""
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    state: dict[str, str] = {}
    for key in ('day', 'status'):
        value = payload.get(key)
        if isinstance(value, str):
            state[key] = value
    return state


def _state_write(path: Path, payload: dict[str, str | int]) -> bool:
    """原子写入预热状态并返回是否成功；失败只是丢一次记录，不影响预热本身。"""
    temporary = path.with_name(f'{path.name}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    except OSError:
        return False
    return True


def _state_is_done(state: dict[str, str], day: str) -> bool:
    """状态是否为「指定日期已完成」；只有 done 才算完成，partial 仍需下一轮补。"""
    return state.get('day') == day and state.get('status') == 'done'


def _prefetch_done_today() -> bool:
    """今天是否已成功完成一次预热。状态落盘，因此重启后依然有效。"""
    from .paths import _today_key

    return _state_is_done(_state_read(_state_path()), _today_key())


async def _run_prefetch_once() -> None:
    """跑一轮预热并落盘结果；除取消除外不外抛异常，避免定时循环中断。

    当日已成功预热则直接返回：这是「重启不重新预热」的关键——状态存在磁盘上，
    重启后读到的仍是「今天已完成」。
    """
    if _prefetch_done_today():
        logger.debug(f'{LOG_PREFIX} 今日图库预热已完成，跳过')
        return
    started = time.monotonic()
    try:
        stats = await _prefetch_once()
    except asyncio.CancelledError:
        raise
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        logger.warning(f'{LOG_PREFIX} 图库预热失败: {exc}')
        return
    if stats['skipped']:
        logger.debug(f'{LOG_PREFIX} 图库预热已按配置跳过')
        return
    from .paths import _today_key

    status = 'partial' if stats['truncated'] else 'done'
    payload: dict[str, str | int] = {
        'version': 1,
        'day': _today_key(),
        'status': status,
        'finished_at': _now_shanghai().isoformat(timespec='seconds'),
        'roles': stats['roles'],
        'downloaded': stats['downloaded'],
        'cached': stats['cached'],
        'failed': stats['failed'],
    }
    if not _state_write(_state_path(), payload):
        logger.warning(f'{LOG_PREFIX} 写入预热状态失败，下次重启会重跑本轮')
    summary = (
        f'耗时 {time.monotonic() - started:.1f} 秒: '
        f'角色 {stats["roles"]}，新下载 {stats["downloaded"]}，'
        f'已缓存 {stats["cached"]}，失败 {stats["failed"]}'
    )
    if stats['truncated']:
        logger.info(f'{LOG_PREFIX} 图库预热达到时间上限（{summary}），下次窗口继续')
    else:
        logger.info(f'{LOG_PREFIX} 图库预热完成，{summary}')


async def _prefetch_loop() -> None:
    """预热调度：只在零点前的窗口内运行，且同一天最多成功一次。

    窗口外重启不做任何预热 —— 已缓存索引在启动时由 `restore_cached_url_index` 从磁盘
    恢复，`prefer_cached_urls` 立刻可用；再下载整个图库既无收益，又会在白天与用户请求
    抢带宽。
    """
    await asyncio.sleep(PREFETCH_STARTUP_DELAY_SECONDS)
    # 启动时若正好落在窗口内才补跑；窗口外只等下一次窗口
    if seconds_until_prefetch() <= 1.0:
        await _run_prefetch_once()

    while True:
        delay = next_sleep_seconds()
        logger.debug(f'{LOG_PREFIX} 下次图库预热将在 {delay / 60:.1f} 分钟后开始')
        await asyncio.sleep(delay)
        await _run_prefetch_once()


async def restore_cached_url_index() -> int:
    """启动时用缓存目录重建「已缓存 URL」索引，返回恢复的条目数。

    该索引只驻留内存，重启后为空，`prefer_cached_urls` 随即退化为全量随机 —— 预热过的
    图片等于白热，这也是此前「重启必须再预热一轮」的真正原因。文件名即缓存键，一次目录
    遍历即可还原，无需读盘或重新下载。
    """
    from .paths import _gallery_image_cache_root
    from .file_cache import rebuild_cached_url_index

    try:
        count = await run_blocking(rebuild_cached_url_index, _gallery_image_cache_root())
    except OSError as exc:
        logger.warning(f'{LOG_PREFIX} 重建图库缓存索引失败: {exc}')
        return 0
    if count:
        logger.info(f'{LOG_PREFIX} 已恢复图库缓存索引 {count} 项')
    return count


def prefetch_task_running() -> bool:
    """是否已有预热循环在跑（跨插件热重载判断，见 `PREFETCH_TASK_NAME`）。"""
    return any(not task.done() and task.get_name() == PREFETCH_TASK_NAME for task in asyncio.all_tasks())
