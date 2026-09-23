"""图库磁盘缓存必须有总容量上限：只按天过期的话，URL 会变时会把磁盘吃满。"""
import os
import sys
import time
import tempfile
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
FILE_CACHE = PLUGIN / 'file_cache.py'


def _load_file_cache():
    spec = importlib.util.spec_from_file_location('todaywaifu_file_cache_budget', FILE_CACHE)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load file_cache')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


file_cache = _load_file_cache()


def _write(root: Path, url: str, size: int, age_seconds: float = 0.0) -> Path:
    path = file_cache.url_hash_cache_path(root, url)
    path.write_bytes(b'x' * size)
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
    return path


class CacheBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        file_cache.clear_cached_url_index()

    def test_no_eviction_when_under_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/a.png', 100)
            self.assertEqual(file_cache.enforce_cache_size_budget(root, max_bytes=10_000), 0)

    def test_evicts_oldest_first_until_under_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/old.png', 400, age_seconds=300)
            _write(root, 'https://x/mid.png', 400, age_seconds=200)
            _write(root, 'https://x/new.png', 400, age_seconds=100)

            removed = file_cache.enforce_cache_size_budget(root, max_bytes=800)

            self.assertEqual(removed, 1)
            self.assertFalse(file_cache.url_hash_cache_path(root, 'https://x/old.png').exists())
            self.assertTrue(file_cache.url_hash_cache_path(root, 'https://x/new.png').exists())
            self.assertLessEqual(file_cache.cache_dir_bytes(root), 800)

    def test_evicts_enough_files_for_a_large_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(10):
                _write(root, f'https://x/{i}.png', 100, age_seconds=100 - i)
            removed = file_cache.enforce_cache_size_budget(root, max_bytes=250)
            self.assertGreaterEqual(removed, 8)
            self.assertLessEqual(file_cache.cache_dir_bytes(root), 250)

    def test_eviction_keeps_the_cached_url_index_in_sync(self) -> None:
        """被淘汰的图必须从索引里移除，否则抽签会一直挑到已经不存在的图。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/old.png', 400, age_seconds=300)
            file_cache.read_url_cache(root, 'https://x/old.png')
            self.assertTrue(file_cache.is_url_cached('https://x/old.png'))

            file_cache.enforce_cache_size_budget(root, max_bytes=10)
            self.assertFalse(file_cache.is_url_cached('https://x/old.png'))

    def test_zero_budget_means_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/a.png', 400)
            self.assertEqual(file_cache.enforce_cache_size_budget(root, max_bytes=0), 0)
            self.assertTrue(file_cache.url_hash_cache_path(root, 'https://x/a.png').exists())

    def test_skips_temp_and_hidden_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/a.png', 100)
            (root / '.a.png.123.tmp').write_bytes(b'y' * 5000)
            (root / '.hidden').write_bytes(b'z' * 5000)

            self.assertEqual(file_cache.enforce_cache_size_budget(root, max_bytes=1000), 0)
            self.assertTrue((root / '.a.png.123.tmp').exists())

    def test_missing_directory_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                file_cache.enforce_cache_size_budget(Path(tmp) / 'nope', max_bytes=100), 0
            )

    def test_cache_dir_bytes_reports_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, 'https://x/a.png', 100)
            _write(root, 'https://x/b.png', 250)
            self.assertEqual(file_cache.cache_dir_bytes(root), 350)


class WiringTests(unittest.TestCase):
    def test_maintenance_enforces_the_budget(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        loop = shared[
            shared.index('async def _cache_maintenance_once('):shared.index('async def _cache_maintenance_loop(')
        ]
        self.assertIn('enforce_cache_size_budget', loop)
        self.assertIn('_gallery_cache_max_bytes()', loop)

    def test_budget_is_configurable_including_unlimited(self) -> None:
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        self.assertIn("'DailyWifeGalleryCacheMaxMB'", config)
        self.assertIn('设为 0 表示不限制', config)

        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        fn = shared[shared.index('def _gallery_cache_max_bytes('):shared.index('def _record_retention_days(')]
        self.assertIn('max(0, max_mb)', fn, '负数或 0 必须落到「不限制」而不是误删')
        self.assertIn('GALLERY_CACHE_MAX_MB', fn, '配置缺失时要回退到常量')


if __name__ == '__main__':
    unittest.main()
