"""本地图片候选必须带缓存：图库挂掉时每次发送失败都会回退到这里做全量扫盘。"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
ROLES = PLUGIN / 'roles.py'


class LocalCandidateCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = ROLES.read_text(encoding='utf-8')

    def _body(self, start_marker: str, end_marker: str) -> str:
        """取出函数体并剥掉 docstring（说明文字里会提到 rglob 等旧细节）。"""
        block = self.source[self.source.index(start_marker):self.source.index(end_marker)]
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

    def test_local_candidates_are_cached_with_ttl(self) -> None:
        body = self._body('def _load_local_candidates(', 'def _scan_local_candidates(')
        self.assertIn("cache_key = f'local:{role_mode}'", body)
        self.assertIn('CANDIDATE_CACHE.get(cache_key)', body)
        self.assertIn('CACHE_TTL_SECONDS', body)
        self.assertIn('CANDIDATE_CACHE[cache_key] = (now, result)', body)

    def test_the_scan_itself_is_separate_so_the_cache_can_wrap_it(self) -> None:
        self.assertIn('def _scan_local_candidates(', self.source)
        body = self._body('def _load_local_candidates(', 'def _scan_local_candidates(')
        # 缓存函数自己不能做目录扫描
        self.assertNotIn('rglob', body)
        self.assertNotIn('_collect_role_candidates(', body)

    def test_failures_are_not_cached(self) -> None:
        """「目录暂时不可用」这类失败不能被缓存住，否则恢复后一直返回失败。"""
        body = self._body('def _load_local_candidates(', 'def _scan_local_candidates(')
        self.assertIn('if result[0]:', body, '只缓存成功结果')

    def test_upload_invalidates_the_cache(self) -> None:
        """上传/删除图片必须能让本地候选缓存失效，否则新图要等 TTL 才出现。"""
        invalidation = (PLUGIN / 'invalidation.py').read_text(encoding='utf-8')
        self.assertIn('CANDIDATE_CACHE.clear()', invalidation)

    def test_scan_keeps_its_original_behaviour(self) -> None:
        body = self._body('def _scan_local_candidates(', 'def _load_nte_local_candidates(')
        for marker in ('_resolve_role_map_path', '_resolve_role_pile_root', '_collect_role_candidates'):
            self.assertIn(marker, body, marker)
        # 返回结构不能变（用原始源码判断，ast.unparse 会给元组补括号）
        raw = self.source[
            self.source.index('def _scan_local_candidates('):self.source.index('def _load_nte_local_candidates(')
        ]
        self.assertIn('return candidates, None', raw)


if __name__ == '__main__':
    unittest.main()
