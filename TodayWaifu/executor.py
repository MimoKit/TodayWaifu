"""TodayWaifu 专用阻塞 IO 线程池。

`asyncio.to_thread` 借用的是**整个进程共享**的默认 executor，CPython 默认线程数是
``min(32, cpu + 4)`` —— 4 核机器只有 8 个线程。而本插件高峰期同时在飞的下载与读盘
请求远超这个数（仅图片下载信号量就有 8 个槽），会把 Core 自身和其它插件的线程一起
饿死，表现为「整个 gscore 卡顿」。

因此所有阻塞 IO 一律走本模块自己的池子，插件再堵也只堵自己。

本模块只依赖标准库，可独立加载（测试用 importlib 直接加载）。
"""
from __future__ import annotations

import atexit
import asyncio
from typing import TypeVar
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

T = TypeVar('T')

# 刻意小于并发下载信号量（8）：让排队发生在信号量那一层，线程池永远不是瓶颈
MAX_BLOCKING_WORKERS = 4

_EXECUTOR: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(
            max_workers=MAX_BLOCKING_WORKERS,
            thread_name_prefix='twf-io',
        )
    return _EXECUTOR


async def run_blocking(func: Callable[..., T], *args: object) -> T:
    """在线程池里执行阻塞函数，不占用 Core 的默认 executor。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor(), func, *args)


def shutdown_blocking_executor() -> None:
    """关闭线程池（Core 退出或插件重载时调用），可重复调用。"""
    global _EXECUTOR
    executor = _EXECUTOR
    _EXECUTOR = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def blocking_executor_workers() -> int:
    """当前线程池的线程数（可观测性用）。"""
    return MAX_BLOCKING_WORKERS


atexit.register(shutdown_blocking_executor)
