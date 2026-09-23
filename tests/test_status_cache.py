"""状态聚合：每次写入都重算会让控制台轮询次次打全表，必须降频且改用 COUNT。"""
import ast
import asyncio
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )
PLUGIN = ROOT / 'TodayWaifu'
STATUS = PLUGIN / 'status.py'
MODELS = PLUGIN / 'models.py'


class _FakeClock:
    def __init__(self) -> None:
        self.now = 5000.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeRecord:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def count_daily_records(self, day: str, buckets: tuple[str, ...]) -> dict[str, int]:
        self.calls.append(day)
        return {'wife': 1, 'loli': 2, 'shota': 0, 'husband': 0}


def _load_status_globals(clock: _FakeClock, calls: list[str]) -> dict[str, Any]:
    tree = ast.parse(STATUS.read_text(encoding='utf-8'))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {'_today_record_counts', 'invalidate_status_cache'}
    ]
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, *wanted], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {
        'asyncio': asyncio,
        'time': clock,
        '_today_key': lambda: '2026-09-24',
        '_daily_bucket_name': lambda kind: kind,
        'DailyWifeRecord': _FakeRecord(calls),
        'STATUS_MIN_RECOMPUTE_SECONDS': 30.0,
        '_STATUS_INFLIGHT': None,
        '_STATUS_CACHE': None,
        '_STATUS_COMPUTED_AT': 0.0,
        '_STATUS_STALE': True,
    }
    exec(compile(module, str(STATUS), 'exec'), globals_dict)
    return globals_dict


class StatusThrottleTests(unittest.TestCase):
    def test_repeated_reads_hit_the_cache(self) -> None:
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            counts = g['_today_record_counts']
            first = await counts()
            second = await counts()
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1, '未过期时不应重复查询')

        asyncio.run(run())

    def test_write_invalidation_does_not_recompute_immediately(self) -> None:
        """高峰期每次写入都重算，会让控制台每次轮询都打一次数据库聚合。"""
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            counts, invalidate = g['_today_record_counts'], g['invalidate_status_cache']
            await counts()
            self.assertEqual(len(calls), 1)

            for _ in range(20):
                invalidate()
                await counts()
            self.assertEqual(len(calls), 1, '最小重算间隔内不应重算')

        asyncio.run(run())

    def test_recomputes_once_the_minimum_interval_elapsed(self) -> None:
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            counts, invalidate = g['_today_record_counts'], g['invalidate_status_cache']
            await counts()
            invalidate()
            clock.advance(29.0)
            await counts()
            self.assertEqual(len(calls), 1)

            clock.advance(2.0)
            await counts()
            self.assertEqual(len(calls), 2, '超过最小间隔后必须重算')

        asyncio.run(run())

    def test_no_recompute_when_nothing_was_written(self) -> None:
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            counts = g['_today_record_counts']
            await counts()
            clock.advance(3600.0)
            await counts()
            self.assertEqual(len(calls), 1, '没有写入就不该重算，哪怕过了很久')

        asyncio.run(run())

    def test_invalidate_only_marks_stale(self) -> None:
        clock, calls = _FakeClock(), []

        async def run() -> None:
            g = _load_status_globals(clock, calls)
            await g['_today_record_counts']()
            self.assertIsNotNone(g['_STATUS_CACHE'])
            g['invalidate_status_cache']()
            self.assertIsNotNone(g['_STATUS_CACHE'], 'invalidate 只标记过期，不丢弃快照')
            self.assertTrue(g['_STATUS_STALE'])

        asyncio.run(run())


class CountQueryTests(unittest.TestCase):
    @staticmethod
    def _count_body() -> str:
        """count_daily_records 的函数体（剥掉 docstring，避免注释里的词误伤断言）。"""
        tree = ast.parse(MODELS.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != 'DailyWifeRecord':
                continue
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == 'count_daily_records':
                    return '\n'.join(
                        ast.unparse(stmt) for stmt in item.body if not _is_docstring(stmt)
                    )
        raise AssertionError('count_daily_records not found')

    def test_count_uses_sql_aggregation_not_a_payload_scan(self) -> None:
        body = self._count_body()
        self.assertIn('func.count()', body)
        self.assertIn('.group_by(cls.bucket)', body)
        # 不再把 payload 拉回来逐行解析
        self.assertNotIn('cls.payload', body)
        self.assertNotIn('json.loads', body)

    def test_count_filters_on_indexed_columns(self) -> None:
        body = self._count_body()
        # 判定条件必须落在 name / origin 列上（origin 已由 _row_from_value 维护）
        self.assertIn("cls.name != ''", body)
        self.assertIn("cls.origin == 'self'", body)

    def test_origin_column_matches_the_old_exclusion_rules(self) -> None:
        """origin == 'self' 必须等价于「没有 stolen_from / gifted_from / safe」。"""
        source = MODELS.read_text(encoding='utf-8')
        origin = source[source.index('def _record_origin('):source.index('def split_context_key(')]
        for marker in ('stolen_from', 'gifted_from', 'safe'):
            self.assertIn(marker, origin, marker)

    def test_min_recompute_interval_is_bounded(self) -> None:
        tree = ast.parse((PLUGIN / 'constants.py').read_text(encoding='utf-8'))
        values: dict[str, Any] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                try:
                    code = compile(ast.Expression(node.value), '<constants>', 'eval')
                    values[node.targets[0].id] = eval(code, {'__builtins__': {}}, {})
                except (NameError, TypeError, ValueError, SyntaxError):
                    continue
        interval = values['STATUS_MIN_RECOMPUTE_SECONDS']
        self.assertGreaterEqual(interval, 5.0, '间隔太小等于没降频')
        self.assertLessEqual(interval, 300.0, '间隔太大会让状态页数字明显滞后')


if __name__ == '__main__':
    unittest.main()
