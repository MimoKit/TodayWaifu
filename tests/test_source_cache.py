import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_cache_class():
    path = ROOT / 'twf' / 'source_cache.py'
    spec = importlib.util.spec_from_file_location('todaywaifu_source_cache', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load source cache module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.AsyncSourceCache


AsyncSourceCache = _load_cache_class()


class SourceCacheTests(unittest.TestCase):
    def test_concurrent_loads_share_one_task(self) -> None:
        async def run() -> None:
            cache = AsyncSourceCache[int](ttl_seconds=60, max_entries=2)
            calls = 0

            async def load() -> int:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.01)
                return 42

            values = await asyncio.gather(*(cache.get('roles', load) for _ in range(8)))
            self.assertEqual(values, [42] * 8)
            self.assertEqual(calls, 1)
            self.assertEqual(await cache.get('roles', load), 42)
            self.assertEqual(calls, 1)

        asyncio.run(run())

    def test_failed_load_is_temporarily_cached(self) -> None:
        async def run() -> None:
            cache = AsyncSourceCache[int](ttl_seconds=60, error_ttl_seconds=60)
            calls = 0

            async def load() -> int:
                nonlocal calls
                calls += 1
                raise RuntimeError('source unavailable')

            with self.assertRaisesRegex(RuntimeError, 'source unavailable'):
                await cache.get('roles', load)
            with self.assertRaisesRegex(RuntimeError, 'source unavailable'):
                await cache.get('roles', load)
            self.assertEqual(calls, 1)

        asyncio.run(run())

    def test_lru_is_bounded_and_invalidation_clears_entries(self) -> None:
        async def run() -> None:
            cache = AsyncSourceCache[int](ttl_seconds=60, max_entries=2)

            async def load(value: int) -> int:
                return value

            await cache.get('a', lambda: load(1))
            await cache.get('b', lambda: load(2))
            await cache.get('c', lambda: load(3))
            self.assertEqual(cache.size, 2)
            cache.invalidate('b')
            self.assertEqual(cache.size, 1)
            cache.invalidate()
            self.assertEqual(cache.size, 0)

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
