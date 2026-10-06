"""命令协程只承担入队，不得以等待图库下载的方式占用 Core 的命令并发额度。

框架 `bot.py` 的 `_process` 先取得 CommandSemaphore 名额、再启动命令协程，名额直到协程结束方才
归还。命令协程若在体内等待网络，额度便在整段等待期内被占住；默认 25 个名额耗尽后
`_process` 停止消费队列，该 bot 上所有插件的命令一并停滞。31c225d 据此把「下载 + 编码 +
发送」整段移入插件自有的有界队列与后台 worker，本文件锁定该结构不被改回同步执行。

守护的契约：命令路径不含下载、编码、发送与读盘；依赖请求上下文的数据在入队时刻取样；
队列满时降级为只发文字而非阻塞调用方；worker 随 Core 启停，并在插件重载后由入队口自愈。
"""
import ast
import time
import asyncio
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
SENDERS = PLUGIN / 'senders.py'


def _function_source(source: str, name: str, end_marker: str) -> str:
    start = source.index(f'async def {name}(')
    return source[start:source.index(end_marker, start)]


class CommandSlotTests(unittest.TestCase):
    # 本类断言全部落在源码文本上：这些入口是纯转发壳，无返回值、无副作用可供观测，
    # 只有文本层面才能确认某个调用「没有出现」，AST 则会正常接受任何合法调用。
    def setUp(self) -> None:
        self.source = SENDERS.read_text(encoding='utf-8')

    def test_public_senders_only_enqueue(self) -> None:
        """公开的发送入口体内不得出现下载、编码、发送或读盘调用。

        这些调用各自都可能阻塞数秒（含重试与退避，最坏二十秒以上，见 c07ca0c 的收敛），
        一旦内联在命令协程里，命令额度就被整段等待占用，退化回 31c225d 之前的状态。断言
        以文本形式覆盖，因为行为测试无法区分「同步执行」与「后台 worker 执行得快」。
        """
        for name, end in (
            ('_send_role_image', 'async def _send_daily_result_image('),
            ('_send_loli_result_image', 'async def _send_local_image('),
        ):
            body = _function_source(self.source, name, end)
            for forbidden in ('_acquire_gallery_image', '_image_message', 'read_file_bytes_cached', '_safe_send('):
                self.assertNotIn(forbidden, body, f'{name} 不应直接做 {forbidden}')
            self.assertIn('_enqueue_image_job(', body, name)

    def test_ai_summary_is_injected_at_enqueue_time(self) -> None:
        """AI 摘要必须在入队时刻注入：后台 worker 发送时已无请求上下文可取。

        入队与发送分离后，原先把摘要拼接在发送处的写法会静默丢失摘要，用户只收到图片。
        """
        role_body = _function_source(
            self.source, '_send_role_image', 'async def _send_daily_result_image('
        )
        loli_body = _function_source(self.source, '_send_loli_result_image', 'async def _send_local_image(')
        self.assertIn('_ai_return_draw(kind, role.name, text)', role_body)
        self.assertIn("_ai_return_draw(kind, '', text)", loli_body)

    def test_deliver_functions_keep_the_real_work(self) -> None:
        # 真实工作只是搬迁而非删除：`_deliver_*` 必须仍完成获取与发送，否则命令会入队后
        # 静默丢失图片——队列本身不会报错，退化为「只回文字」而不留任何痕迹。
        for name, marker in (
            ('_deliver_role_image', '_acquire_gallery_image'),
            ('_deliver_loli_result_image', '_acquire_gallery_image'),
        ):
            body = _function_source(self.source, name, '\n\n\n')
            self.assertIn(marker, body, name)
            self.assertIn('_safe_send(', body, name)

    def test_queue_is_bounded_and_never_blocks_the_caller(self) -> None:
        self.assertIn('asyncio.Queue(maxsize=IMAGE_DELIVERY_QUEUE_MAX)', self.source)
        enqueue = self.source[
            self.source.index('async def _enqueue_image_job('):self.source.index('async def _send_role_image(')
        ]
        # 必须 put_nowait（满了就降级），不能 await put：后者在队列满时重新引入等待，
        # 把已消除的阻塞从下载路径搬回入队路径，命令额度依旧会被耗尽
        self.assertIn('put_nowait(job)', enqueue)
        self.assertNotIn('await _IMAGE_DELIVERY_QUEUE.put(', enqueue)
        self.assertIn('except asyncio.QueueFull:', enqueue)
        self.assertIn('await _safe_send(', enqueue)


