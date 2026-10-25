"""分配老公：命令入口、权限门与记录桶必须与分配老婆对称。

历史上有分配老婆但没有分配老公；该能力在 2026-09-30 补入，后因上游同步丢失。
本文件把该能力的入口、权限与落库位置固定下来，避免再次被同步覆盖后无人发现。
"""

import ast
import json
import unittest
import importlib.util
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_PATH = ROOT / 'TodayWaifu' / 'shared.py'
DAILY_PATH = ROOT / 'TodayWaifu' / 'daily.py'
UPLOAD_ACCESS_PATH = ROOT / 'TodayWaifu' / 'upload_access.py'
HELP_PATH = ROOT / 'help.json'


def _source(path: Path) -> str:
    return path.read_text(encoding='utf-8-sig')


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(_source(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f'{path.name} 里没有 {name}')


def _load_upload_access():
    spec = importlib.util.spec_from_file_location('todaywaifu_husband_upload_access', UPLOAD_ACCESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load upload_access module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_assign_husband_gate(required_pm: int, whitelist):
    tree = ast.parse(_source(SHARED_PATH))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_can_assign_husband'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        '_is_master': lambda ev: str(ev.user_id) == '10000',
        'can_use_pm_or_whitelisted_feature': _load_upload_access().can_use_pm_or_whitelisted_feature,
        'assign_husband_sv': SimpleNamespace(pm=required_pm),
        '_cfg': lambda key: whitelist,
    }
    exec(compile(module, str(SHARED_PATH), 'exec'), namespace)  # noqa: S102
    return namespace['_can_assign_husband']


def _event(user_id: str, user_pm: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, user_pm=user_pm)


class AssignHusbandTests(unittest.TestCase):
    def test_husband_assign_service_is_registered_separately(self) -> None:
        self.assertIn(
            "assign_husband_sv = SV('今日老婆-分配老公', pm=3, priority=2)",
            _source(SHARED_PATH),
        )

    def test_group_admin_passes_assign_husband_gate(self) -> None:
        gate = _load_assign_husband_gate(required_pm=3, whitelist=[])
        self.assertTrue(gate(_event('20001', 3)))

    def test_normal_member_is_rejected_by_assign_husband_gate(self) -> None:
        gate = _load_assign_husband_gate(required_pm=3, whitelist=[])
        self.assertFalse(gate(_event('30001', 6)))

    def test_whitelisted_member_passes_assign_husband_gate(self) -> None:
        gate = _load_assign_husband_gate(required_pm=3, whitelist=['30001'])
        self.assertTrue(gate(_event('30001', 6)))

    def test_handler_reads_husband_candidates_and_writes_husbands_bucket(self) -> None:
        source = _function_source(DAILY_PATH, '_send_assign_husband')
        self.assertIn('_can_assign_husband(ev)', source)
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
