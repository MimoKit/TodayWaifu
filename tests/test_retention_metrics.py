"""旧数据清理与运行时指标：表不能无限增长，卡顿要能看到瓶颈在哪一层。"""
import ast
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'


def _constants() -> dict[str, Any]:
    tree = ast.parse((PLUGIN / 'constants.py').read_text(encoding='utf-8'))
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            code = compile(ast.Expression(node.value), '<constants>', 'eval')
            values[node.targets[0].id] = eval(code, {'__builtins__': {}}, {})
        except (NameError, TypeError, ValueError, SyntaxError):
            continue
    return values


class RetentionTests(unittest.TestCase):
    def test_retention_default_is_bounded(self) -> None:
        retention = _constants()['DAILY_RECORD_RETENTION_DAYS']
        self.assertGreater(retention, 0, '默认必须清理，否则表随天数无限增长')
        self.assertLessEqual(retention, 365)

    def test_retention_is_user_configurable_including_disabled(self) -> None:
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        self.assertIn("'DailyWifeRecordRetentionDays'", config)
        self.assertIn('设为 0 表示永久保留', config)

    def test_cleanup_skips_when_retention_is_disabled(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        fn = shared[
            shared.index('async def _prune_old_daily_records('):shared.index('def _record_retention_days(')
        ]
        self.assertIn('if retention_days <= 0:', fn)
        self.assertIn('return', fn)

    def test_cleanup_uses_an_iso_cutoff_day(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        fn = shared[
            shared.index('async def _prune_old_daily_records('):shared.index('def _record_retention_days(')
        ]
        self.assertIn('timedelta(days=retention_days)', fn)
        self.assertIn('.isoformat()', fn)
        self.assertIn('DailyWifeRecord.delete_before(cutoff)', fn)

    def test_cleanup_runs_inside_the_maintenance_loop(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        loop = shared[
            shared.index('async def _cache_maintenance_once('):shared.index('async def _cache_maintenance_loop(')
        ]
        self.assertIn('await _prune_old_daily_records()', loop)

    def test_delete_before_compares_iso_day_strings(self) -> None:
        source = (PLUGIN / 'models.py').read_text(encoding='utf-8')
        fn = source[source.index('async def delete_before('):source.index('async def get_record(')]
        self.assertIn('delete(cls)', fn)
        self.assertIn('cls.day < cutoff_day', fn)

    def test_retention_falls_back_to_the_constant(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        fn = shared[shared.index('def _record_retention_days('):shared.index('async def _cache_maintenance_once(')]
        self.assertIn("int(_cfg('DailyWifeRecordRetentionDays'))", fn)
        self.assertIn('return DAILY_RECORD_RETENTION_DAYS', fn)


class MetricsTests(unittest.TestCase):
    EXPECTED = {
        'inflight_image_downloads',
        'inflight_candidate_loads',
        'candidate_cache_entries',
        'context_cache_entries',
        'compat_context_cache_entries',
        'source_cache_entries',
        'pgr_cache_entries',
        'member_cache_entries',
        'blocking_executor_workers',
        'gallery_circuit_open',
        'gallery_circuit_retry_after',
    }

    def test_metrics_module_covers_the_peak_bottlenecks(self) -> None:
        source = (PLUGIN / 'metrics.py').read_text(encoding='utf-8')
        for key in self.EXPECTED:
            self.assertIn(f"'{key}'", source, key)

    def test_metrics_are_read_only(self) -> None:
        source = (PLUGIN / 'metrics.py').read_text(encoding='utf-8')
        collect = source[source.index('def collect_metrics('):source.index('def log_metrics(')]
        # 采集不能改状态、不能触发网络或数据库
        for forbidden in ('await ', '.clear()', '.pop(', 'logger.'):
            self.assertNotIn(forbidden, collect, forbidden)

    def test_metrics_are_logged_by_the_maintenance_loop(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        loop = shared[
            shared.index('async def _cache_maintenance_once('):shared.index('async def _cache_maintenance_loop(')
        ]
        self.assertIn('log_metrics()', loop)

    def test_gallery_exposes_circuit_state(self) -> None:
        source = (PLUGIN / 'gallery.py').read_text(encoding='utf-8')
        fn = source[source.index('def gallery_circuit_state('):source.index('def _retry_delay(')]
        self.assertIn('_HTTP_BREAKER.is_open(key)', fn)
        self.assertIn('_HTTP_BREAKER.retry_after(key)', fn)

    def test_metrics_docstring_explains_how_to_read_them(self) -> None:
        source = (PLUGIN / 'metrics.py').read_text(encoding='utf-8')
        # 指标没有解读说明就等于没有可观测性
        self.assertIn('瓶颈在图库网络', source)
        self.assertIn('日期翻转回收没生效', source)


if __name__ == '__main__':
    unittest.main()
