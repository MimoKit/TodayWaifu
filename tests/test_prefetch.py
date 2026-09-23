"""零点前预热的调度与边界：必须严格有界，且只在图库模式下运行。"""
import ast
import unittest
from typing import Any
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
PREFETCH = PLUGIN / 'prefetch.py'


def _extract_scheduler() -> Any:
    """单独抽出纯函数 seconds_until_prefetch 来跑（prefetch.py 依赖 gsuid_core）。"""
    tree = ast.parse(PREFETCH.read_text(encoding='utf-8'))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == 'seconds_until_prefetch'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, node], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {
        'datetime': datetime,
        'timedelta': __import__('datetime').timedelta,
        'PREFETCH_HOUR': 23,
        'PREFETCH_MINUTE': 50,
    }
    exec(compile(module, str(PREFETCH), 'exec'), globals_dict)
    return globals_dict['seconds_until_prefetch']


seconds_until_prefetch = _extract_scheduler()


class PrefetchScheduleTests(unittest.TestCase):
    def test_waits_until_2350_on_the_same_day(self) -> None:
        delay = seconds_until_prefetch(datetime(2026, 9, 24, 20, 0, 0))
        self.assertAlmostEqual(delay, 3 * 3600 + 50 * 60, delta=1.0)

    def test_runs_immediately_inside_the_prefetch_window(self) -> None:
        # 进程刚好在 23:55 启动：必须立刻补跑，而不是等 24 小时
        for minute in (50, 55, 59):
            with self.subTest(minute=minute):
                delay = seconds_until_prefetch(datetime(2026, 9, 24, 23, minute, 0))
                self.assertLessEqual(delay, 1.0)

    def test_schedules_next_day_after_midnight(self) -> None:
        delay = seconds_until_prefetch(datetime(2026, 9, 25, 0, 5, 0))
        self.assertAlmostEqual(delay, 23 * 3600 + 45 * 60, delta=1.0)

    def test_just_before_the_window_still_targets_today(self) -> None:
        delay = seconds_until_prefetch(datetime(2026, 9, 24, 23, 49, 30))
        self.assertAlmostEqual(delay, 30.0, delta=1.0)

    def test_delay_is_never_negative(self) -> None:
        for hour in range(24):
            delay = seconds_until_prefetch(datetime(2026, 9, 24, hour, 0, 0))
            self.assertGreater(delay, 0.0, f'hour={hour}')


class PrefetchBoundednessTests(unittest.TestCase):
    def _constants(self) -> dict[str, Any]:
        tree = ast.parse((PLUGIN / 'constants.py').read_text(encoding='utf-8'))
        values: dict[str, Any] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
                continue
            try:
                # 常量文件里允许 10 * 60 这类算式，直接求值即可
                code = compile(ast.Expression(node.value), '<constants>', 'eval')
                values[node.targets[0].id] = eval(code, {'__builtins__': {}}, {})
            except (NameError, TypeError, ValueError, SyntaxError):
                continue
        return values

    def test_prefetch_has_a_wall_clock_budget(self) -> None:
        values = self._constants()
        self.assertIn('PREFETCH_MAX_SECONDS', values)
        self.assertLessEqual(values['PREFETCH_MAX_SECONDS'], 30 * 60, '预热必须有时间上限，不能无限跑')
        self.assertGreater(values['PREFETCH_MAX_SECONDS'], 60)

    def test_prefetch_runs_before_midnight(self) -> None:
        values = self._constants()
        self.assertEqual(values['PREFETCH_HOUR'], 23)
        self.assertGreaterEqual(values['PREFETCH_MINUTE'], 30, '预热要留足下载时间')
        self.assertLess(values['PREFETCH_MINUTE'], 60)

    def test_prefetch_is_opt_outable_and_bounded_per_role(self) -> None:
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        self.assertIn("'DailyWifePrefetchEnabled'", config)
        self.assertIn("'DailyWifePrefetchImagesPerRole'", config)
        # 默认开启（否则等于没修），但每角色张数要小
        self.assertIn('GsBoolConfig(', config)

    def test_prefetch_only_runs_in_gallery_mode_and_respects_the_switch(self) -> None:
        source = PREFETCH.read_text(encoding='utf-8')
        body = source[source.index('async def _prefetch_once('):source.index('def _prefetch_modes(')]
        self.assertIn("_cfg_bool('DailyWifePrefetchEnabled', True)", body)
        self.assertIn("_image_source() != 'gallery'", body)
        self.assertIn('PREFETCH_MAX_SECONDS', body)

    def test_prefetch_skips_images_already_on_disk(self) -> None:
        source = PREFETCH.read_text(encoding='utf-8')
        body = source[source.index('async def _prefetch_once('):source.index('def _prefetch_modes(')]
        self.assertIn('read_url_cache', body)
        self.assertIn("stats['cached'] += 1", body)

    def test_prefetch_never_raises_out_of_the_loop(self) -> None:
        source = PREFETCH.read_text(encoding='utf-8')
        loop = source[source.index('async def _prefetch_loop('):]
        # 不允许 except Exception（skill 红线），但必须有具体异常兜底
        self.assertNotIn('except Exception', loop)
        self.assertIn('except asyncio.CancelledError', loop)
        self.assertIn('except (OSError, RuntimeError, TimeoutError, ValueError)', loop)


class PrefetchLifecycleTests(unittest.TestCase):
    def test_prefetch_loop_is_registered_and_cancelled(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('async def _start_gallery_prefetch_on_startup()', shared)
        self.assertIn('async def _stop_gallery_prefetch_on_shutdown()', shared)
        self.assertIn('_PREFETCH_TASK = asyncio.create_task(_prefetch_loop())', shared)
        self.assertIn('task.cancel()', shared)


if __name__ == '__main__':
    unittest.main()
