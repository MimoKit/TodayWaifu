import ast
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _load_status_helpers() -> dict[str, Any]:
    status_path = ROOT / 'twf' / 'status.py'
    tree = ast.parse(status_path.read_text(encoding='utf-8-sig'))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {'_is_countable_daily_record', '_daily_record_count'}
    ]
    module = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {'Any': Any}
    exec(compile(module, str(status_path), 'exec'), globals_dict)
    return globals_dict


class StatusSourceTests(unittest.TestCase):
    def test_status_module_is_loaded_by_plugin_entry(self) -> None:
        source = (ROOT / '__init__.py').read_text(encoding='utf-8-sig')
        self.assertIn('from .twf import status', source)

    def test_status_registers_three_daily_metrics(self) -> None:
        source = (ROOT / 'twf' / 'status.py').read_text(encoding='utf-8-sig')
        self.assertIn('register_status(', source)
        self.assertIn("'今日老婆': get_today_wife_count", source)
        self.assertIn("'今日萝莉': get_today_loli_count", source)
        self.assertIn("'今日老公': get_today_husband_count", source)
        self.assertIn("days.get(_today_key())", source)

    def test_daily_record_count_counts_today_original_records(self) -> None:
        helpers = _load_status_helpers()
        count = helpers['_daily_record_count']

        day_data = {
            'qqgroup:g1': {
                'wives': {
                    'u1': {'name': '今汐'},
                    'u2': {'name': '长离', 'stolen_by': 'u3'},
                    'u3': {'name': '长离', 'stolen_from': 'u2'},
                    'u4': {'name': '吟霖', 'gifted_from': 'u5'},
                    'u5': {'name': ''},
                },
                'lolis': {'u1': {'name': '萝莉图abc'}},
                'husbands': {'u1': {'name': '忌炎'}},
            },
            'qqgroup:g2': {
                'wives': {'u6': {'name': '珂莱塔', 'divorced': True}},
                'lolis': {'u2': {'name': '萝莉图def', 'safe': True}},
            },
        }

        self.assertEqual(count(day_data, 'wives'), 3)
        self.assertEqual(count(day_data, 'lolis'), 1)
        self.assertEqual(count(day_data, 'husbands'), 1)


if __name__ == '__main__':
    unittest.main()
