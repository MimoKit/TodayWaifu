"""熔断器与重试策略：图库挂掉时不能再让每个用户都打满重试链。"""
import ast
import sys
import unittest
import importlib.util
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PLUGIN / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {filename}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


breaker_module = _load('todaywaifu_circuit_breaker', 'circuit_breaker.py')
CircuitBreaker = breaker_module.CircuitBreaker


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CircuitBreakerTests(unittest.TestCase):
    def _breaker(self, threshold: int = 3, cooldown: float = 30.0) -> tuple[Any, _FakeClock]:
        clock = _FakeClock()
        return CircuitBreaker(failure_threshold=threshold, cooldown_seconds=cooldown, clock=clock), clock

    def test_stays_closed_below_threshold(self) -> None:
        breaker, _ = self._breaker(threshold=3)
        for _ in range(2):
            breaker.record_failure('gallery.test')
        self.assertTrue(breaker.allow('gallery.test'))
        self.assertFalse(breaker.is_open('gallery.test'))

    def test_trips_at_threshold_and_blocks_until_cooldown(self) -> None:
        breaker, clock = self._breaker(threshold=3, cooldown=30.0)
        for _ in range(3):
            breaker.record_failure('gallery.test')

        self.assertTrue(breaker.is_open('gallery.test'))
        self.assertFalse(breaker.allow('gallery.test'))
        self.assertAlmostEqual(breaker.retry_after('gallery.test'), 30.0, places=3)

        clock.advance(29.0)
        self.assertFalse(breaker.allow('gallery.test'))

        clock.advance(1.5)
        self.assertTrue(breaker.allow('gallery.test'), '冷却结束后必须放行一个探测请求')

    def test_success_closes_the_circuit(self) -> None:
        breaker, _ = self._breaker(threshold=3)
        for _ in range(3):
            breaker.record_failure('gallery.test')
        breaker.record_success('gallery.test')
        self.assertFalse(breaker.is_open('gallery.test'))
        self.assertTrue(breaker.allow('gallery.test'))

    def test_failed_probe_re_trips_immediately(self) -> None:
        """半开探测失败必须立刻重新熔断，而不是再等满一个阈值。"""
        breaker, clock = self._breaker(threshold=3, cooldown=30.0)
        for _ in range(3):
            breaker.record_failure('gallery.test')
        clock.advance(31.0)
        self.assertTrue(breaker.allow('gallery.test'))

        breaker.record_failure('gallery.test')
        self.assertTrue(breaker.is_open('gallery.test'), '探测失败应立即重新熔断')

    def test_breakers_are_isolated_per_host(self) -> None:
        breaker, _ = self._breaker(threshold=2)
        breaker.record_failure('a.test')
        breaker.record_failure('a.test')
        self.assertTrue(breaker.is_open('a.test'))
        self.assertFalse(breaker.is_open('b.test'))
        self.assertTrue(breaker.allow('b.test'), '一个主机熔断不能影响其它主机')

    def test_unknown_key_is_allowed_and_has_no_side_effects(self) -> None:
        breaker, _ = self._breaker()
        self.assertTrue(breaker.allow('never-seen.test'))
        self.assertTrue(breaker.allow('never-seen.test'))
        self.assertEqual(breaker.retry_after('never-seen.test'), 0.0)

    def test_is_open_is_a_pure_query(self) -> None:
        breaker, clock = self._breaker(threshold=1, cooldown=10.0)
        breaker.record_failure('a.test')
        clock.advance(11.0)
        # 纯查询不应消费掉半开机会
        self.assertFalse(breaker.is_open('a.test'))
        self.assertTrue(breaker.allow('a.test'))

    def test_reset_clears_every_host(self) -> None:
        breaker, _ = self._breaker(threshold=1)
        breaker.record_failure('a.test')
        breaker.reset()
        self.assertFalse(breaker.is_open('a.test'))


def _gallery_tree() -> ast.Module:
    return ast.parse((PLUGIN / 'gallery.py').read_text(encoding='utf-8'))


class RetryPolicyTests(unittest.TestCase):
    def test_retry_count_is_reduced_to_one(self) -> None:
        constants = (PLUGIN / 'constants.py').read_text(encoding='utf-8')
        tree = ast.parse(constants)
        values = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
        }
        self.assertEqual(values['HTTP_RETRIES'], 1, '重试次数必须降到 1，否则一次失败被放大 4 倍')
        self.assertLessEqual(values['GALLERY_HTTP_TIMEOUT_SECONDS'], 10)
        self.assertLessEqual(values['IMAGE_HTTP_TIMEOUT_SECONDS'], 10)
        self.assertLess(values['RETRY_BASE_DELAY_SECONDS'], 5.0, '固定 5 秒间隔是重试风暴的来源之一')
        self.assertGreater(values['RETRY_JITTER_SECONDS'], 0.0, '必须有抖动，否则失败请求会同时重试')

    def test_worst_case_slot_occupancy_is_bounded(self) -> None:
        """最坏等待 = (retries+1) * timeout + 退避总和，必须远小于原来的 95 秒。"""
        constants = (PLUGIN / 'constants.py').read_text(encoding='utf-8')
        tree = ast.parse(constants)
        values = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
        }
        retries = values['HTTP_RETRIES']
        worst = (retries + 1) * values['IMAGE_HTTP_TIMEOUT_SECONDS']
        worst += retries * (values['RETRY_MAX_DELAY_SECONDS'] + values['RETRY_JITTER_SECONDS'])
        self.assertLess(worst, 30.0, f'最坏占用 {worst}s 仍然过长')

    def test_gallery_http_helper_consults_the_breaker(self) -> None:
        source = (PLUGIN / 'gallery.py').read_text(encoding='utf-8')
        helper = source[
            source.index('def _http_get_with_retry('):source.index('def _fetch_gallery_payload_sync(')
        ]
        self.assertIn('_HTTP_BREAKER.allow(key)', helper)
        self.assertIn('_HTTP_BREAKER.record_failure(key)', helper)
        self.assertIn('_HTTP_BREAKER.record_success(key)', helper)
        # 熔断快速失败必须是 RuntimeError，才能走既有的「回退本地图库」分支
        self.assertIn('raise RuntimeError(', helper)

    def test_retry_delay_uses_exponential_backoff_with_jitter(self) -> None:
        source = (PLUGIN / 'gallery.py').read_text(encoding='utf-8')
        fn = source[source.index('def _retry_delay('):source.index('def _http_get_with_retry(')]
        self.assertIn('2 ** attempt', fn)
        self.assertIn('random.uniform', fn)
        self.assertIn('min(base, RETRY_MAX_DELAY_SECONDS)', fn)

    def test_auth_errors_bypass_the_breaker(self) -> None:
        """401/403 是配置问题不是上游故障，不该把图库熔断掉。"""
        source = (PLUGIN / 'gallery.py').read_text(encoding='utf-8')
        helper = source[
            source.index('def _http_get_with_retry('):source.index('def _fetch_gallery_payload_sync(')
        ]
        auth_branch = helper[helper.index('if exc.code in {401, 403}:'):]
        self.assertIn('raise', auth_branch[:120])


if __name__ == '__main__':
    unittest.main()
