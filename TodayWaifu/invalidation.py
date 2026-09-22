"""TodayWaifu 的缓存失效钩子。"""
from __future__ import annotations

import sys

from . import state
from .state import _SOURCE_CACHE, CANDIDATE_CACHE, _PGR_CANDIDATE_CACHE


def _invalidate_status_cache() -> None:
    # 延迟导入 + 存在性判断：status 反向依赖 shared，顶层导入会成环；未加载时跳过。
    if f'{__package__}.status' not in sys.modules:
        return
    from . import status as status_module

    status_module.invalidate_status_cache()


def _invalidate_candidate_cache() -> None:
    state._CANDIDATE_CACHE_GENERATION += 1
    CANDIDATE_CACHE.clear()
    _SOURCE_CACHE.invalidate()
    _PGR_CANDIDATE_CACHE.invalidate()
    _invalidate_status_cache()
    if f'{__package__}.normal_wife' not in sys.modules:
        return
    from . import normal_wife

    normal_wife.invalidate_normal_gallery_cache()
