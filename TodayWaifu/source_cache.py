from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar('T')
Loader = Callable[[], Awaitable[T]]


@dataclass(frozen=True)
class _ErrorEntry:
    expires_at: float
    error: BaseException


class AsyncSourceCache(Generic[T]):
    """为资源列表提供 TTL、失败短缓存、并发合并和有界 LRU 缓存。"""

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 32,
        error_ttl_seconds: float = 15.0,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.error_ttl_seconds = error_ttl_seconds
        self.max_entries = max_entries
        self._values: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._errors: OrderedDict[str, _ErrorEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[T]] = {}
        self._epochs: dict[str, int] = {}

    async def get(self, key: str, loader: Loader[T]) -> T:
        now = time.monotonic()
        cached = self._values.get(key)
        if cached is not None:
            expires_at, value = cached
            if expires_at > now:
                self._values.move_to_end(key)
                return value
            self._values.pop(key, None)

        error_entry = self._errors.get(key)
        if error_entry is not None:
            if error_entry.expires_at > now:
                self._errors.move_to_end(key)
                raise error_entry.error
            self._errors.pop(key, None)

        task = self._inflight.get(key)
        if task is None:
            epoch = self._epochs.get(key, 0)
            async def load() -> T:
                try:
                    value = await loader()
                except BaseException as exc:
                    if not isinstance(exc, asyncio.CancelledError) and epoch == self._epochs.get(key, 0):
                        self._errors[key] = _ErrorEntry(
                            time.monotonic() + self.error_ttl_seconds,
                            exc,
                        )
                        self._errors.move_to_end(key)
                        while len(self._errors) > self.max_entries:
                            self._errors.popitem(last=False)
                    raise
                if epoch != self._epochs.get(key, 0):
                    return value
                self._errors.pop(key, None)
                self._values[key] = (time.monotonic() + self.ttl_seconds, value)
                self._values.move_to_end(key)
                while len(self._values) > self.max_entries:
                    self._values.popitem(last=False)
                return value

            task = asyncio.create_task(load())
            self._inflight[key] = task

        try:
            return await task
        finally:
            if task.done() and self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    def invalidate(self, key: str | None = None) -> None:
        """清除指定源或全部缓存；正在进行的加载不被取消。"""
        if key is None:
            self._values.clear()
            self._errors.clear()
            for inflight_key in tuple(self._inflight):
                self._epochs[inflight_key] = self._epochs.get(inflight_key, 0) + 1
            return
        self._values.pop(key, None)
        self._errors.pop(key, None)
        self._epochs[key] = self._epochs.get(key, 0) + 1

    def prune(self) -> None:
        now = time.monotonic()
        for key, (expires_at, _) in tuple(self._values.items()):
            if expires_at <= now:
                self._values.pop(key, None)
        for key, error in tuple(self._errors.items()):
            if error.expires_at <= now:
                self._errors.pop(key, None)

    @property
    def size(self) -> int:
        return len(self._values)
