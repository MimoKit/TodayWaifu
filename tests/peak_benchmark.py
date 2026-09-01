"""本地高峰期状态/缓存基准，不连接网络和生产数据库。"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('todaywaifu_source_cache', ROOT / 'twf' / 'source_cache.py')
if spec is None or spec.loader is None:
    raise RuntimeError('cannot load source cache module')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
AsyncSourceCache = module.AsyncSourceCache


async def benchmark_source_cache(concurrency: int = 100) -> dict[str, float | int]:
    cache = AsyncSourceCache[int](ttl_seconds=60, max_entries=4)
    calls = 0

    async def loader() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.001)
        return 1

    started = time.perf_counter()
    await asyncio.gather(*(cache.get('peak', loader) for _ in range(concurrency)))
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {'concurrency': concurrency, 'loader_calls': calls, 'elapsed_ms': elapsed_ms}


async def main() -> None:
    result = await benchmark_source_cache()
    print(result)


if __name__ == '__main__':
    asyncio.run(main())
