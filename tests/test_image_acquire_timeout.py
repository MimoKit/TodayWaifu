"""图片获取超时必须归还 Core 的命令并发额度，且不能掐断底层下载。"""
import ast
import asyncio
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENDERS = ROOT / 'TodayWaifu' / 'senders.py'


def _node(tree: ast.Module, name: str) -> ast.stmt:
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f'{name} not found in {SENDERS.name}')


def _exec_nodes(nodes: list[ast.stmt], globals_dict: dict[str, Any]) -> None:
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, *nodes], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(SENDERS), 'exec'), globals_dict)


def _load_helpers(timeout: float, download: Any) -> dict[str, Any]:
    tree = ast.parse(SENDERS.read_text(encoding='utf-8'))
    globals_dict: dict[str, Any] = {
        'asyncio': asyncio,
        'IMAGE_ACQUIRE_TIMEOUT_SECONDS': timeout,
        '_download_image': download,
    }
    _exec_nodes([_node(tree, '_ImageAcquireTimeout')], globals_dict)
    _exec_nodes([_node(tree, '_acquire_gallery_image')], globals_dict)
    return globals_dict


class ImageAcquireTimeoutTests(unittest.TestCase):
    def test_timeout_raises_a_runtime_error_so_fallback_branches_still_catch_it(self) -> None:
        tree = ast.parse(SENDERS.read_text(encoding='utf-8'))
        source = ast.unparse(_node(tree, '_ImageAcquireTimeout'))
        self.assertIn('RuntimeError', source, '超时异常必须继承 RuntimeError，否则回退本地图的分支接不住')

    def test_command_gives_up_waiting_but_the_download_keeps_warming_the_cache(self) -> None:
        """超时只放弃「等待」，底层下载任务必须继续跑完并落盘。"""
        finished: list[str] = []

        async def inner(url: str) -> bytes:
            await asyncio.sleep(0.25)
            finished.append(f'downloaded:{url}')
            return b'image-bytes'

        async def download(url: str) -> bytes:
            # 与 gallery._download_image 同构：内部是独立 Task，取消等待者不会取消它
            return await asyncio.ensure_future(inner(url))

        async def run() -> None:
            helpers = _load_helpers(0.05, download)
            acquire = helpers['_acquire_gallery_image']
            timeout_error = helpers['_ImageAcquireTimeout']

            with self.assertRaises(timeout_error):
                await acquire('https://gallery.test/a.png')

            # 命令协程已经返回，底层下载仍在后台继续
            self.assertEqual(finished, [])
            await asyncio.sleep(0.35)
            self.assertEqual(finished, ['downloaded:https://gallery.test/a.png'])

        asyncio.run(run())

    def test_fast_download_returns_bytes_without_timing_out(self) -> None:
        async def download(url: str) -> bytes:
            return b'fast'

        async def run() -> None:
            helpers = _load_helpers(1.0, download)
            data = await helpers['_acquire_gallery_image']('https://gallery.test/b.png')
            self.assertEqual(data, b'fast')

        asyncio.run(run())


class SenderWiringTests(unittest.TestCase):
    def test_role_and_loli_senders_use_the_bounded_acquisition(self) -> None:
        source = SENDERS.read_text(encoding='utf-8')
        role_fn = source[
            source.index('async def _deliver_role_image('):source.index('async def _deliver_daily_result_image(')
        ]
        loli_fn = source[
            source.index('async def _deliver_loli_result_image('):source.index('_deliver_shota_result_image = ')
        ]

        # 超时取消不能顺着 await 传下去掐断下载，必须用 shield 保护
        acquire_fn = source[
            source.index('async def _acquire_gallery_image('):source.index('async def _deliver_role_image(')
        ]
        self.assertIn('asyncio.shield(_download_image(image_url))', acquire_fn)

        # 命令路径上不允许再直接 await 无上限的下载
        self.assertIn('image: bytes = await _acquire_gallery_image(image_url)', role_fn)
        self.assertIn('image_ref = await _acquire_gallery_image(image)', loli_fn)
        self.assertNotIn('await _download_image(', role_fn)
        self.assertNotIn('await _download_image(', loli_fn)

    def test_acquire_timeout_is_a_small_bounded_value(self) -> None:
        constants = (ROOT / 'TodayWaifu' / 'constants.py').read_text(encoding='utf-8')
        self.assertIn('IMAGE_ACQUIRE_TIMEOUT_SECONDS', constants)
        tree = ast.parse(constants)
        value = next(
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == 'IMAGE_ACQUIRE_TIMEOUT_SECONDS'
        )
        self.assertLessEqual(value, 10.0, '超时上限必须足够小，否则命令额度还是会被长期占用')
        self.assertGreater(value, 0.0)


if __name__ == '__main__':
    unittest.main()
