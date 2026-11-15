"""指定老婆（今日老婆 角色名）的权限契约：仅机器人主人与指定老婆白名单可用。

`specify_wife_sv` 同时承载 `今日老婆 帮助`、`今日老婆 列表` 等前缀分支，且必须允许白名单内
的普通群员（user_pm=6）进入处理器，因此 SV 层不可锁 `pm`（锁 `pm` 会被框架提前挡掉）；
而未锁 `pm` 的 SV 在 GsCore 中默认 `pm=6`，业务门若拿 `ev.user_pm <= specify_wife_sv.pm`
比较就会退化成全员放行（即 dc5cc06 引入的线上缺陷）。
"""

from __future__ import annotations

import ast
import unittest
import importlib.util
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_PATH = ROOT / 'TodayWaifu' / 'shared.py'
UPLOAD_ACCESS_PATH = ROOT / 'TodayWaifu' / 'upload_access.py'


class _FakeSV:
    """复刻 GsCore `SV` 默认 `pm=6` 的构造语义，防止桩对象掩盖默认权限。"""

    def __init__(self, name: str = '', pm: int = 6, priority: int = 5, **kwargs: object) -> None:
        self.name = name
        self.pm = pm
        self.priority = priority
        self.kwargs = kwargs


def _load_upload_access():
    spec = importlib.util.spec_from_file_location('todaywaifu_specify_upload_access', UPLOAD_ACCESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load upload_access module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_specify_runtime(whitelist: object):
    """连同 `specify_wife_sv = SV(...)` 与 `_can_specify_wife` 一起从真实源码加载执行。"""
    tree = ast.parse(SHARED_PATH.read_text(encoding='utf-8-sig'))
    sv_assign = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == 'specify_wife_sv' for t in node.targets)
    )
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_can_specify_wife'
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
        '_cfg': lambda key: whitelist if key == 'DailyWifeSpecifyWhitelist' else [],
    }
    exec(compile(module, str(SHARED_PATH), 'exec'), namespace)  # noqa: S102
    return namespace['specify_wife_sv'], namespace['_can_specify_wife']


def _event(user_id: str, user_pm: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id, user_pm=user_pm)


class SpecifyWifeAccessTests(unittest.TestCase):
    def test_specify_sv_does_not_lock_pm_at_framework_layer(self) -> None:
        sv, _ = _load_specify_runtime(whitelist=[])
        self.assertEqual(sv.pm, 6, '指定老婆的 SV 不能锁 pm，否则白名单群员与列表/帮助子命令会被框架拦截')

    def test_master_passes_regardless_of_permission_level(self) -> None:
        _, gate = _load_specify_runtime(whitelist=[])
        self.assertTrue(gate(_event('10000', 6)))

    def test_whitelisted_normal_member_passes(self) -> None:
        _, gate = _load_specify_runtime(whitelist=['30001'])
        self.assertTrue(gate(_event('30001', 6)))

    def test_normal_member_is_rejected_by_default(self) -> None:
        _, gate = _load_specify_runtime(whitelist=[])
        self.assertFalse(gate(_event('30001', 6)))

    def test_unlisted_group_admin_and_owner_are_rejected(self) -> None:
        _, gate = _load_specify_runtime(whitelist=[])
        self.assertFalse(gate(_event('20001', 3)))
        self.assertFalse(gate(_event('20002', 2)))

    def test_specify_whitelist_config_keeps_default_empty(self) -> None:
        tree = ast.parse((ROOT / 'config_default.py').read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.value, ast.Dict):
                continue
            if not isinstance(node.target, ast.Name) or node.target.id != 'CONFIG_DEFAULT':
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and key.value == 'DailyWifeSpecifyWhitelist':
                    expr = ast.unparse(value)
                    self.assertIn('GsListStrConfig', expr)
                    self.assertTrue(expr.endswith('[])'), expr)
                    return
        raise AssertionError('CONFIG_DEFAULT 里没有 DailyWifeSpecifyWhitelist')


if __name__ == '__main__':
    unittest.main()
