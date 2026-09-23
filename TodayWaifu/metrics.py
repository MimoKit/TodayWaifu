"""插件侧可观测性：把高峰期真正关键的几个数字暴露出来。

框架只会在命令排队超过 5 秒时打一条 `queue_wait` 警告，看不到插件内部
「有多少下载在飞」「缓存里堆了多少群上下文」「图库是否已熔断」。
这里把这些指标定期打进日志，卡顿时可以直接判断瓶颈在哪一层：

- `inflight_image_downloads` 高 + `context_cache_entries` 正常
  → 瓶颈在图库网络，看 `gallery_circuit_open`
- `context_cache_entries` 接近「活跃群数 × 2」
  → 日期翻转回收没生效
- `inflight_candidate_loads` 长期不为 0
  → 候选列表加载卡住，所有抽签都在等它
"""
from __future__ import annotations

from gsuid_core.logger import logger

from .state import (
    _MEMBER_CACHE,
    _SOURCE_CACHE,
    _IMAGE_INFLIGHT,
    CANDIDATE_CACHE,
    _CONTEXT_REGISTRY,
    _CANDIDATE_INFLIGHT,
    _DAILY_CONTEXT_CACHE,
    _PGR_CANDIDATE_CACHE,
)
from .executor import blocking_executor_workers
from .constants import LOG_PREFIX


def collect_metrics() -> dict[str, int | bool]:
    """采集当前运行时指标（只读，不产生副作用）。"""
    from .gallery import gallery_circuit_state
    from .senders import image_delivery_backlog

    circuit_open, retry_after = gallery_circuit_state()
    return {
        'image_delivery_backlog': image_delivery_backlog(),
        'inflight_image_downloads': len(_IMAGE_INFLIGHT),
        'inflight_candidate_loads': len(_CANDIDATE_INFLIGHT),
        'candidate_cache_entries': len(CANDIDATE_CACHE),
        'context_cache_entries': len(_CONTEXT_REGISTRY.cache),
        'compat_context_cache_entries': len(_DAILY_CONTEXT_CACHE),
        'source_cache_entries': _SOURCE_CACHE.size,
        'pgr_cache_entries': _PGR_CANDIDATE_CACHE.size,
        'member_cache_entries': _MEMBER_CACHE.size,
        'blocking_executor_workers': blocking_executor_workers(),
        'gallery_circuit_open': circuit_open,
        'gallery_circuit_retry_after': int(retry_after),
    }


def log_metrics() -> dict[str, int | bool]:
    """把指标打进日志并返回，供维护循环与测试使用。"""
    metrics = collect_metrics()
    summary = ', '.join(f'{key}={value}' for key, value in metrics.items())
    logger.info(f'{LOG_PREFIX} 运行时指标: {summary}')
    return metrics
