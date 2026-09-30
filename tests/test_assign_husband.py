import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding='utf-8-sig')


def _function_source(relative: str, name: str) -> str:
    tree = ast.parse(_source(relative))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f'{relative} 里没有 {name}')


class AssignHusbandTests(unittest.TestCase):
    def test_husband_assign_service_is_registered_separately(self) -> None:
        source = _source('TodayWaifu/shared.py')
        self.assertIn("assign_husband_sv = SV('今日老婆-分配老公', pm=3, priority=2)", source)

    def test_husband_assign_gate_reuses_assign_whitelist_and_service_permission(self) -> None:
        source = _function_source('TodayWaifu/shared.py', '_can_assign_husband')
        self.assertIn('_is_master(ev)', source)
        self.assertIn('ev.user_pm', source)
        self.assertIn('assign_husband_sv.pm', source)
        self.assertIn('DailyWifeAssignWhitelist', source)

    def test_husband_assign_handler_uses_husband_candidates_and_bucket(self) -> None:
        source = _function_source('TodayWaifu/daily.py', '_send_assign_husband')
        self.assertIn('_can_assign_husband(ev)', source)
        self.assertIn("_load_candidates('husband')", source)
        self.assertIn("_filter_by_mode(candidates, 'husband')", source)
        self.assertIn("('husbands', target_key, assigned_record)", source)
        self.assertIn("kind='husband'", source)

    def test_assignment_parser_removes_husband_command_words(self) -> None:
        source = _function_source('TodayWaifu/daily.py', '_assignment_role_name')
        self.assertIn("'分配老公'", source)
        self.assertIn("'分配今日老公'", source)
        self.assertIn("'老公'", source)

    def test_husband_assign_commands_have_prefix_and_usage_handlers(self) -> None:
        source = _source('TodayWaifu/daily.py')
        self.assertRegex(source, r"@assign_husband_sv\.on_prefix\(\s*\('分配老公', '分配今日老公'\)")
        self.assertRegex(source, r"@assign_husband_sv\.on_fullmatch\(\s*\('分配老公', '分配今日老公'\)")


if __name__ == '__main__':
    unittest.main()
