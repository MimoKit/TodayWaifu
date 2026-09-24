import ast
import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def _migration_module() -> ast.Module:
    tree = ast.parse((ROOT / 'daily_wife_config.py').read_text(encoding='utf-8'))
    start = next(
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.operand, ast.Call)
        and isinstance(node.test.operand.func, ast.Attribute)
        and node.test.operand.func.attr == 'is_file'
    )
    end = next(
        index
        for index, node in enumerate(tree.body[start:], start)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'DailyWifeShowConfig' for target in node.targets)
    )
    return ast.Module(body=tree.body[start:end], type_ignores=[])


class ConfigMigrationTests(unittest.TestCase):
    def test_forced_remote_urls_are_current(self) -> None:
        tree = ast.parse((ROOT / 'daily_wife_config.py').read_text(encoding='utf-8'))
        forced_urls = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == '_FORCED_REMOTE_URLS'
                for target in node.targets
            ):
                forced_urls = ast.literal_eval(node.value)
                break

        self.assertEqual(
            forced_urls,
            {
                'DailyWifeApiUrl': 'https://twfapi.xlinxc.cn',
            },
        )

    def test_first_start_overwrites_empty_and_custom_values_once(self) -> None:
        migration = _migration_module()
        with TemporaryDirectory() as directory:
            marker = Path(directory) / '.remote_urls_v3_migrated'
            config = SimpleNamespace(
                config={
                    'DailyWifeApiUrl': SimpleNamespace(data='https://custom.example.test/gallery'),
                },
                write_count=0,
            )

            def write_config() -> None:
                config.write_count += 1

            config.write_config = write_config
            namespace = {
                'CONFIG_PATH': marker.parent / 'config.json',
                'DailyWifeConfig': config,
                '_FORCED_URL_MIGRATION_MARKER': marker,
                '_FORCED_REMOTE_URLS': {
                    'DailyWifeApiUrl': 'https://twfapi.xlinxc.cn',
                },
                '_LEGACY_KEYS_TO_REMOVE': [
                    'DailyWifeGalleryApiUrl',
                    'DailyWifeNormalGalleryApiUrl',
                    'DailyWifeLoliApiUrl',
                    'DailyShotaGalleryApiUrl',
                    'DailyWifePgrGalleryApiUrl',
                    'DailyWifeRandomGalleryApiUrl',
                ],
            }
            code = compile(migration, '<config-migration>', 'exec')
            exec(code, namespace)

            self.assertEqual(
                config.config['DailyWifeApiUrl'].data,
                'https://twfapi.xlinxc.cn',
            )
            self.assertEqual(config.write_count, 1)
            self.assertTrue(marker.is_file())

            config.config['DailyWifeApiUrl'].data = 'https://later.example.test/gallery'
            exec(code, namespace)

            self.assertEqual(
                config.config['DailyWifeApiUrl'].data,
                'https://later.example.test/gallery',
            )
            self.assertEqual(config.write_count, 1)


if __name__ == '__main__':
    unittest.main()
