import ast
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


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
                'DailyWifeGalleryApiUrl': 'https://img.mimokit.dpdns.org/api/xwuid/roles',
                'DailyWifeLoliApiUrl': 'https://loli.mimokit.dpdns.org',
            },
        )

    def test_first_start_overwrites_empty_and_custom_values_once(self) -> None:
        migration = _migration_module()
        with TemporaryDirectory() as directory:
            marker = Path(directory) / '.remote_urls_v2_migrated'
            config = SimpleNamespace(
                config={
                    'DailyWifeGalleryApiUrl': SimpleNamespace(data='https://custom.example.test/gallery'),
                    'DailyWifeLoliApiUrl': SimpleNamespace(data=''),
                },
                write_count=0,
            )

            def write_config() -> None:
                config.write_count += 1

            config.write_config = write_config
            namespace = {
                'DailyWifeConfig': config,
                '_FORCED_URL_MIGRATION_MARKER': marker,
                '_FORCED_REMOTE_URLS': {
                    'DailyWifeGalleryApiUrl': 'https://img.mimokit.dpdns.org/api/xwuid/roles',
                    'DailyWifeLoliApiUrl': 'https://loli.mimokit.dpdns.org',
                },
            }
            code = compile(migration, '<config-migration>', 'exec')
            exec(code, namespace)

            self.assertEqual(
                config.config['DailyWifeGalleryApiUrl'].data,
                'https://img.mimokit.dpdns.org/api/xwuid/roles',
            )
            self.assertEqual(
                config.config['DailyWifeLoliApiUrl'].data,
                'https://loli.mimokit.dpdns.org',
            )
            self.assertEqual(config.write_count, 1)
            self.assertTrue(marker.is_file())

            config.config['DailyWifeGalleryApiUrl'].data = 'https://later.example.test/gallery'
            config.config['DailyWifeLoliApiUrl'].data = ''
            exec(code, namespace)

            self.assertEqual(
                config.config['DailyWifeGalleryApiUrl'].data,
                'https://later.example.test/gallery',
            )
            self.assertEqual(config.config['DailyWifeLoliApiUrl'].data, '')
            self.assertEqual(config.write_count, 1)


if __name__ == '__main__':
    unittest.main()
