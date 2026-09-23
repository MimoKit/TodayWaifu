"""按主机熔断的轻量断路器。

图库接口挂掉时，每个用户的抽签仍会老实打满整条重试链，把已经吃紧的线程池
和上游一起拖垮（重试风暴）。熔断器在连续失败达到阈值后直接快速失败一段时间，
既给上游恢复的机会，也让插件立刻降级到本地图库而不是继续干等。

本模块只依赖标准库，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from collections.abc import Callable


@dataclass
class _BreakerState:
    consecutive_failures: int = 0
    open_until: float = 0.0
    tripped_count: int = 0


class CircuitBreaker:
    """按 key（通常是主机名）熔断；冷却期内 `allow()` 返回 False。"""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.clock = clock
        self._states: dict[str, _BreakerState] = {}

    def _state(self, key: str) -> _BreakerState:
        state = self._states.get(key)
        if state is None:
            state = _BreakerState()
            self._states[key] = state
        return state

    def allow(self, key: str) -> bool:
        """是否放行本次请求。冷却结束后放行**一个**探测请求（半开）。"""
        state = self._states.get(key)
        if state is None or state.open_until <= 0.0:
            return True
        if self.clock() >= state.open_until:
            state.open_until = 0.0
            return True
        return False

    def is_open(self, key: str) -> bool:
        """纯查询，不产生半开副作用。"""
        state = self._states.get(key)
        return state is not None and state.open_until > self.clock()

    def retry_after(self, key: str) -> float:
        """距离冷却结束还有多少秒；未熔断时为 0。"""
        state = self._states.get(key)
        if state is None or state.open_until <= 0.0:
            return 0.0
        return max(0.0, state.open_until - self.clock())

    def record_success(self, key: str) -> None:
        state = self._state(key)
        state.consecutive_failures = 0
        state.open_until = 0.0

    def record_failure(self, key: str) -> None:
        state = self._state(key)
        state.consecutive_failures += 1
        if state.consecutive_failures >= self.failure_threshold:
            state.open_until = self.clock() + self.cooldown_seconds
            state.tripped_count += 1

    def tripped_count(self, key: str) -> int:
        state = self._states.get(key)
        return state.tripped_count if state is not None else 0

    def reset(self) -> None:
        self._states.clear()
