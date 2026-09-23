import sys
import time
import asyncio
import unittest
import threading
import importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'


def _load_executor():
    spec = importlib.util.spec_from_file_location('todaywaifu_executor', PLUGIN / 'executor.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load executor module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


executor = _load_executor()


class DedicatedExecutorTests(unittest.TestCase):
    def test_run_blocking_uses_its_own_thread(self) -> None:
        async def run() -> tuple[str, int]:
            loop_thread = threading.get_ident()
            worker = await executor.run_blocking(threading.get_ident)
            return loop_thread, worker

        loop_thread, worker = asyncio.run(run())
        self.assertNotEqual(loop_thread, worker, 'run_blocking must not run on the event loop thread')

    def test_run_blocking_is_not_starved_by_a_saturated_default_executor(self) -> None:
        """核心回归：Core 的默认 executor 被占满时，插件仍能跑自己的阻塞 IO。"""

        async def run() -> int:
            loop = asyncio.get_running_loop()
            release = threading.Event()
            # 把默认 executor 压缩成 1 个线程并彻底占满它
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            blocker = loop.run_in_executor(None, release.wait)

            started = time.perf_counter()
            value = await executor.run_blocking(lambda: 7)
            elapsed = time.perf_counter() - started

            release.set()
            await blocker
            self.assertLess(elapsed, 1.0, 'plugin blocking IO must not queue behind the Core default executor')
            return value

        self.assertEqual(asyncio.run(run()), 7)

    def test_worker_count_exceeds_the_download_semaphore(self) -> None:
        """池必须大于下载信号量，否则线程池自己成了瓶颈、吞吐减半。

        真机压测实测：池=4 而信号量=8 时，排空时间从 13s 恶化到 25s。
        """
        self.assertGreater(executor.MAX_BLOCKING_WORKERS, 8)
        # 但也不能无限大：插件线程数必须有界，否则等于把 Core 的线程池问题搬回自己身上
        self.assertLessEqual(executor.MAX_BLOCKING_WORKERS, 32)

    def test_shutdown_is_idempotent(self) -> None:
        executor.shutdown_blocking_executor()
        executor.shutdown_blocking_executor()
        # 关闭后再次调用会重建池子，不能抛异常
        self.assertEqual(asyncio.run(executor.run_blocking(lambda: 'ok')), 'ok')


class NoDefaultExecutorLeakTests(unittest.TestCase):
    def test_plugin_no_longer_borrows_the_process_default_executor(self) -> None:
        """生产代码里不允许再出现 asyncio.to_thread —— 那是借用 Core 全局线程池的入口。"""
        offenders = []
        for path in sorted(PLUGIN.glob('*.py')):
            if path.name == 'executor.py':
                continue  # 只有这里允许在文档里提到它
            text = path.read_text(encoding='utf-8-sig')
            if 'asyncio.to_thread(' in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], f'asyncio.to_thread still used in: {offenders}')

    def test_every_blocking_call_goes_through_the_plugin_executor(self) -> None:
        # run_blocking 的调用点必须真的落在插件模块里，避免误删
        callers = {
            path.name
            for path in sorted(PLUGIN.glob('*.py'))
            if 'run_blocking(' in path.read_text(encoding='utf-8-sig') and path.name != 'executor.py'
        }
        for expected in ('gallery.py', 'senders.py', 'members.py', 'loli.py', 'custom_role.py'):
            self.assertIn(expected, callers)


if __name__ == '__main__':
    unittest.main()
