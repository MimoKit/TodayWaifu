"""抢老婆/抢老公保护名单：配置项、判定函数与入口顺序。

名单用于让指定用户不能被抢。判定必须在读取目标记录之前短路，否则被保护用户
的记录仍会被加载并被后续流程当成可抢目标处理。
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROB_PATH = ROOT / 'TodayWaifu' / 'rob.py'
CONFIG_PATH = ROOT / 'config_default.py'


def _function_source(name: str) -> str:
    tree = ast.parse(ROB_PATH.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f'rob.py 里没有 {name}')


def _load_is_rob_protected(configured):
    """抽出 _is_rob_protected 单独执行，只需 _cfg 一个桩。"""
    tree = ast.parse(ROB_PATH.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_is_rob_protected'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {'_cfg': lambda key: configured}
    exec(compile(module, str(ROB_PATH), 'exec'), namespace)  # noqa: S102
    return namespace['_is_rob_protected']


def _config_entry(key: str) -> str:
    tree = ast.parse(CONFIG_PATH.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != 'CONFIG_DEFAULT' or not isinstance(node.value, ast.Dict):
            continue
        for item_key, item_value in zip(node.value.keys, node.value.values):
            if isinstance(item_key, ast.Constant) and item_key.value == key:
                return ast.unparse(item_value)
    raise AssertionError(f'CONFIG_DEFAULT 里没有 {key}')


class RobProtectionTests(unittest.TestCase):
    def test_config_exposes_protected_user_list_with_empty_default(self) -> None:
        expr = _config_entry('DailyWifeRobProtectedUsers')
        self.assertIn('GsListStrConfig', expr)
        self.assertTrue(expr.endswith('[])'), expr)

    def test_protected_helper_matches_configured_ids_only(self) -> None:
        is_protected = _load_is_rob_protected(['123456', ' 789012 '])
        self.assertTrue(is_protected('123456'))
        self.assertTrue(is_protected('789012'))
        self.assertFalse(is_protected('345678'))

    def test_protected_helper_accepts_comma_or_space_separated_text(self) -> None:
        is_protected = _load_is_rob_protected('123456, 789012\n345678')
        for user_id in ('123456', '789012', '345678'):
            self.assertTrue(is_protected(user_id), user_id)

    def test_protected_helper_ignores_empty_values(self) -> None:
        is_protected = _load_is_rob_protected('')
        self.assertFalse(is_protected(''))
        self.assertFalse(is_protected('123456'))

    def test_rob_entry_checks_protection_before_loading_target_record(self) -> None:
        source = ROB_PATH.read_text(encoding='utf-8-sig')
        check = source.index('if _is_rob_protected(target_user_id):')
        load = source.index('target_record = await _get_existing_daily_record')
        self.assertLess(check, load, '保护判定必须早于读取目标记录')

    def test_rob_entry_rejects_protected_target_with_explicit_reason(self) -> None:
        source = _function_source('_send_rob_daily')
        self.assertIn('_is_rob_protected(target_user_id)', source)
        self.assertIn('受到保护', source)


if __name__ == '__main__':
    unittest.main()
