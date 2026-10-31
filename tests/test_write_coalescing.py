"""每日记录写合并的回归测试：同一事件循环回合内的写入须合并为一次多值事务，且不可丢行。

GsCore 默认使用 SQLite，所有写入共用一个进程级单写者闸门，实测吞吐约 250 写/秒。
零点高峰每个用户抽签即一次写入，逐条提交会把闸门占满，而等待写入的命令协程仍占着
Core 的命令并发额度，造成无关命令一并被拖慢（见提交 1c0b56e 的实测数据）。

合并换来的性能以两条正确性为前提，二者出错的后果都不是变慢而是数据损坏：快照之后到达
的写入必须开新批而不能落入已提交的旧批（静默丢行）；一个批次失败必须让该批所有等待者
都收到异常（否则部分调用方以为写成功，内存快照随之与数据库分叉）。本文件即以这两点
为核心展开，并额外锁定三条写入路径都已接入合并器、关停时会先排空待写批次。
"""
import ast
import sys
import types
import asyncio
import tempfile
import unittest
import functools
import importlib.util
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
STORE = PLUGIN / 'daily_store.py'


def _load_coalescer(record_cls: Any) -> dict[str, Any]:
    """抽取写合并相关定义单独执行，绕开 daily_store 对 gsuid_core 的依赖。

    合并器的行为由少量模块级状态与函数决定，逐个抽出即可在无 Core 的环境中验证。
    这里同时注入受控的 `DailyWifeRecord` 替身，使「合并成几次事务」「各行内容为何」
    成为可直接断言的量。
    """
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


def _load_models_with_sqlite() -> tuple[Any, Any, Any]:
    """Use a real async SQLite database to verify that a failed upsert rolls back its deletes."""
    from sqlmodel import Field, SQLModel
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    maker_box: dict[str, Any] = {}

    def with_session(fn: Any) -> Any:
        # 复刻 Core 的会话装饰器语义：正常返回后提交，任何异常（含取消）都回滚。
        # 回滚是本文件要验证的对象本身，因此不能用「只开会话不提交」的简化替身。
        @functools.wraps(fn)
        async def wrapped(cls: Any, *args: Any, **kwargs: Any) -> Any:
            async with maker_box['maker']() as session:
                try:
                    result = await fn(cls, session, *args, **kwargs)
                    await session.commit()
                    return result
                except BaseException:
                    await session.rollback()
                    raise

        return wrapped

    def with_read_session(fn: Any) -> Any:
        @functools.wraps(fn)
        async def wrapped(cls: Any, *args: Any, **kwargs: Any) -> Any:
            async with maker_box['maker']() as session:
                return await fn(cls, session, *args, **kwargs)

        return wrapped

    class BaseIDModel(SQLModel):
        id: int | None = Field(default=None, primary_key=True)

    class BaseModel(BaseIDModel):
        bot_id: str = Field(index=True)
        user_id: str = Field(index=True)

    class _Logger:
        def info(self, message: str) -> None:
            pass

    class _Site:
        def register_admin(self, cls: Any) -> Any:
            return cls

    class _PageSchema:
        def __init__(self, **kwargs: Any) -> None:
            pass

    def _decorator(**kwargs: Any) -> Any:
        def apply(fn: Any) -> Any:
            return fn

        return apply

    # 以桩模块替换 Core 的导入面：models.py 的表定义与查询逻辑需要被真实执行，
    # 只有 WebConsole 注册与启动钩子这类与本用例无关的副作用被替换为透传实现。
    core_modules = {
        'gsuid_core': types.ModuleType('gsuid_core'),
        'gsuid_core.logger': types.ModuleType('gsuid_core.logger'),
        'gsuid_core.server': types.ModuleType('gsuid_core.server'),
        'gsuid_core.webconsole': types.ModuleType('gsuid_core.webconsole'),
        'gsuid_core.webconsole.mount_app': types.ModuleType('gsuid_core.webconsole.mount_app'),
        'gsuid_core.utils': types.ModuleType('gsuid_core.utils'),
        'gsuid_core.utils.database': types.ModuleType('gsuid_core.utils.database'),
        'gsuid_core.utils.database.startup': types.ModuleType('gsuid_core.utils.database.startup'),
        'gsuid_core.utils.database.base_models': types.ModuleType('gsuid_core.utils.database.base_models'),
    }
    core_modules['gsuid_core.logger'].logger = _Logger()
    core_modules['gsuid_core.server'].on_core_start_before = _decorator
    mount_app = core_modules['gsuid_core.webconsole.mount_app']
    mount_app.PageSchema = _PageSchema
    mount_app.GsAdminModel = object
    mount_app.site = _Site()
    core_modules['gsuid_core.utils.database.startup'].exec_list = []
    base_models = core_modules['gsuid_core.utils.database.base_models']
    base_models.BaseIDModel = BaseIDModel
    base_models.BaseModel = BaseModel
    base_models.engine = None
    base_models.with_session = with_session
    base_models.with_read_session = with_read_session

    # 记录被替换前的 sys.modules 条目，供 dispose 逐一还原：测试进程内可能已有
    # 其它用例装入了真实的 Core 模块，直接清空会让后续用例不可预期地改变行为。
    old_modules = {name: sys.modules.get(name) for name in core_modules}
    sys.modules.update(core_modules)
    temp = tempfile.TemporaryDirectory()
    engine = create_async_engine(f'sqlite+aiosqlite:///{Path(temp.name) / "atomicity.db"}')
    maker_box['maker'] = async_sessionmaker(engine, expire_on_commit=False)
    # 模块名带上临时目录标识，确保每个用例拿到独立的模块实例与独立的库文件，
    # 避免残留的表数据让「回滚」类断言失去判别力。
    module_name = f'todaywaifu_models_atomic_{id(temp)}'
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN / 'models.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load models module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    async def initialize() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def dispose() -> None:
        await engine.dispose()
        temp.cleanup()
        sys.modules.pop(module_name, None)
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    return module, initialize, dispose


