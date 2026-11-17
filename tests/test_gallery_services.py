"""Gallery startup retains disk-cache hints without scheduling image downloads.

Extract the lifecycle functions to avoid importing the GsCore host, following
other source-isolated unit tests in this suite. Keep decorators and relative
imports active so registration and the moved cache-restoration imports are tested.
"""

import ast
import sys
import types
import asyncio
import unittest
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
PACKAGE = 'todaywaifu_gallery_services_test'


class GalleryServicesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(PLUGIN)]
        paths = types.ModuleType(f'{PACKAGE}.paths')
        self.cache_root = Path('/unused/gallery-cache')
        paths._gallery_image_cache_root = Mock(return_value=self.cache_root)
        self.paths = paths
        spec = importlib.util.spec_from_file_location(f'{PACKAGE}.file_cache', PLUGIN / 'file_cache.py')
        if spec is None or spec.loader is None:
            raise RuntimeError('cannot load file_cache')
        self.file_cache = importlib.util.module_from_spec(spec)
        self.modules = patch.dict(sys.modules, {
            PACKAGE: package,
            f'{PACKAGE}.paths': paths,
            spec.name: self.file_cache,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec.loader.exec_module(self.file_cache)

        self.startup_hooks = []
        self.shutdown_hooks = []

        def on_start(*, priority):
            def register(function):
                self.startup_hooks.append((priority, function))
                return function
            return register

        def on_shutdown(function):
            self.shutdown_hooks.append(function)
            return function

        self.blocking = AsyncMock(return_value=3)
        self.start_workers = Mock()
        self.stop_workers = AsyncMock()
        self.logger = Mock()
        self.namespace = {
            '__package__': PACKAGE,
            'asyncio': asyncio,
            'run_blocking': self.blocking,
            'logger': self.logger,
            'LOG_PREFIX': '[test]',
            'on_core_start_before': on_start,
            'on_core_shutdown': on_shutdown,
            'start_image_delivery_workers': self.start_workers,
            'stop_image_delivery_workers': self.stop_workers,
        }
        names = {
            'restore_cached_url_index',
            '_start_gallery_services_on_startup',
            '_stop_gallery_services_on_shutdown',
        }
        tree = ast.parse((PLUGIN / 'shared.py').read_text(encoding='utf-8'))
        functions = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
        self.assertEqual({node.name for node in functions}, names)
        module = ast.Module(body=functions, type_ignores=[])
        exec(compile(module, str(PLUGIN / 'shared.py'), 'exec'), self.namespace)
        self.restore = self.namespace['restore_cached_url_index']
        self.start = self.namespace['_start_gallery_services_on_startup']
        self.stop = self.namespace['_stop_gallery_services_on_shutdown']

    async def test_restore_delegates_scan_to_blocking_executor_and_returns_count(self) -> None:
        self.assertEqual(await self.restore(), 3)
        self.paths._gallery_image_cache_root.assert_called_once_with()
        self.blocking.assert_awaited_once_with(self.file_cache.rebuild_cached_url_index, self.cache_root)
        self.logger.info.assert_called_once()
        self.assertIn('3', self.logger.info.call_args.args[0])
        self.logger.warning.assert_not_called()

    async def test_empty_index_is_returned_without_success_log(self) -> None:
        self.blocking.return_value = 0
        self.assertEqual(await self.restore(), 0)
        self.logger.info.assert_not_called()
        self.logger.warning.assert_not_called()

    async def test_scan_oserror_is_logged_and_degrades_to_zero(self) -> None:
        self.blocking.side_effect = PermissionError('cache denied')
        self.assertEqual(await self.restore(), 0)
        self.logger.warning.assert_called_once()
        self.assertIn('cache denied', self.logger.warning.call_args.args[0])
        self.logger.info.assert_not_called()

    async def test_cache_root_oserror_is_also_handled(self) -> None:
        self.paths._gallery_image_cache_root.side_effect = OSError('root unavailable')
        self.assertEqual(await self.restore(), 0)
        self.blocking.assert_not_awaited()
        self.logger.warning.assert_called_once()

    async def test_restore_does_not_swallow_programming_errors(self) -> None:
        self.blocking.side_effect = RuntimeError('executor unavailable')
        with self.assertRaisesRegex(RuntimeError, 'executor unavailable'):
            await self.restore()
        self.logger.warning.assert_not_called()

    async def test_restore_propagates_cancellation(self) -> None:
        self.blocking.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.restore()
        self.logger.warning.assert_not_called()

    async def test_restart_restores_cached_preference_from_filenames_without_reading_images(self) -> None:
        cached = 'https://gallery.test/cached.png?token=old'
        rotated = 'https://gallery.test/cached.png?token=new'
        uncached = 'https://gallery.test/uncached.png'
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.file_cache.write_url_cache(root, cached, b'image bytes')
            (root / 'shrink_thumbnail').write_bytes(b'thumbnail')
            (root / 'unrelated.txt').write_text('ignored', encoding='utf-8')
            (root / ('a' * 64)).mkdir()
            self.file_cache.clear_cached_url_index()
            self.assertEqual(self.file_cache.prefer_cached_urls((uncached, rotated)), (uncached, rotated))
            self.paths._gallery_image_cache_root.return_value = root

            async def run_blocking(function, *args):
                return function(*args)

            self.blocking.side_effect = run_blocking
            with (
                patch.object(Path, 'read_bytes', side_effect=AssertionError('must not read image payload')),
                patch.object(Path, 'write_bytes', side_effect=AssertionError('must not download images')),
            ):
                self.assertEqual(await self.restore(), 1)
            self.assertEqual(self.file_cache.prefer_cached_urls((uncached, rotated)), (rotated,))
            self.assertEqual(self.file_cache.cached_url_count(), 1)

    async def test_missing_cache_directory_is_a_valid_cold_start(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'not-created'
            self.paths._gallery_image_cache_root.return_value = root

            async def run_blocking(function, *args):
                return function(*args)

            self.blocking.side_effect = run_blocking
            self.assertEqual(await self.restore(), 0)
            urls = ('https://gallery.test/a.png', 'https://gallery.test/b.png')
            self.assertEqual(self.file_cache.prefer_cached_urls(urls), urls)
            self.assertFalse(root.exists())

    async def test_startup_hooks_remain_registered_with_the_core(self) -> None:
        self.assertEqual(self.startup_hooks, [(-60, self.start)])
        self.assertEqual(self.shutdown_hooks, [self.stop])

    async def test_startup_starts_workers_before_awaiting_index_restoration(self) -> None:
        events = []
        self.start_workers.side_effect = lambda: events.append('workers')

        async def restore():
            events.append('restore')
            await asyncio.sleep(0)
            events.append('restored')
            return 2

        self.namespace['restore_cached_url_index'] = AsyncMock(side_effect=restore)
        with patch.object(asyncio, 'create_task', side_effect=AssertionError('unexpected background task')):
            await self.start()
        self.assertEqual(events, ['workers', 'restore', 'restored'])
        self.start_workers.assert_called_once_with()
        self.namespace['restore_cached_url_index'].assert_awaited_once_with()

    async def test_startup_survives_unreadable_cache_without_scheduling_downloads(self) -> None:
        self.blocking.side_effect = OSError('unreadable cache')
        with patch.object(asyncio, 'create_task', side_effect=AssertionError('unexpected background task')):
            await self.start()
        self.start_workers.assert_called_once_with()
        self.blocking.assert_awaited_once()
        self.logger.warning.assert_called_once()

    async def test_shutdown_waits_for_delivery_workers_to_stop(self) -> None:
        events = []

        async def stop_workers():
            await asyncio.sleep(0)
            events.append('stopped')

        self.stop_workers.side_effect = stop_workers
        await self.stop()
        self.stop_workers.assert_awaited_once_with()
        self.assertEqual(events, ['stopped'])
        self.blocking.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
