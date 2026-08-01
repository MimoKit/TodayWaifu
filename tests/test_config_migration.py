import ast
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


class ConfigMigrationTests(unittest.TestCase):
    def test_legacy_default_urls_are_migrated(self) -> None:
        tree = ast.parse((ROOT / 'daily_wife_config.py').read_text(encoding='utf-8'))
        legacy_urls = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                if any(isinstance(target, ast.Name) and target.id == '_LEGACY_DEFAULT_URLS' for target in node.targets):
                    legacy_urls = ast.literal_eval(node.value)
                    break

        self.assertEqual(
            legacy_urls,
            {
                'DailyWifeGalleryApiUrl': (
                    'https://img.xlinxc.cn/api/xwuid/roles',
                    'https://img.mimokit.dpdns.org/api/xwuid/roles',
                ),
                'DailyWifeLoliApiUrl': (
                    'https://loli.xlinxc.cn',
                    'https://loli.mimokit.dpdns.org',
                ),
            },
        )

    def test_migration_only_replaces_exact_legacy_defaults(self) -> None:
        source = (ROOT / 'daily_wife_config.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        start = next(
            index
            for index, node in enumerate(tree.body)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == '_config_changed' for target in node.targets)
        )
        end = next(
            index
            for index, node in enumerate(tree.body[start:], start)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'DailyWifeShowConfig' for target in node.targets)
        )
        migration = ast.Module(body=tree.body[start:end], type_ignores=[])

        config = SimpleNamespace(
            config={
                'DailyWifeGalleryApiUrl': SimpleNamespace(
                    data='https://img.xlinxc.cn/api/xwuid/roles',
                ),
                'DailyWifeLoliApiUrl': SimpleNamespace(
                    data='https://custom.example.test/loli',
                ),
            },
            write_count=0,
        )

        def write_config() -> None:
            config.write_count += 1

        config.write_config = write_config
        namespace = {
            'DailyWifeConfig': config,
            '_LEGACY_DEFAULT_URLS': {
                'DailyWifeGalleryApiUrl': (
                    'https://img.xlinxc.cn/api/xwuid/roles',
                    'https://img.mimokit.dpdns.org/api/xwuid/roles',
                ),
                'DailyWifeLoliApiUrl': (
                    'https://loli.xlinxc.cn',
                    'https://loli.mimokit.dpdns.org',
                ),
            },
        }
        exec(compile(migration, '<config-migration>', 'exec'), namespace)

        self.assertEqual(
            config.config['DailyWifeGalleryApiUrl'].data,
            'https://img.mimokit.dpdns.org/api/xwuid/roles',
        )
        self.assertEqual(
            config.config['DailyWifeLoliApiUrl'].data,
            'https://custom.example.test/loli',
        )
        self.assertEqual(config.write_count, 1)


if __name__ == '__main__':
    unittest.main()
