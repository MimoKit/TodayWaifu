"""缓存命中契约：抽签候选集必须收敛到磁盘上已有的图片。

提交 40af9d1 之前，抽签直接取 `rng.choice(role.images)`，抽到未缓存的图就必须
在发送时走网络下载。优先从已缓存图片里挑，能直接命中的就不必再下载。

本文件守护两层契约：内存 URL 索引在写入、命中读、过期删除与容量溢出四条路径上都必须
与磁盘状态一致；挑选函数在无缓存时必须退回全量，保持与既有实现同种子逐次一致。
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
    # 独立模块名避免与其它测试文件共用 sys.modules 条目，否则模块级 URL 索引会被复用
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
            # 索引初始状态必须为未命中，否则后续断言无法区分「真的记了」与「默认即为真」
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

            # 重启后进程内索引为空，若读命中不回填，已缓存的图片在索引里等同于不存在，
            # 首次抽签的偏好判定会整体退化
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

            # 过期删除与容量裁剪是两条独立路径，两者都必须回写索引，否则「索引只增不减」
            # 会让抽签持续选中已被删除的 URL
            file_cache.clear_expired_files(root, max_age_seconds=10)
            self.assertFalse(file_cache.is_url_cached('https://x/c.png'), '过期删除后索引必须同步')

    def test_prefer_cached_returns_only_cached_when_any_exists(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            urls = ('https://x/1.png', 'https://x/2.png', 'https://x/3.png')
            file_cache.write_url_cache(root, urls[1], b'data')

            # 候选集收缩是有意为之：未缓存的 URL 一旦入选就必然在零点回源下载
            preferred = file_cache.prefer_cached_urls(urls)
            self.assertEqual(preferred, (urls[1],))

    def test_prefer_cached_falls_back_to_all_when_nothing_cached(self) -> None:
        urls = ('https://x/1.png', 'https://x/2.png')
        # 无缓存时必须退回全量而非返回空：候选集为空会让随机选取直接抛异常
        self.assertEqual(file_cache.prefer_cached_urls(urls), urls, '一张都没缓存时必须退回全量')

    def test_index_overflow_degrades_gracefully(self) -> None:
        original = file_cache.CACHED_URL_INDEX_MAX
        try:
            file_cache.CACHED_URL_INDEX_MAX = 4
            for i in range(10):
                file_cache._remember_cached_url(f'https://x/{i}.png')
            # 溢出后整体丢弃，不报错、不无限增长；索引只是提示信息，丢失仅降低命中率，
            # 不影响挑选结果的正确性，故选择降级而非拒绝写入
            self.assertLessEqual(file_cache.cached_url_count(), 4)
        finally:
            # 上限是模块级常量，测试改写后必须还原，否则泄漏到同进程的其它用例
            file_cache.CACHED_URL_INDEX_MAX = original
            file_cache.clear_cached_url_index()


class PickPreferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = (PLUGIN / 'roles.py').read_text(encoding='utf-8')

    def _pick_body(self) -> str:
        """_pick_role_record 的函数体，剥掉 docstring（注释里会提到旧写法）。

        以源码文本做断言无法容忍格式变化，故先经 AST 还原再匹配；docstring 中会引用被
        禁用的旧表达式，若不一并剥离，针对旧写法的负向断言会因文档本身而恒为真。
        """
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
        # 两条断言互为正反：正向锁定偏好入口，负向防止回退到未过缓存的旧写法
        self.assertIn('prefer_cached_urls(role.images)', body)
        self.assertNotIn('rng.choice(role.images)', body)

    def test_role_selection_stays_random(self) -> None:
        # 偏好只允许收缩候选集，不得把选择改成确定性策略：同一角色在相近时间内被反复
        # 分配到会让功能失去抽签语义
        self.assertIn('rng.choice(candidates)', self._pick_body())

    def test_preference_is_behaviourally_identical_without_cache(self) -> None:
        """没有缓存时，挑选结果必须与原来逐次一致（同种子同结果）。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location('roles_under_test', PLUGIN / 'roles.py')
        self.assertIsNotNone(spec)
        # roles.py 依赖包内相对导入，这里只验证纯函数语义
        urls = ('https://x/1.png', 'https://x/2.png', 'https://x/3.png')
        file_cache.clear_cached_url_index()
        # 同种子双随机源对照：无缓存时新旧两条取值路径必须给出同一结果，保证本次改动
        # 不改变既有抽取序列
        rng_a, rng_b = random.Random(42), random.Random(42)
        self.assertEqual(
            rng_a.choice(file_cache.prefer_cached_urls(urls)),
            rng_b.choice(urls),
        )


if __name__ == '__main__':
    unittest.main()
