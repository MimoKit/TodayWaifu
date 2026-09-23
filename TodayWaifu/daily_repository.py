"""TodayWaifu 的高峰期状态仓储协调层。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .payloads import DailyContext


@dataclass(frozen=True)
class ContextKey:
    day: str
    bot_id: str
    group_id: str

    @property
    def cache_key(self) -> str:
        return f'{self.day}:{self.bot_id}:{self.group_id}'


@dataclass(frozen=True)
class ContextSnapshot:
    generation: int
    value: DailyContext


class ContextRegistry:
    """管理按上下文分片的锁、缓存和进行中的 hydrate 任务。"""

    def __init__(self) -> None:
        self.locks: dict[ContextKey, asyncio.Lock] = {}
        self.cache: dict[ContextKey, ContextSnapshot] = {}
        self.inflight: dict[ContextKey, asyncio.Task[DailyContext]] = {}
        self._generations: dict[ContextKey, int] = {}

    def lock_for(self, key: ContextKey) -> asyncio.Lock:
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock

    def generation(self, key: ContextKey) -> int:
        return self._generations.get(key, 0)

    def put(self, key: ContextKey, value: DailyContext, generation: int | None = None) -> bool:
        """发布成功提交的快照；旧 generation 不能覆盖较新的快照。"""
        current = self.generation(key)
        if generation is not None and generation < current:
            return False
        next_generation = max(current, generation or 0) + 1
        self._generations[key] = next_generation
        self.cache[key] = ContextSnapshot(next_generation, value)
        return True

    def get(self, key: ContextKey) -> DailyContext | None:
        snapshot = self.cache.get(key)
        return snapshot.value if snapshot is not None else None

    def snapshot(self, key: ContextKey) -> ContextSnapshot | None:
        return self.cache.get(key)

    def invalidate(self, key: ContextKey) -> None:
        self._generations[key] = self.generation(key) + 1
        self.cache.pop(key, None)

    def drop_stale_days(self, current_day: str) -> int:
        """日期翻转时立即丢弃非当天的上下文快照，返回丢弃数量。

        快照按 `(day, bot, group)` 缓存整群记录。若只靠维护循环回收（默认间隔 1 小时），
        零点到凌晨 1 点之间内存里会同时躺着**两天 × 全部活跃群**的上下文。

        **只动快照，不动锁**：翻转瞬间可能仍有协程持有前一天 key 的锁，
        此时回收锁会让两个协程拿到不同的锁对象、同时进入临界区。
        锁留给维护循环在一小时后回收，那时已经没有协程还在用前一天的 key。
        """
        dropped = 0
        for key in tuple(self.cache):
            if key.day != current_day:
                self.cache.pop(key, None)
                # 递增而不是删除 generation：让滞后的 hydrate 无法把上一天的快照
                # 重新发布回来（generation 比较会拒绝它）
                self._generations[key] = self.generation(key) + 1
                dropped += 1
        for key, task in tuple(self.inflight.items()):
            if task.done():
                self.inflight.pop(key, None)
        return dropped

    def prune(self, current_day: str) -> None:
        for key in tuple(self.cache):
            if key.day != current_day:
                self.cache.pop(key, None)
                self._generations.pop(key, None)
        for key, task in tuple(self.inflight.items()):
            if task.done():
                self.inflight.pop(key, None)
        for key, lock in tuple(self.locks.items()):
            if lock.locked() or key in self.cache or key in self.inflight:
                continue
            self.locks.pop(key, None)
            self._generations.pop(key, None)
