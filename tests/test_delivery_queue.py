"""命令协程必须只做入队就返回，不能把 Core 的命令并发额度耗在等图库下载上。

框架 `bot.py` 的 `_process` 是「先拿 CommandSemaphore 名额、再跑协程」，
名额在协程结束才归还。命令协程一旦在等网络，名额就一直被占着；
25 个名额被占满后 `_process` 停止消费队列，该 bot 上所有插件的命令一起卡住。
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
    def setUp(self) -> None:
        self.source = SENDERS.read_text(encoding='utf-8')

    def test_public_senders_only_enqueue(self) -> None:
        """公开发送函数体里不允许出现任何阻塞调用（下载/编码/发送/读盘）。"""
        for name, end in (
            ('_send_role_image', 'async def _send_daily_result_image('),
            ('_send_loli_result_image', 'async def _send_local_image('),
        ):
            body = _function_source(self.source, name, end)
            for forbidden in ('_acquire_gallery_image', '_image_message', 'read_file_bytes_cached', '_safe_send('):
                self.assertNotIn(forbidden, body, f'{name} 不应直接做 {forbidden}')
            self.assertIn('_enqueue_image_job(', body, name)

    def test_ai_summary_is_injected_at_enqueue_time(self) -> None:
        """后台 worker 发送时已无请求上下文，摘要必须在入队那一刻注入。"""
        role_body = _function_source(
            self.source, '_send_role_image', 'async def _send_daily_result_image('
        )
        loli_body = _function_source(self.source, '_send_loli_result_image', 'async def _send_local_image(')
        self.assertIn('_ai_return_draw(kind, role.name, text)', role_body)
        self.assertIn("_ai_return_draw(kind, '', text)", loli_body)

    def test_deliver_functions_keep_the_real_work(self) -> None:
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
        # 必须 put_nowait（满了就降级），不能 await put（那等于把阻塞搬回来）
        self.assertIn('put_nowait(job)', enqueue)
        self.assertNotIn('await _IMAGE_DELIVERY_QUEUE.put(', enqueue)
        self.assertIn('except asyncio.QueueFull:', enqueue)
        self.assertIn('await _safe_send(', enqueue)


class BehavioralSlotTests(unittest.TestCase):
    def test_enqueue_returns_immediately_even_when_delivery_is_slow(self) -> None:
        """用一个永远跑不完的投递实现，验证入队路径耗时与投递无关。"""
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
            'IMAGE_DELIVERY_QUEUE_MAX': 512,
            '_IMAGE_DELIVERY_QUEUE': asyncio.Queue(maxsize=512),
            '_send_loli_text': fake_send,
            '_safe_send': fake_send,
            'start_image_delivery_workers': lambda: None,
            '_prune_image_delivery_workers': lambda: None,
        }
        exec(compile(module, str(SENDERS), 'exec'), globals_dict)

        async def run() -> float:
            job = globals_dict['_ImageJob'](
                bot=_FakeBot(),
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
            for _ in range(1000):
                if await globals_dict['_enqueue_image_job'](job):
                    accepted += 1
            elapsed = time.perf_counter() - started
            # 前 512 个入队，其余走「只发文字」降级分支，全程都不能阻塞
            self.assertEqual(accepted, 512)
            self.assertEqual(globals_dict['_IMAGE_DELIVERY_QUEUE'].qsize(), 512)
            self.assertEqual(len(sent), 1000 - 512, '队列满时必须立刻降级为只发文字')
            return elapsed

        elapsed = asyncio.run(run())
        # 512 个名额塞满后剩下的走降级分支，整体必须远快于任何一次网络下载
        self.assertLess(elapsed, 0.5, f'入队 1000 次耗时 {elapsed:.3f}s，入队路径被阻塞了')


class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SENDERS.read_text(encoding='utf-8')

    def test_workers_are_started_and_stopped_with_the_core(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('start_image_delivery_workers()', shared)
        self.assertIn('await stop_image_delivery_workers()', shared)

    def test_enqueue_revives_workers_missing_after_reload(self) -> None:
        """重载插件只重跑 @on_core_start，句柄表被重置，入队必须自愈补足 worker。"""
        enqueue = self.source[
            self.source.index('async def _enqueue_image_job('):self.source.index('async def _send_role_image(')
        ]
        self.assertIn('_prune_image_delivery_workers()', enqueue)

    def test_maintenance_restarts_dead_workers(self) -> None:
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        loop = shared[
            shared.index('async def _cache_maintenance_once('):shared.index('async def _cache_maintenance_loop(')
        ]
        self.assertIn('_prune_image_delivery_workers()', loop)

    def test_worker_failure_does_not_kill_the_loop(self) -> None:
        worker = self.source[
            self.source.index('async def _image_delivery_worker('):
            self.source.index('def start_image_delivery_workers(')
        ]
        self.assertIn('except asyncio.CancelledError', worker)
        self.assertIn('except (OSError, RuntimeError, TimeoutError, ValueError, TypeError)', worker)
        self.assertIn('task_done()', worker)


if __name__ == '__main__':
    unittest.main()
