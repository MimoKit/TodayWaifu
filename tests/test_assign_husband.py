"""分配老公：命令入口、SV 默认权限与记录桶必须与分配老婆对称。"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_PATH = ROOT / 'TodayWaifu' / 'shared.py'
DAILY_PATH = ROOT / 'TodayWaifu' / 'daily.py'
HELP_PATH = ROOT / 'help.json'


class _FakeSV:
    """复刻 GsCore `SV` 默认 `pm=6` 的构造语义，防止桩对象掩盖默认权限。"""

    def __init__(self, name: str = '', pm: int = 6, priority: int = 5, **kwargs: object) -> None:
        self.name = name
        self.pm = pm
        self.priority = priority


def _source(path: Path) -> str:
    return path.read_text(encoding='utf-8-sig')


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f'{path.name} 里没有 {name}')


def _load_assign_husband_sv() -> _FakeSV:
    tree = ast.parse(_source(SHARED_PATH))
    sv_assign = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'assign_husband_sv' for t in node.targets)
    )
    module = ast.Module(body=[sv_assign], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {'SV': _FakeSV}
    exec(compile(module, str(SHARED_PATH), 'exec'), namespace)  # noqa: S102
    sv = namespace['assign_husband_sv']
    assert isinstance(sv, _FakeSV)
    return sv


class AssignHusbandTests(unittest.TestCase):
    def test_husband_assign_service_defaults_to_pm1(self) -> None:
        sv = _load_assign_husband_sv()
        self.assertEqual(sv.name, '今日老婆-分配老公')
        self.assertEqual(sv.pm, 1)
        self.assertEqual(sv.priority, 2)

    def test_handler_reads_husband_candidates_and_writes_husbands_bucket(self) -> None:
        source = _function_source(DAILY_PATH, '_send_assign_husband')
        self.assertIn("_load_candidates('husband')", source)
        self.assertIn("_filter_by_mode(candidates, 'husband')", source)
        self.assertIn("('husbands', target_key, assigned_record)", source)
        self.assertIn("kind='husband'", source)

    def test_assignment_parser_removes_husband_command_words(self) -> None:
        source = _function_source(DAILY_PATH, '_assignment_role_name')
        self.assertIn("'分配老公'", source)
        self.assertIn("'分配今日老公'", source)
        self.assertIn("'老公'", source)

    def test_husband_assign_commands_have_prefix_and_usage_handlers(self) -> None:
        source = _source(DAILY_PATH)
        self.assertRegex(source, r"@assign_husband_sv\.on_prefix\(\s*\('分配老公', '分配今日老公'\)")
        self.assertRegex(source, r"@assign_husband_sv\.on_fullmatch\(\s*\('分配老公', '分配今日老公'\)")

    def test_help_lists_assign_husband(self) -> None:
        data = json.loads(_source(HELP_PATH))
        names = [item['name'] for group in data.values() for item in group['data']]
        self.assertIn('分配老公', names)


if __name__ == '__main__':
    unittest.main()
