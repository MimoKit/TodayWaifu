# 阻塞 IO 执行路径的回归测试：插件必须使用自有线程池，不得借用 Core 的全局默认
# executor。该约束对应提交 254a093（改用插件专用线程池）与 ddc3f85（修正线程池容量）：
# 在专用池落地前，高峰期的图片下载与读盘会和 Core 及其它插件争抢同一线程池，
# 表现为整个 gscore 卡顿。此处守护的是故障隔离边界，而非某个函数的具体实现。
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
        """Core 的默认 executor 饱和时，插件的阻塞 IO 必须仍能推进。

        这是提交 254a093 的直接回归点：默认 executor 一旦被长任务占满，借用它的阻塞
        调用会与 Core 及其它插件的任务一同排队，图片下载与读盘因此整体停摆。断言采用
        1 秒的宽松上限，只用于区分「独立池立即执行」与「排在默认池队列中等待」，
        不承担性能基准的职责。
        """

        async def run() -> int:
            loop = asyncio.get_running_loop()
            release = threading.Event()
            # 默认池压缩为单线程并长期占用：任何仍借用它的调用必然排队至超时
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
        """池容量必须大于下载信号量，否则线程池自身成为吞吐上限。

        真机压测实测：池=4 而信号量=8 时，排空时间从 13s 恶化到 25s。原因是信号量放行
        的请求在池队列中积压，提升网络并发度带来的收益被线程容量直接抵消；容量下限
        因此按「下载信号量 + 读缓存与回退扫描的余量」确定。
        """
        self.assertGreater(executor.MAX_BLOCKING_WORKERS, 8)
        # 上界同样必要：容量无界时插件自身会演变为新的资源争抢源，与故障隔离的初衷相悖
        self.assertLessEqual(executor.MAX_BLOCKING_WORKERS, 32)

    def test_shutdown_is_idempotent(self) -> None:
        executor.shutdown_blocking_executor()
        executor.shutdown_blocking_executor()
        # 关闭仅置空全局引用，重载后首次调用必须按需重建池子，而非抛出异常
        self.assertEqual(asyncio.run(executor.run_blocking(lambda: 'ok')), 'ok')


class NoDefaultExecutorLeakTests(unittest.TestCase):
    def test_plugin_no_longer_borrows_the_process_default_executor(self) -> None:
        """生产模块不得再出现 asyncio.to_thread，它是借用 Core 全局线程池的入口。

        以源码扫描替代代码评审来守住该约束：重新引入该 API 往往只体现为一行改动，
        评审阶段难以察觉，而一旦引入，插件过载便会外溢到 Core 及其它插件；
        executor.py 是唯一允许在文档中提及该 API 的位置。
        """
        offenders = []
        for path in sorted(PLUGIN.glob('*.py')):
            if path.name == 'executor.py':
                continue  # executor.py 自身是唯一允许提及该 API 的模块
            text = path.read_text(encoding='utf-8-sig')
            if 'asyncio.to_thread(' in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [], f'asyncio.to_thread still used in: {offenders}')

    def test_every_blocking_call_goes_through_the_plugin_executor(self) -> None:
        # 逐模块核对调用点：迁移过程中任一调用点被遗漏，都会让该路径退回默认 executor。
        # members.py 已不再下载头像（改为只解析 URL、由 senders.py 在发送时现取字节），
        # 故它不再是阻塞调用点；此处按当前真实调用方清单核对。
        callers = {
            path.name
            for path in sorted(PLUGIN.glob('*.py'))
            if 'run_blocking(' in path.read_text(encoding='utf-8-sig') and path.name != 'executor.py'
        }
        for expected in ('gallery.py', 'senders.py', 'loli.py', 'custom_role.py'):
            self.assertIn(expected, callers)
        # 头像下载已迁到 senders.py，仍必须走插件线程池而非事件循环
        senders = (PLUGIN / 'senders.py').read_text(encoding='utf-8-sig')
        self.assertIn('run_blocking(_download_member_avatar', senders)


if __name__ == '__main__':
    unittest.main()
