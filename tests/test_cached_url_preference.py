"""预热命中率：抽签必须优先挑磁盘上已有的图，否则预热基本白做。

原来抽签是 `rng.choice(role.images)`，而预热只暖每个角色的前几张图：
角色有 10 张图、只暖 2 张的话命中率只有 20%，剩下 80% 照样在零点走网络。
"""
import sys
import random
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
FILE_CACHE = PLUGIN / 'file_cache.py'


def _load_file_cache():
    spec = importlib.util.spec_from_file_location('todaywaifu_file_cache_prefer', FILE_CACHE)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load file_cache')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


file_cache = _load_file_cache()


class CachedUrlIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        file_cache.clear_cached_url_index()

    def test_write_then_query_reports_cached(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(file_cache.is_url_cached('https://x/a.png'))
            file_cache.write_url_cache(root, 'https://x/a.png', b'data')
            self.assertTrue(file_cache.is_url_cached('https://x/a.png'))

    def test_read_hit_also_remembers_the_url(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_cache.write_url_cache(root, 'https://x/b.png', b'data')
            file_cache.clear_cached_url_index()
            self.assertFalse(file_cache.is_url_cached('https://x/b.png'))

            self.assertIsNotNone(file_cache.read_url_cache(root, 'https://x/b.png'))
            self.assertTrue(file_cache.is_url_cached('https://x/b.png'), '命中读也要记进索引')

    def test_expiry_drops_the_url_from_the_index(self) -> None:
        import os
        import time
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_cache.write_url_cache(root, 'https://x/c.png', b'data')
            old = time.time() - 10_000
            for path in root.iterdir():
                os.utime(path, (old, old))

            file_cache.clear_expired_files(root, max_age_seconds=10)
            self.assertFalse(file_cache.is_url_cached('https://x/c.png'), '过期删除后索引必须同步')

    def test_prefer_cached_returns_only_cached_when_any_exists(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            urls = ('https://x/1.png', 'https://x/2.png', 'https://x/3.png')
            file_cache.write_url_cache(root, urls[1], b'data')

            preferred = file_cache.prefer_cached_urls(urls)
            self.assertEqual(preferred, (urls[1],))

    def test_prefer_cached_falls_back_to_all_when_nothing_cached(self) -> None:
        urls = ('https://x/1.png', 'https://x/2.png')
        self.assertEqual(file_cache.prefer_cached_urls(urls), urls, '一张都没缓存时必须退回全量')

    def test_index_overflow_degrades_gracefully(self) -> None:
        original = file_cache.CACHED_URL_INDEX_MAX
        try:
            file_cache.CACHED_URL_INDEX_MAX = 4
            for i in range(10):
                file_cache._remember_cached_url(f'https://x/{i}.png')
            # 溢出后整体丢弃，不报错、不无限增长
            self.assertLessEqual(file_cache.cached_url_count(), 4)
        finally:
            file_cache.CACHED_URL_INDEX_MAX = original
            file_cache.clear_cached_url_index()


class PickPreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (PLUGIN / 'roles.py').read_text(encoding='utf-8')

    def _pick_body(self) -> str:
        """_pick_role_record 的函数体，剥掉 docstring（注释里会提到旧写法）。"""
        import ast

        block = self.source[
            self.source.index('def _pick_role_record('):self.source.index('def _load_role_map(')
        ]
        function = ast.parse(block).body[0]
        return '\n'.join(
            ast.unparse(node)
            for node in function.body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        )

    def test_pick_prefers_cached_images(self) -> None:
        body = self._pick_body()
        self.assertIn('prefer_cached_urls(role.images)', body)
        self.assertNotIn('rng.choice(role.images)', body)

    def test_role_selection_stays_random(self) -> None:
        self.assertIn('rng.choice(candidates)', self._pick_body())

    def test_preference_is_behaviourally_identical_without_cache(self) -> None:
        """没有缓存时，挑选结果必须与原来逐次一致（同种子同结果）。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location('roles_under_test', PLUGIN / 'roles.py')
        self.assertIsNotNone(spec)
        # roles.py 依赖包内相对导入，这里只验证纯函数语义
        urls = ('https://x/1.png', 'https://x/2.png', 'https://x/3.png')
        file_cache.clear_cached_url_index()
        rng_a, rng_b = random.Random(42), random.Random(42)
        self.assertEqual(
            rng_a.choice(file_cache.prefer_cached_urls(urls)),
            rng_b.choice(urls),
        )


if __name__ == '__main__':
    unittest.main()
