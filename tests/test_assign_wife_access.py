import ast
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_ACCESS_PATH = ROOT / 'TodayWaifu' / 'upload_access.py'


def _load_upload_access():
    spec = importlib.util.spec_from_file_location('todaywaifu_assign_upload_access', UPLOAD_ACCESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load upload_access module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding='utf-8-sig')


def _function_source(relative: str, name: str) -> str:
    tree = ast.parse(_source(relative))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f'{relative} 里没有 {name}')


class AssignWifeAccessTests(unittest.TestCase):
    """分配老婆从"仅主人"改为"主人或白名单"。"""

    def test_whitelisted_user_passes_generic_gate(self) -> None:
        access = _load_upload_access()
        self.assertTrue(access.can_use_whitelisted_feature('10001', [], ['10001']))

    def test_master_passes_generic_gate_without_whitelist(self) -> None:
        access = _load_upload_access()
        self.assertTrue(access.can_use_whitelisted_feature('10001', ['10001'], []))

    def test_unlisted_user_is_rejected(self) -> None:
        access = _load_upload_access()
        self.assertFalse(access.can_use_whitelisted_feature('10001', ['20002'], ['30003']))

    def test_string_whitelist_accepts_common_separators(self) -> None:
        access = _load_upload_access()
        self.assertTrue(access.can_use_whitelisted_feature('10002', [], '10001, 10002\n10003'))

    def test_assign_gate_uses_assign_whitelist(self) -> None:
        gate = _function_source('TodayWaifu/shared.py', '_can_assign_wife')
        self.assertIn('_is_master(ev)', gate)
        self.assertIn('DailyWifeAssignWhitelist', gate)

    def test_assign_gate_is_exported_for_submodules(self) -> None:
        self.assertIn("'_can_assign_wife'", _source('TodayWaifu/shared.py'))

    def test_assign_handler_uses_whitelist_gate(self) -> None:
        handler = _function_source('TodayWaifu/daily.py', '_send_assign_wife')
        self.assertIn('_can_assign_wife(ev)', handler)
        self.assertNotIn('_is_master(ev)', handler)
        self.assertIn('白名单', handler)

    def test_assign_sv_allows_everyone_and_relies_on_gate(self) -> None:
        tree = ast.parse(_source('TodayWaifu/shared.py'))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if not any(isinstance(t, ast.Name) and t.id == 'assign_wife_sv' for t in node.targets):
                continue
            keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in node.value.keywords}
            self.assertNotIn('pm', keywords, '分配老婆的 SV 不能再锁 pm（否则白名单用户到不了处理器）')
            return
        raise AssertionError('没有找到 assign_wife_sv 的定义')

    def test_assign_whitelist_config_keeps_default_empty(self) -> None:
        tree = ast.parse(_source('config_default.py'))
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.value, ast.Dict):
                continue
            if not isinstance(node.target, ast.Name) or node.target.id != 'CONFIG_DEFAULT':
                continue
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and key.value == 'DailyWifeAssignWhitelist':
                    expr = ast.unparse(value)
                    self.assertIn('GsListStrConfig', expr)
                    self.assertTrue(expr.endswith('[])'), expr)
                    return
        raise AssertionError('CONFIG_DEFAULT 里没有 DailyWifeAssignWhitelist')


if __name__ == '__main__':
    unittest.main()
