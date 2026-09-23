"""写合并：同一瞬间的写入要合成一条多值 upsert，且**一行都不能丢**。

GsCore 默认 SQLite，所有写排一个进程级单写者闸门（实测约 250 写/秒）。
零点高峰逐条提交会把闸门压满，而命令协程在等写时仍占着 Core 的命令额度。
"""
import ast
import asyncio
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
STORE = PLUGIN / 'daily_store.py'


def _load_coalescer(record_cls: Any) -> dict[str, Any]:
    """抽出写合并相关定义单独跑（daily_store 依赖 gsuid_core）。"""
    tree = ast.parse(STORE.read_text(encoding='utf-8'))
    wanted: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == '_WriteBatch':
            wanted.append(node)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name in {
            '_flush_write_batch',
            'flush_pending_writes',
        }:
            wanted.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in {
            '_consume_batch_exception',
            '_current_batch',
            'pending_write_count',
        }:
            wanted.append(node)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, *wanted], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {
        'asyncio': asyncio,
        'logger': __import__('logging').getLogger('test'),
        'DailyWifeRecord': record_cls,
        '_PENDING_BATCH': None,
        'RoleRecordValue': dict,
    }
    exec(compile(module, str(STORE), 'exec'), globals_dict)
    return globals_dict


class _FakeRecord:
    """记录每次落库调用，用于断言合并行为。"""

    def __init__(self) -> None:
        self.upsert_calls: list[list[tuple[str, ...]]] = []
        self.delete_calls: list[list[tuple[str, ...]]] = []
        self.fail_next = False

    async def upsert_rows(self, rows: list[tuple[str, ...]]) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError('boom')
        self.upsert_calls.append(list(rows))

    async def delete_rows(self, rows: list[tuple[str, ...]]) -> None:
        self.delete_calls.append(list(rows))


def _key(group: str, user: str = 'u1') -> tuple[str, str, str, str, str]:
    return ('2026-09-24', 'bot1', group, 'wives', user)


class CoalescingTests(unittest.TestCase):
    def test_simultaneous_writes_share_one_transaction(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            for i in range(25):
                batch.add(_key(f'g{i}'), {'name': f'角色{i}'})
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(len(record.upsert_calls), 1, '25 个群同时写应只提交一次')
        self.assertEqual(len(record.upsert_calls[0]), 25, '一行都不能丢')

    def test_writes_arriving_after_the_snapshot_start_a_new_batch(self) -> None:
        """关键竞态：快照之后到达的写入必须开新批，不能被静默丢弃。"""
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            current = g['_current_batch']
            flush = g['_flush_write_batch']

            first = current()
            first.add(_key('g1'), {'name': 'A'})
            task = first.ensure_task()
            # 让 flush 走到「摘除当前批」之后
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # 此时应已开新批
            second = current()
            self.assertIsNot(second, first, '快照后必须开新批')
            second.add(_key('g2'), {'name': 'B'})
            await second.ensure_task()
            await task
            self.assertIsNot(flush, None)

        asyncio.run(run())
        self.assertEqual(len(record.upsert_calls), 2)
        self.assertEqual(record.upsert_calls[0][0][2], 'g1')
        self.assertEqual(record.upsert_calls[1][0][2], 'g2')

    def test_all_waiters_see_a_flush_failure(self) -> None:
        """一个批次失败必须让**所有**等待者都拿到异常，不能有人以为写成功了。"""
        record = _FakeRecord()
        record.fail_next = True

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            task = batch.ensure_task()
            with self.assertRaises(RuntimeError):
                await task
            with self.assertRaises(RuntimeError):
                await task

        asyncio.run(run())

    def test_later_value_wins_within_a_batch(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': '旧'})
            batch.add(_key('g1'), {'name': '新'})
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(len(record.upsert_calls[0]), 1)
        self.assertEqual(record.upsert_calls[0][0][5], {'name': '新'})

    def test_delete_then_write_same_key_keeps_the_write(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.drop(_key('g1'))
            batch.add(_key('g1'), {'name': 'A'})
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(record.delete_calls, [])
        self.assertEqual(len(record.upsert_calls[0]), 1)

    def test_write_then_delete_same_key_keeps_the_delete(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            batch.drop(_key('g1'))
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(record.upsert_calls, [])
        self.assertEqual(len(record.delete_calls[0]), 1)

    def test_deletes_and_writes_can_share_a_batch(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            batch.drop(_key('g2'))
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(len(record.upsert_calls[0]), 1)
        self.assertEqual(len(record.delete_calls[0]), 1)

    def test_pending_count_reports_the_open_batch(self) -> None:
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            self.assertEqual(g['pending_write_count'](), 0)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            batch.drop(_key('g2'))
            self.assertEqual(g['pending_write_count'](), 2)

        asyncio.run(run())

    def test_exception_is_consumed_when_nobody_awaits(self) -> None:
        """调用方被取消时不能留下 'exception was never retrieved' 告警。"""
        record = _FakeRecord()
        record.fail_next = True

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            task = batch.ensure_task()
            await asyncio.sleep(0.05)
            self.assertTrue(task.done())
            self.assertIsNotNone(task.exception())

        asyncio.run(run())


class WiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = STORE.read_text(encoding='utf-8')

    def test_all_three_write_paths_go_through_the_coalescer(self) -> None:
        for name in ('_save_daily_record', '_save_daily_records', '_delete_daily_record'):
            start = self.source.index(f'async def {name}(')
            body = self.source[start:self.source.index('\n\n\n', start)]
            self.assertIn('_submit_writes(', body, name)

    def test_submit_is_atomic_between_get_and_await(self) -> None:
        """`_current_batch` → `add` → `ensure_task` 之间不能有 await，否则会丢行。"""
        body = self.source[
            self.source.index('async def _submit_writes('):self.source.index('async def _save_daily_records(')
        ]
        before_await = body[:body.index('await batch.ensure_task()')]
        self.assertNotIn('await ', before_await.replace('async def', ''))

    def test_shutdown_flushes_pending_writes(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        hook = shared[shared.index('async def _stop_blocking_executor_on_shutdown('):]
        self.assertIn('await flush_pending_writes()', hook)

    def test_models_expose_a_multi_context_upsert(self) -> None:
        models = (PLUGIN / 'models.py').read_text(encoding='utf-8')
        self.assertIn('async def upsert_rows(', models)
        self.assertIn('async def delete_rows(', models)


if __name__ == '__main__':
    unittest.main()