class BehavioralSlotTests(unittest.TestCase):
    _BURST_SIZE = 1000
    _QUEUE_MAX = 512
    # 每轮都重建队列与命名空间：容量固定 512，复用会让后续轮次全部落进降级分支，
    # 从而测不到「前 512 次立即成功」这条路径。
    _ROUNDS = 5
    # 1000 次入队须远快于任何一次网络下载。取多轮最小值而非单轮样本：单轮耗时受调度
    # 抖动与 GC 影响，实测 300 轮 p95 仅 0.084s 而最大 0.493s，用单轮会把这类噪声当成
    # 阻塞；真出现阻塞性等待时每一轮都会同样慢，最小值不会随之下降。
    _ELAPSED_BUDGET_SECONDS = 0.5

    def _build_namespace(self) -> dict[str, Any]:
        tree = ast.parse(SENDERS.read_text(encoding='utf-8'))
        wanted = [
            node
            for node in tree.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in {'_ImageJob', '_enqueue_image_job'}
        ]
        future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
        module = ast.Module(body=[future, *wanted], type_ignores=[])
        ast.fix_missing_locations(module)

        class _FakeBot:
            pass

        sent: list[object] = []

        async def fake_send(bot: object, message: object) -> None:
            sent.append(message)

        globals_dict: dict[str, Any] = {
            'asyncio': asyncio,
            'dataclass': __import__('dataclasses').dataclass,
            'Bot': _FakeBot,
            'RoleCandidate': object,
            'logger': __import__('logging').getLogger('test'),
            'LOG_PREFIX': '[测试]',
            'IMAGE_DELIVERY_QUEUE_MAX': self._QUEUE_MAX,
            '_IMAGE_DELIVERY_QUEUE': asyncio.Queue(maxsize=self._QUEUE_MAX),
            '_send_loli_text': fake_send,
            '_safe_send': fake_send,
            'start_image_delivery_workers': lambda: None,
            '_prune_image_delivery_workers': lambda: None,
            'sent': sent,
            '_FakeBot': _FakeBot,
        }
        exec(compile(module, str(SENDERS), 'exec'), globals_dict)
        return globals_dict

    async def _enqueue_burst(self) -> float:
        globals_dict = self._build_namespace()
        job = globals_dict['_ImageJob'](
            bot=globals_dict['_FakeBot'](),
            role=object(),
            image='https://x/y.png',
            text='文字',
            user_id=1,
            is_group=True,
            kind='wife',
            loli_style=False,
        )
        started = time.perf_counter()
        accepted = 0
        for _ in range(self._BURST_SIZE):
            if await globals_dict['_enqueue_image_job'](job):
                accepted += 1
        elapsed = time.perf_counter() - started

        # 容量 512：前 512 次入队成功，其余转为只发文字的降级分支，全程不得阻塞。
        # 需要同时确认降级分支确实发出了文字，否则“不阻塞”可由丢弃任务来实现。
        self.assertEqual(accepted, self._QUEUE_MAX)
        self.assertEqual(globals_dict['_IMAGE_DELIVERY_QUEUE'].qsize(), self._QUEUE_MAX)
        self.assertEqual(
            len(globals_dict['sent']),
            self._BURST_SIZE - self._QUEUE_MAX,
            '队列满时必须立刻降级为只发文字',
        )
        return elapsed

    def test_enqueue_returns_immediately_even_when_delivery_is_slow(self) -> None:
        """以真实事件循环复核入队路径的耗时与投递耗力无关。

        前面的文本断言只能证明不存在已知的阻塞调用；此处改为实际测量，因为入队路径一旦引入
        任何未被列举的等待（例如满队列时的反压），命令额度仍会被占用。
        """
        timings = [asyncio.run(self._enqueue_burst()) for _ in range(self._ROUNDS)]
        best = min(timings)
        self.assertLess(
            best,
            self._ELAPSED_BUDGET_SECONDS,
            f'入队 {self._BURST_SIZE} 次最快 {best:.3f}s，入队路径被阻塞了',
        )


class WorkerLifecycleTests(unittest.TestCase):
    # worker 生命周期没有任何外部可观测信号：丢失或静默退出只会表现为「图迟到」，不会
    # 报错，故须逐条核对启停挂载点、自愈路径与异常放行范围。
    def setUp(self) -> None:
        self.source = SENDERS.read_text(encoding='utf-8')

    def test_workers_are_started_and_stopped_with_the_core(self) -> None:
        # worker 绑定 Core 生命周期：只启动不停止会让任务在执行器关闭后仍持有事件循环
        # 引用，插件重载与进程退出时因此出现悬挂任务告警
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('start_image_delivery_workers()', shared)
        self.assertIn('await stop_image_delivery_workers()', shared)

    def test_enqueue_revives_workers_missing_after_reload(self) -> None:
        """入队口必须补足重载后丢失的 worker。

        插件重载只重跑 `on_core_start`，worker 句柄表被重置而旧任务已被取消；若不在入队路径
        上自愈，图片与文字会静默积压在队列中无人消费（dd71d75 修复的正是该情形）。
        """
        enqueue = self.source[
            self.source.index('async def _enqueue_image_job('):self.source.index('async def _send_role_image(')
        ]
        self.assertIn('_prune_image_delivery_workers()', enqueue)

    def test_maintenance_restarts_dead_workers(self) -> None:
        # 除入队口外，维护循环也须兜底：worker 因未捕获异常退出后若无入队流量，
        # 仅靠入队自愈将无人触发，积压会持续到下一次命令到来
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        loop = shared[
            shared.index('async def _cache_maintenance_once('):shared.index('async def _cache_maintenance_loop(')
        ]
        self.assertIn('_prune_image_delivery_workers()', loop)

    def test_worker_failure_does_not_kill_the_loop(self) -> None:
        # worker 是长驻循环，单次投递的异常若向外冒泡，任务即终止且没有任何调用方会
        # 察觉。CancelledError 必须单独放行，否则 Core 关闭时的正常取消会被当作投递失败
        # 记录；task_done 则须在任何退出路径上调用，否则队列的未完成计数永久失衡。
        worker = self.source[
            self.source.index('async def _image_delivery_worker('):
            self.source.index('def start_image_delivery_workers(')
        ]
        self.assertIn('except asyncio.CancelledError', worker)
        self.assertIn('except (OSError, RuntimeError, TimeoutError, ValueError, TypeError)', worker)
        self.assertIn('task_done()', worker)


if __name__ == '__main__':
    unittest.main()