class _FakeRecord:
    """记录每次落库调用，使合并粒度与批次内容成为可直接断言的量。"""

    def __init__(self) -> None:
        self.upsert_calls: list[list[tuple[str, ...]]] = []
        self.delete_calls: list[list[tuple[str, ...]]] = []
        self.apply_calls: list[tuple[list[tuple[str, ...]], list[tuple[str, ...]]]] = []
        self.fail_next = False

    async def apply_rows(
        self,
        rows: list[tuple[str, ...]],
        deletes: list[tuple[str, ...]],
    ) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError('boom')
        self.apply_calls.append((list(rows), list(deletes)))
        if rows:
            self.upsert_calls.append(list(rows))
        if deletes:
            self.delete_calls.append(list(deletes))


def _key(group: str, user: str = 'u1') -> tuple[str, str, str, str, str]:
    return ('2026-09-24', 'bot1', group, 'wives', user)


class CoalescingTests(unittest.TestCase):
    def test_sqlite_flush_rolls_back_delete_when_upsert_fails(self) -> None:
        # 写合并成立的前提是「同批次内写入与删除共处一个事务」。若改为先删后插两次独立
        # 提交，插入失败时删除已生效，用户的记录会被彻底抹掉而非保持原状；
        # 这类损坏只在事务失败时才显形，故此处注入 upsert 失败并在真实 SQLite 上
        # 断言旧记录仍在、新记录未落库。
        async def run() -> None:
            models, initialize, dispose = _load_models_with_sqlite()
            await initialize()
            try:
                seed_key = ('2026-09-24', 'bot1', 'g1', 'wives', 'u1')
                seed_value = {'name': '保留', 'image': 'keep.png', 'updated_at': 1}
                await models.DailyWifeRecord.upsert_rows([(*seed_key, seed_value)])

                original = models.DailyWifeRecord.__dict__['_upsert_rows']

                async def fail_upsert(cls: Any, session: Any, rows: Any) -> None:
                    raise RuntimeError('injected upsert failure')

                models.DailyWifeRecord._upsert_rows = classmethod(fail_upsert)
                try:
                    g = _load_coalescer(models.DailyWifeRecord)
                    batch = g['_current_batch']()
                    new_key = ('2026-09-24', 'bot1', 'g1', 'wives', 'u2')
                    batch.add(new_key, {'name': '新记录', 'updated_at': 2})
                    batch.drop(seed_key)
                    with self.assertRaises(RuntimeError):
                        await batch.ensure_task()
                finally:
                    models.DailyWifeRecord._upsert_rows = original

                self.assertEqual(
                    await models.DailyWifeRecord.get_record(*seed_key),
                    seed_value,
                )
                self.assertIsNone(
                    await models.DailyWifeRecord.get_record(
                        '2026-09-24', 'bot1', 'g1', 'wives', 'u2'
                    )
                )
            finally:
                await dispose()

        asyncio.run(run())

    def test_simultaneous_writes_share_one_transaction(self) -> None:
        # 合并的直接目的：25 个群同回合的写入只能产生一次提交，摊薄单写者闸门压力。
        # 行数必须一一对应，合并不得以丢弃为代价换取提交次数下降。
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
        """关键竞态：快照之后到达的写入必须进入新批次，否则会被静默丢弃。

        提交动作先摘除当前批次再落库；摘除与提交之间若有写入落入已被摘除（即即将提交）
        的批次，该写入不会出现在任何事务中，且不会有任何异常提示。两次 `sleep(0)`
        用于让出至 flush 完成摘除之后，从而稳定复现该窗口。
        """
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            current = g['_current_batch']
            flush = g['_flush_write_batch']

            first = current()
            first.add(_key('g1'), {'name': 'A'})
            task = first.ensure_task()
            # 让出两次事件循环，使 flush 推进到「摘除当前批」之后
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # 此刻应已存在新批次，旧批次不再接受写入
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
        """批次失败必须传播给**所有**等待者，不能有调用方误认为写入成功。

        所有调用方 await 同一个 task，因此异常天然对全体可见；若改为各自等待独立的
        future，未收到通知的一方会继续更新内存快照，内存与数据库自此分叉。
        同一 task 被重复 await 时仍须抛出，故此处连续断言两次。
        """
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
        # 同批次内对同一键的重复写入取最后一次：合并后按键去重，先到的值会被覆盖。
        # 与逐条提交的语义一致，也是「同一回合内后写覆盖先写」这一预期行为的依据。
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
        # 删除后又在同批次写入同一键，最终状态应是「存在」。若实现为固定顺序地
        # 先应用全部删除再应用全部写入即自然满足；反之若删除延后执行，刚写入的
        # 记录会被自己这一批的删除抹掉。
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
        # 与上一条互为反向：写入后又在同批次删除同一键，最终状态应是「不存在」。
        # 两条用例共同固定键级操作的时间序，防止合并实现退化为无序集合运算。
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

    def test_deletes_and_writes_can_share_one_atomic_batch(self) -> None:
        # 删除与写入必须走同一次 apply_rows：拆成两次提交即失去原子性，
        # 中途失败会留下「删除已生效、写入未落库」的中间状态。
        record = _FakeRecord()

        async def run() -> None:
            g = _load_coalescer(record)
            batch = g['_current_batch']()
            batch.add(_key('g1'), {'name': 'A'})
            batch.drop(_key('g2'))
            await batch.ensure_task()

        asyncio.run(run())
        self.assertEqual(len(record.apply_calls), 1)
        rows, deletes = record.apply_calls[0]
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(deletes), 1)

    def test_pending_count_reports_the_open_batch(self) -> None:
        # pending_write_count 是关停前判断「是否还有未落库写入」的依据，
        # 因此计数必须覆盖未提交批次中的全部键，且空批次返回 0。
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
        """无人在场的失败必须被消费，避免运行期持续输出 'exception was never retrieved' 告警。

        调用方可能在等待期间被取消，此时 task 上的异常再无 await 者。若不显式取走，
        事件循环会在 task 被回收时打印告警，掩盖真正的错误来源。
        """
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
        # 合并只覆盖部分写入路径等于没有合并：漏掉的那条仍会逐次占用单写者闸门，
        # 而且它的落库顺序会与合并批次交错，产生更难排查的不一致。
        # 三条路径（单条保存、批量保存、删除）都必须在同一处汇总后提交。
        for name in ('_save_daily_record', '_save_daily_records', '_delete_daily_record'):
            start = self.source.index(f'async def {name}(')
            body = self.source[start:self.source.index('\n\n\n', start)]
            self.assertIn('_submit_writes(', body, name)

    def test_submit_is_atomic_between_get_and_await(self) -> None:
        """`_current_batch` → `add` → `ensure_task` 之间不得出现 await，否则会丢行。

        中间一旦让出事件循环，另一协程可能摘除并提交当前批次，本次写入便会落入
        已经落库的旧批次：既是静默丢行，也破坏了「返回即已提交」的持久性承诺。
        断言仅在 ensure_task 之前的那段文本内检索 await。
        """
        body = self.source[
            self.source.index('async def _submit_writes('):self.source.index('async def _save_daily_records(')
        ]
        before_await = body[:body.index('await batch.ensure_task()')]
        self.assertNotIn('await ', before_await.replace('async def', ''))

    def test_shutdown_flushes_pending_writes(self) -> None:
        # 关停时若只释放线程池而不排空待写批次，已返回成功但尚未提交的写入会随进程
        # 消失。合并引入了「写入已在批中、提交尚未发生」的窗口，必须由关停钩子补上。
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        hook = shared[shared.index('async def _stop_blocking_executor_on_shutdown('):]
        self.assertIn('await flush_pending_writes()', hook)

    def test_models_expose_atomic_multi_context_apply(self) -> None:
        # 跨上下文的多值 upsert 是合并的必要条件：原 upsert_records 只支持单个上下文，
        # 合并只能退化为逐上下文提交，调不动闸门压力。
        models = (PLUGIN / 'models.py').read_text(encoding='utf-8')
        self.assertIn('async def apply_rows(', models)
        self.assertIn('async def _upsert_rows(', models)
        self.assertIn('async def _delete_rows(', models)

    def test_flush_routes_both_operations_through_apply_rows(self) -> None:
        # 汇聚到唯一入口是原子性的实现保障：如果 flush 分别调用 upsert_rows 与
        # delete_rows，两者各自开启会话与提交，删除与写入便不再共处一个事务。
        source = STORE.read_text(encoding='utf-8')
        start = source.index('async def _flush_write_batch(')
        end = source.index('\n\n\n', start)
        body = source[start:end]
        self.assertIn('await DailyWifeRecord.apply_rows(rows, deletes)', body)
        self.assertNotIn('DailyWifeRecord.delete_rows(', body)
        self.assertNotIn('DailyWifeRecord.upsert_rows(', body)


if __name__ == '__main__':
    unittest.main()
