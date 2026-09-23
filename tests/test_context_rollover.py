"""日期翻转必须立刻回收上一天的上下文快照，而不是等一小时的维护循环。"""
import ast
import asyncio
import unittest
import dataclasses
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
REPOSITORY = PLUGIN / 'daily_repository.py'
STORE = PLUGIN / 'daily_store.py'


def _exec_nodes(nodes: list[ast.stmt], globals_dict: dict[str, Any]) -> None:
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, *nodes], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(REPOSITORY), 'exec'), globals_dict)


def _load_repository() -> dict[str, Any]:
    """daily_repository 只有相对导入，抽出来单独跑。"""
    tree = ast.parse(REPOSITORY.read_text(encoding='utf-8'))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in {'ContextKey', 'ContextSnapshot', 'ContextRegistry'}
    ]
    globals_dict: dict[str, Any] = {'asyncio': asyncio, 'dataclass': dataclasses.dataclass}
    _exec_nodes(wanted, globals_dict)
    return globals_dict


repository = _load_repository()
ContextKey = repository['ContextKey']
ContextRegistry = repository['ContextRegistry']


class DropStaleDaysTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ContextRegistry()
        self.yesterday = ContextKey('2026-09-23', 'bot1', 'group1')
        self.today = ContextKey('2026-09-24', 'bot1', 'group1')
        self.other_group_today = ContextKey('2026-09-24', 'bot1', 'group2')

    def test_drops_yesterday_and_keeps_today(self) -> None:
        self.registry.put(self.yesterday, {'wives': {'u1': {'name': 'A'}}})
        self.registry.put(self.today, {'wives': {'u1': {'name': 'B'}}})
        self.registry.put(self.other_group_today, {'wives': {}})

        dropped = self.registry.drop_stale_days('2026-09-24')

        self.assertEqual(dropped, 1)
        self.assertIsNone(self.registry.get(self.yesterday), '上一天的快照必须被回收')
        self.assertIsNotNone(self.registry.get(self.today), '当天快照不能被误删')
        self.assertIsNotNone(self.registry.get(self.other_group_today))

    def test_keeps_locks_so_concurrent_callers_share_the_same_object(self) -> None:
        """翻转瞬间可能仍有协程持有前一天 key 的锁，回收锁会破坏互斥。"""
        lock_before = self.registry.lock_for(self.yesterday)
        self.registry.put(self.yesterday, {'wives': {}})

        self.registry.drop_stale_days('2026-09-24')

        self.assertIs(self.registry.lock_for(self.yesterday), lock_before, '锁不能在翻转时被回收')

    def test_generations_are_reset_so_stale_writes_cannot_republish(self) -> None:
        self.registry.put(self.yesterday, {'wives': {}})
        generation_before = self.registry.generation(self.yesterday)
        self.assertGreater(generation_before, 0)

        self.registry.drop_stale_days('2026-09-24')

        # 翻转时可能还有协程在 hydrate 上一天的快照，它不能把旧快照发布回来
        self.assertFalse(
            self.registry.put(self.yesterday, {'wives': {'u1': {}}}, generation_before),
            '滞后的 hydrate 必须被 generation 比较挡下',
        )
        self.assertIsNone(self.registry.get(self.yesterday))

    def test_no_op_when_nothing_is_stale(self) -> None:
        self.registry.put(self.today, {'wives': {}})
        self.assertEqual(self.registry.drop_stale_days('2026-09-24'), 0)

    def test_clears_finished_inflight_tasks(self) -> None:
        async def run() -> None:
            task: asyncio.Task[dict] = asyncio.ensure_future(asyncio.sleep(0, result={'wives': {}}))
            self.registry.inflight[self.yesterday] = task
            await task
            self.registry.drop_stale_days('2026-09-24')
            self.assertNotIn(self.yesterday, self.registry.inflight)

        asyncio.run(run())


class RolloverWiringTests(unittest.TestCase):
    def test_loading_a_context_triggers_the_rollover_check(self) -> None:
        source = STORE.read_text(encoding='utf-8')
        body = source[source.index('async def _load_daily_context('):source.index('async def _save_daily_records(')]
        self.assertIn('_roll_over_context_day(key.day)', body, '每次 hydrate 都要检查日期翻转')

    def test_rollover_is_a_single_cheap_comparison_on_the_hot_path(self) -> None:
        source = STORE.read_text(encoding='utf-8')
        fn = source[source.index('def _roll_over_context_day('):source.index('async def _load_daily_context(')]
        # 同一天必须直接返回，不能每次都遍历缓存
        self.assertIn('if _LAST_CONTEXT_DAY == day:', fn)
        self.assertIn('return 0', fn)

    def test_rollover_uses_drop_stale_days_not_full_prune(self) -> None:
        """必须用只回收快照的 drop_stale_days，而不是会连锁一起回收的 prune。"""
        source = STORE.read_text(encoding='utf-8')
        fn = source[source.index('def _roll_over_context_day('):source.index('async def _load_daily_context(')]
        self.assertIn('_CONTEXT_REGISTRY.drop_stale_days(day)', fn)
        self.assertNotIn('_CONTEXT_REGISTRY.prune(', fn)

    def test_compat_cache_is_pruned_by_day_prefix(self) -> None:
        source = STORE.read_text(encoding='utf-8')
        fn = source[source.index('def _roll_over_context_day('):source.index('async def _load_daily_context(')]
        self.assertIn("key.startswith(f'{day}:')", fn)


if __name__ == '__main__':
    unittest.main()
