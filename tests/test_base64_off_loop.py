"""图片 base64 必须在插件线程池里编码，不能阻塞事件循环。"""
import ast
import sys
import time
import base64
import asyncio
import unittest
import threading
import importlib.util
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
SENDERS = PLUGIN / 'senders.py'


def _load_executor():
    spec = importlib.util.spec_from_file_location('todaywaifu_executor_b64', PLUGIN / 'executor.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load executor')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


executor = _load_executor()


def _load_encode_helper() -> Any:
    """senders.py 依赖 gsuid_core，单独抽出纯函数 _encode_base64_ref 来跑。"""
    tree = ast.parse(SENDERS.read_text(encoding='utf-8'))
    node = next(
        item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == '_encode_base64_ref'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, node], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {'b64encode': base64.b64encode}
    exec(compile(module, str(SENDERS), 'exec'), globals_dict)
    return globals_dict['_encode_base64_ref']


encode_base64_ref = _load_encode_helper()


class Base64RefTests(unittest.TestCase):
    def test_produces_a_framework_passthrough_ref(self) -> None:
        ref = encode_base64_ref(b'hello')
        self.assertTrue(ref.startswith('base64://'))
        self.assertEqual(base64.b64decode(ref[len('base64://'):]), b'hello')

    def test_round_trips_binary_image_bytes(self) -> None:
        payload = bytes(range(256)) * 512
        ref = encode_base64_ref(payload)
        self.assertEqual(base64.b64decode(ref[len('base64://'):]), payload)

    def test_encoding_happens_off_the_event_loop_thread(self) -> None:
        async def run() -> tuple[int, int]:
            loop_thread = threading.get_ident()
            worker_thread = await executor.run_blocking(
                lambda: threading.get_ident()
            )
            return loop_thread, worker_thread

        loop_thread, worker_thread = asyncio.run(run())
        self.assertNotEqual(loop_thread, worker_thread)

    def test_a_large_encode_does_not_stall_the_event_loop(self) -> None:
        """编码 2MB 图时事件循环必须还能跑别的协程。"""
        payload = b'x' * (2 * 1024 * 1024)

        async def run() -> float:
            ticks = 0

            async def ticker() -> None:
                nonlocal ticks
                while True:
                    ticks += 1
                    await asyncio.sleep(0.001)

            task = asyncio.ensure_future(ticker())
            started = time.perf_counter()
            await executor.run_blocking(encode_base64_ref, payload)
            elapsed = time.perf_counter() - started
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.assertGreater(ticks, 0, '编码期间事件循环被完全阻塞了')
            return elapsed

        asyncio.run(run())


class SenderEncodingWiringTests(unittest.TestCase):
    def test_helpers_route_through_the_plugin_executor(self) -> None:
        source = SENDERS.read_text(encoding='utf-8')
        helper = source[source.index('def _encode_base64_ref('):source.index('async def _image_message(')]
        self.assertIn('b64encode(data).decode()', helper)

        message_fn = source[
            source.index('async def _image_message('):source.index('async def _image_message_from_path(')
        ]
        self.assertIn('run_blocking(_encode_base64_ref, data)', message_fn)

    def test_upload_mode_still_hands_raw_bytes_to_the_framework(self) -> None:
        """EnablePicSrv 打开时框架需要原始字节做图床上传，不能预先编码。"""
        source = SENDERS.read_text(encoding='utf-8')
        message_fn = source[
            source.index('async def _image_message('):source.index('async def _image_message_from_path(')
        ]
        self.assertIn('if IS_UPLOAD:', message_fn)
        self.assertIn('return MessageSegment.image(data)', message_fn)

    def test_local_path_helper_reads_and_encodes_off_loop(self) -> None:
        source = SENDERS.read_text(encoding='utf-8')
        fn = source[source.index('async def _image_message_from_path('):]
        fn = fn[:fn.index('\n\n\n')]
        self.assertIn('run_blocking(read_file_bytes_cached, path)', fn)

    def test_hot_path_senders_no_longer_build_image_segments_inline(self) -> None:
        source = SENDERS.read_text(encoding='utf-8')
        for name in ('_send_role_image', '_send_loli_result_image', '_send_local_image'):
            start = source.index(f'async def {name}(')
            end = source.find('\nasync def ', start + 1)
            block = source[start:end if end >= 0 else None]
            self.assertNotIn('MessageSegment.image(', block, f'{name} 仍在事件循环上构造图片段')

    def test_list_commands_also_use_the_off_loop_helper(self) -> None:
        for name in ('loli.py', 'custom_role.py'):
            source = (PLUGIN / name).read_text(encoding='utf-8')
            self.assertIn('await _image_message_from_path(path)', source, name)
            self.assertNotIn('MessageSegment.image(path)', source, name)


if __name__ == '__main__':
    unittest.main()
