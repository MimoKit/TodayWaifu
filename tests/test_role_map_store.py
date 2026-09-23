import sys
import json
import tempfile
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_store():
    path = ROOT / 'TodayWaifu' / 'role_map_store.py'
    spec = importlib.util.spec_from_file_location('todaywaifu_role_map_store', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load role map store module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


store = _load_store()


class BuiltinRoleMapTests(unittest.TestCase):
    def test_builtin_json_carries_all_three_sections(self) -> None:
        payload = json.loads((ROOT / 'role_id_map.json').read_text(encoding='utf-8'))
        self.assertEqual(payload['version'], 1)
        for section in store.ROLE_MAP_SECTIONS:
            self.assertIsInstance(payload[section], dict, section)
            self.assertTrue(payload[section], section)

    def test_builtin_json_replaces_the_removed_txt_files(self) -> None:
        for legacy in ('wife_role_id_map.txt', 'husband_role_id_map.txt', 'nte_role_id_map.txt'):
            self.assertFalse((ROOT / legacy).exists(), legacy)

    def test_each_section_is_readable_by_mode(self) -> None:
        text = (ROOT / 'role_id_map.json').read_text(encoding='utf-8')
        wife = store.loads_role_map(text, 'wife')
        husband = store.loads_role_map(text, 'husband')
        nte = store.loads_role_map(text, 'nte')
        self.assertEqual(wife['1102'], '散华')
        self.assertEqual(husband['1104'], '凌阳')
        self.assertEqual(nte['1003'], '早雾')
        self.assertNotIn('1102', husband)
        self.assertNotIn('1104', wife)

    def test_unknown_section_does_not_fall_back_to_whole_file(self) -> None:
        text = (ROOT / 'role_id_map.json').read_text(encoding='utf-8')
        self.assertEqual(store.loads_role_map(text, 'nonexistent'), {})


class LegacyTextCompatTests(unittest.TestCase):
    def test_legacy_txt_still_parses_with_both_colon_styles(self) -> None:
        parsed = store.loads_role_map('1102：散华\n1610: 秧秧·玄翎\n# 注释行\n\n')
        self.assertEqual(parsed, {'1102': '散华', '1610': '秧秧·玄翎'})

    def test_flat_json_is_used_when_no_section_is_requested(self) -> None:
        self.assertEqual(store.loads_role_map('{"900001": "达妮娅"}'), {'900001': '达妮娅'})

    def test_sectioned_json_requested_flat_keeps_only_flat_entries(self) -> None:
        # 自定义老婆对照表是扁平结构；误配到分节文件上时不应把节名当角色名
        parsed = store.loads_role_map('{"version": 1, "wife": {"1102": "散华"}}')
        self.assertEqual(parsed, {})

    def test_broken_json_returns_empty_instead_of_raising(self) -> None:
        self.assertEqual(store.loads_role_map('{not json'), {})
        self.assertEqual(store.loads_role_map(''), {})


class WriteAndMigrationTests(unittest.TestCase):
    def test_dumps_sorts_numeric_ids_and_round_trips(self) -> None:
        payload = store.dumps_role_map({'1000': '乙', '900': '甲', 'custom': '丙'})
        self.assertLess(payload.index('"900"'), payload.index('"1000"'))
        self.assertLess(payload.index('"1000"'), payload.index('"custom"'))
        self.assertEqual(store.loads_role_map(payload), {'900': '甲', '1000': '乙', 'custom': '丙'})

    def test_write_role_map_is_atomic_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'nested' / 'custom_role_map.json'
            store.write_role_map(path, {'900001': '达妮娅'})
            self.assertEqual(store.loads_role_map(path.read_text(encoding='utf-8')), {'900001': '达妮娅'})
            self.assertEqual([item.name for item in path.parent.iterdir()], ['custom_role_map.json'])

    def test_legacy_txt_is_migrated_once_and_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / 'custom_role_map.txt'
            js = Path(tmp) / 'custom_role_map.json'
            txt.write_text('900001：达妮娅\n900002：今汐\n', encoding='utf-8')

            self.assertTrue(store.migrate_legacy_text_map(txt, js))
            self.assertFalse(txt.exists())
            self.assertTrue((Path(tmp) / 'custom_role_map.txt.migrated.bak').is_file())
            self.assertEqual(
                store.loads_role_map(js.read_text(encoding='utf-8')),
                {'900001': '达妮娅', '900002': '今汐'},
            )
            # 幂等：JSON 已存在时不再迁移
            self.assertFalse(store.migrate_legacy_text_map(txt, js))

    def test_migration_is_skipped_when_no_legacy_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(
                store.migrate_legacy_text_map(Path(tmp) / 'missing.txt', Path(tmp) / 'out.json')
            )
            self.assertFalse((Path(tmp) / 'out.json').exists())


if __name__ == '__main__':
    unittest.main()
