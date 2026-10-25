"""指定老婆（今日老婆 角色名）的放行条件必须遵循服务卡权限。

控制台把「今日老婆-指定老婆」的权限设为群主/群管理后，达到该权限的用户应可直接
指定角色；业务层若仍只认主人与白名单，服务卡权限形同虚设。
"""

import ast
import unittest
import importlib.util
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_PATH = ROOT / 'TodayWaifu' / 'shared.py'
UPLOAD_ACCESS_PATH = ROOT / 'TodayWaifu' / 'upload_access.py'


def _load_upload_access():
    spec = importlib.util.spec_from_file_location('todaywaifu_specify_upload_access', UPLOAD_ACCESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load upload_access module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_specify_gate(required_pm: int, whitelist):
    """抽出 _can_specify_wife 单独执行，依赖真实的权限比较函数。"""
    tree = ast.parse(SHARED_PATH.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_can_specify_wife'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        '_is_master': lambda ev: str(ev.user_id) == '10000',
        'can_use_pm_or_whitelisted_feature': _load_upload_access().can_use_pm_or_whitelisted_feature,
        'specify_wife_sv': SimpleNamespace(pm=required_pm),
        '_cfg': lambda key: whitelist,
    }
    exec(compile(module, str(SHARED_PATH), 'exec'), namespace)  # noqa: S102
    return namespace['_can_specify_wife']


def _event(user_id: str, user_pm: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, user_pm=user_pm)


class SpecifyWifeAccessTests(unittest.TestCase):
    def test_master_passes_regardless_of_permission_level(self) -> None:
        gate = _load_specify_gate(required_pm=3, whitelist=[])
        self.assertTrue(gate(_event('10000', 6)))

    def test_group_admin_passes_when_service_requires_group_admin(self) -> None:
        gate = _load_specify_gate(required_pm=3, whitelist=[])
        self.assertTrue(gate(_event('20001', 3)))

    def test_group_owner_passes_when_service_requires_group_admin(self) -> None:
        gate = _load_specify_gate(required_pm=3, whitelist=[])
        self.assertTrue(gate(_event('20002', 2)))

    def test_normal_member_is_rejected_when_service_requires_group_admin(self) -> None:
        gate = _load_specify_gate(required_pm=3, whitelist=[])
        self.assertFalse(gate(_event('30001', 6)))

    def test_whitelisted_normal_member_still_passes(self) -> None:
        gate = _load_specify_gate(required_pm=3, whitelist=['30001'])
        self.assertTrue(gate(_event('30001', 6)))

    def test_gate_honors_service_card_permission(self) -> None:
        gate = _load_specify_gate(required_pm=6, whitelist=[])
        self.assertTrue(gate(_event('30001', 6)))


if __name__ == '__main__':
    unittest.main()
