import ast
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _extract_function(path: Path, name: str, globals_dict: dict[str, Any]):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), globals_dict)
    return globals_dict[name]


class NteRosterTests(unittest.TestCase):
    def test_builtin_nte_map_contains_all_current_non_protagonist_women(self) -> None:
        names = {
            line.split('：', 1)[1].strip()
            for line in (ROOT / 'nte_role_id_map.txt').read_text(encoding='utf-8-sig').splitlines()
            if '：' in line
        }
        self.assertEqual(
            names,
            {
                '早雾',
                '安魂曲',
                '娜娜莉',
                '薄荷',
                '哈尼娅',
                '哈索尔',
                '法帝娅',
                '浔',
                '达芙蒂尔',
                '九原',
                '海月',
                '小吱',
                '伊洛伊',
                '真红',
            },
        )

    def test_nte_filter_rejects_men_and_both_protagonists(self) -> None:
        shared_path = ROOT / 'twf' / 'shared.py'
        is_excluded = _extract_function(
            shared_path,
            '_is_excluded_nte_role',
            {
                '_normalize_role_name': lambda name: name.replace('・', '·').strip(),
                'NTE_EXCLUDED_ROLE_NAMES': {
                    '翳',
                    '埃德嘉',
                    '白藏',
                    '阿德勒',
                    '卡厄斯',
                },
                'NTE_EXCLUDED_ROLE_KEYWORDS': (
                    '异能者·零',
                    '异能者零',
                    '男主',
                    '女主',
                ),
            },
        )

        for name in ('翳', '埃德嘉', '白藏', '阿德勒', '卡厄斯'):
            self.assertTrue(is_excluded(name), name)
        for name in ('异能者·零(男)', '异能者·零(女)', '男主', '女主'):
            self.assertTrue(is_excluded(name), name)
        for name in ('早雾', '安魂曲', '伊洛伊', '真红'):
            self.assertFalse(is_excluded(name), name)

        source = shared_path.read_text(encoding='utf-8-sig')
        self.assertIn('if not _is_excluded_nte_role(role_name)', source)


class UnifiedDivorceTests(unittest.TestCase):
    def _mark_all(self):
        bucket_names = {
            'wife': 'wives',
            'nte': 'nte_wives',
            'pgr': 'pgr_wives',
            'husband': 'husbands',
            'loli': 'lolis',
        }
        return _extract_function(
            ROOT / 'twf' / 'shared.py',
            '_mark_all_daily_records_divorced',
            {
                'Any': Any,
                'ALL_DAILY_RECORD_KINDS': tuple(bucket_names),
                '_daily_bucket_name': bucket_names.__getitem__,
            },
        )

    def test_unified_divorce_marks_every_mode_and_only_current_user(self) -> None:
        context = {
            'wives': {'u1': {'name': '今汐'}, 'u2': {'name': '长离'}},
            'nte_wives': {'u1': {'name': '早雾'}},
            'pgr_wives': {'u1': {'name': '露西亚'}},
            'husbands': {'u1': {'name': '忌炎'}},
            'lolis': {'u1': {'name': '萝莉'}},
            'safe_wives': {'u1': {'name': '珂莱塔', 'safe': True}},
        }

        divorced = self._mark_all()(context, 'u1', 123456)

        self.assertEqual(len(divorced), 6)
        for bucket in (
            'wives',
            'nte_wives',
            'pgr_wives',
            'husbands',
            'lolis',
            'safe_wives',
        ):
            self.assertTrue(context[bucket]['u1']['divorced'])
            self.assertEqual(context[bucket]['u1']['divorced_at'], 123456)
        self.assertNotIn('divorced', context['wives']['u2'])

    def test_unified_divorce_is_idempotent(self) -> None:
        context = {
            'wives': {'u1': {'name': '今汐'}},
            'nte_wives': {},
            'pgr_wives': {},
            'husbands': {},
            'lolis': {},
            'safe_wives': {},
        }
        mark_all = self._mark_all()

        self.assertEqual(mark_all(context, 'u1', 100), [('wife', '今汐')])
        self.assertEqual(mark_all(context, 'u1', 200), [])
        self.assertEqual(context['wives']['u1']['divorced_at'], 100)

    def test_divorced_state_takes_precedence_over_old_transfer_flags(self) -> None:
        wife_state = _extract_function(
            ROOT / 'twf' / 'shared.py',
            '_wife_state',
            {'Any': Any},
        )

        self.assertEqual(
            wife_state(
                {
                    'name': '露西亚',
                    'stolen_by': 'u2',
                    'gifted_to': 'u3',
                    'divorced': True,
                }
            ),
            'divorced',
        )

    def test_loli_divorce_result_hides_internal_image_id(self) -> None:
        result_name = _extract_function(
            ROOT / 'twf' / 'divorce.py',
            '_divorce_result_name',
            {},
        )

        self.assertEqual(result_name('loli', '萝莉图b1b06da1'), '今日萝莉')
        self.assertEqual(result_name('pgr', '露西亚'), '露西亚')


if __name__ == '__main__':
    unittest.main()
