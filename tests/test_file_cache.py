"""twf/file_cache.py 的单元测试：不依赖 gsuid_core，importlib 独立加载。"""
import importlib.util
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "twf" / "file_cache.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("todaywaifu_file_cache", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load file_cache module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FileBytesCacheTests(unittest.TestCase):
    def test_hit_returns_same_bytes_without_reread(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "a.png"
            path.write_bytes(b"first")
            self.assertEqual(cache.read_file_bytes_cached(path), b"first")
            # 缓存命中时不应再次 read_bytes（stat 校验 mtime 是廉价系统调用，允许）
            original_read = Path.read_bytes

            def fail_read(self, *args, **kwargs):
                if self == path:
                    raise AssertionError("命中缓存时不应再次 read_bytes")
                return original_read(self, *args, **kwargs)

            try:
                Path.read_bytes = fail_read
                self.assertEqual(cache.read_file_bytes_cached(path), b"first")
            finally:
                Path.read_bytes = original_read

    def test_mtime_change_invalidates_cache(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "b.png"
            path.write_bytes(b"old")
            self.assertEqual(cache.read_file_bytes_cached(path), b"old")
            time.sleep(0.02)
            path.write_bytes(b"new-content-longer")
            self.assertEqual(cache.read_file_bytes_cached(path), b"new-content-longer")

    def test_lru_eviction_respects_max_entries(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            for index in range(cache.LOCAL_BYTES_CACHE_MAX_ENTRIES + 8):
                p = Path(directory) / f"{index}.png"
                p.write_bytes(b"x")
                cache.read_file_bytes_cached(p)
            self.assertLessEqual(
                len(cache._LOCAL_BYTES_CACHE), cache.LOCAL_BYTES_CACHE_MAX_ENTRIES
            )


class UrlCacheTests(unittest.TestCase):
    def test_write_then_read_round_trip(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            url = "https://example.com/a.png"
            self.assertIsNone(cache.read_url_cache(root, url))
            self.assertTrue(cache.write_url_cache(root, url, b"image-bytes"))
            self.assertEqual(cache.read_url_cache(root, url), b"image-bytes")

    def test_content_addressed_same_url_same_path(self) -> None:
        cache = _load_module()
        p1 = cache.url_hash_cache_path(Path("/tmp/x"), "https://a/1.png")
        p2 = cache.url_hash_cache_path(Path("/tmp/x"), "https://a/1.png")
        p3 = cache.url_hash_cache_path(Path("/tmp/x"), "https://a/2.png")
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)

    def test_expired_files_are_removed_but_temp_files_are_kept(self) -> None:
        cache = _load_module()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            expired = root / 'expired'
            fresh = root / 'fresh'
            temporary = root / '.expired.tmp'
            expired.write_bytes(b'old')
            fresh.write_bytes(b'new')
            temporary.write_bytes(b'tmp')
            old_time = time.time() - 100
            os.utime(expired, (old_time, old_time))
            os.utime(temporary, (old_time, old_time))
            removed = cache.clear_expired_files(root, 50)
            self.assertEqual(removed, 1)
            self.assertFalse(expired.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(temporary.exists())

        cache = _load_module()
        with TemporaryDirectory() as directory:
            self.assertFalse(cache.write_url_cache(Path(directory), "https://a", b""))


if __name__ == "__main__":
    unittest.main()
