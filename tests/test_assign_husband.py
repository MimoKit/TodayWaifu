"""分配老公：命令入口、权限门与记录桶必须与分配老婆对称。

SV 层与分配老婆同样不锁 `pm`，统一交由 `_can_assign_husband` 按「主人或分配白名单」判定，
否则白名单内的普通群员（user_pm=6）会在进入处理器前就被框架拦截。
"""

from __future__ import annotations

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


def _load_upload_access():
    spec = importlib.util.spec_from_file_location('todaywaifu_husband_upload_access', UPLOAD_ACCESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load upload_access module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_assign_husband_runtime(whitelist: object):
    tree = ast.parse(_source(SHARED_PATH))
    sv_assign = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'assign_husband_sv' for t in node.targets)
    )
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_can_assign_husband'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, sv_assign, function], type_ignores=[])
    ast.fix_missing_locations(module)
    access = _load_upload_access()
    namespace: dict[str, object] = {
        'SV': _FakeSV,
        '_is_master': lambda ev: str(ev.user_id) == '10000',
        'can_use_whitelisted_feature': access.can_use_whitelisted_feature,
        'normalized_user_ids': access.normalized_user_ids,
        '_cfg': lambda key: whitelist if key == 'DailyWifeAssignWhitelist' else [],
    }
    exec(compile(module, str(SHARED_PATH), 'exec'), namespace)  # noqa: S102
    return namespace['assign_husband_sv'], namespace['_can_assign_husband']


def _event(user_id: str, user_pm: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, user_pm=user_pm)


class AssignHusbandTests(unittest.TestCase):
    def test_husband_assign_service_does_not_lock_pm_at_framework_layer(self) -> None:
        sv, _ = _load_assign_husband_runtime(whitelist=[])
        self.assertEqual(sv.name, '今日老婆-分配老公')
        self.assertEqual(sv.priority, 2)
        self.assertEqual(sv.pm, 6, '分配老公的 SV 不能锁 pm，否则白名单普通群员会被框架提前拦截')

    def test_master_passes_assign_husband_gate(self) -> None:
        _, gate = _load_assign_husband_runtime(whitelist=[])
        self.assertTrue(gate(_event('10000', 6)))

    def test_whitelisted_member_passes_assign_husband_gate(self) -> None:
        _, gate = _load_assign_husband_runtime(whitelist=['30001'])
        self.assertTrue(gate(_event('30001', 6)))

    def test_unlisted_members_and_admins_are_rejected_by_assign_husband_gate(self) -> None:
        _, gate = _load_assign_husband_runtime(whitelist=[])
        self.assertFalse(gate(_event('30001', 6)))
        self.assertFalse(gate(_event('20001', 3)))

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
