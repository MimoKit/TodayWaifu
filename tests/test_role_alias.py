"""角色别名：默认用 XWUID 的别名表，没装该插件时回退插件自带的表，控制台可切换。

DailyWifeAliasSource 决定来源（xwuid / local）。测试注入假的 _cfg 与临时目录，覆盖
「XWUID 可用时用它的表」「XWUID 不可用时回退插件自带表」「配置成 local 时只用自带表」
「标准名不被顶掉」「未知名原样返回」以及 _normalize_role_name 的集成效果。
"""

import ast
import json
import tempfile
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALIASES_PATH = ROOT / 'TodayWaifu' / 'aliases.py'
ROLES_PATH = ROOT / 'TodayWaifu' / 'roles.py'

# 插件自带表（随插件分发）
BUNDLED = {
    '丹瑾': ['丹瑾', 'dj', '丹谨', '小西王'],
    '丽贝卡': ['丽贝卡', '大枪枪'],
    '心': ['心', '心月狐'],
}
# XWUID 的两份表：内置 + 用户自定义
XWUID_BUILTIN = {
    '丹瑾': ['丹瑾', 'xwuid专属别名'],
    '新角色': ['新角色', '别名一'],
}
XWUID_CUSTOM = {
    '丹瑾': ['用户加的名'],
}


def _load_aliases_module(root: Path, source: str) -> dict[str, Any]:
    """按 AST 加载 aliases.py，把 BASE_DIR / get_res_path / _cfg 换成可控实现。"""
    tree = ast.parse(ALIASES_PATH.read_text(encoding='utf-8-sig'))
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.ImportFrom)
            and (node.level == 1 or node.module == 'gsuid_core.data_store')
        )
    ]
    globals_dict: dict[str, Any] = {
        'get_res_path': lambda name='': root if not name else root / name,
        'BASE_DIR': root,
        '_cfg': lambda key: source if key == 'DailyWifeAliasSource' else None,
    }
    exec(compile(tree, str(ALIASES_PATH), 'exec'), globals_dict)
    return globals_dict


def _write_json(path: Path, payload: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def _write_bundled(root: Path) -> None:
    _write_json(root / 'role_aliases.json', BUNDLED)


def _write_xwuid(root: Path) -> None:
    _write_json(root / 'XutheringWavesUID' / 'resource' / 'map' / 'alias' / 'char_alias.json', XWUID_BUILTIN)
    _write_json(root / 'XutheringWavesUID' / 'alias' / 'char_alias.json', XWUID_CUSTOM)


class AliasSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_prefers_xwuid_when_installed(self) -> None:
        """装了 XWUID：走它的表，自带表不参与。"""
        _write_bundled(self.root)
        _write_xwuid(self.root)
        mod = _load_aliases_module(self.root, 'xwuid')
        self.assertEqual(mod['alias_source'](), 'xwuid')
        self.assertEqual(mod['resolve_role_name']('xwuid专属别名'), '丹瑾')
        self.assertEqual(mod['resolve_role_name']('用户加的名'), '丹瑾')
        # 自带表独有、XWUID 没有的别名不应生效
        self.assertEqual(mod['resolve_role_name']('小西王'), '小西王')

    def test_falls_back_to_bundled_when_xwuid_missing(self) -> None:
        """核心诉求：没装 XWUID 时自动回退插件自带的别名表。"""
        _write_bundled(self.root)
        mod = _load_aliases_module(self.root, 'xwuid')
        self.assertEqual(mod['alias_source'](), 'local')
        self.assertEqual(mod['resolve_role_name']('小西王'), '丹瑾')
        self.assertEqual(mod['resolve_role_name']('心月狐'), '心')

    def test_configured_local_ignores_xwuid(self) -> None:
        """控制台选 local 时，即使装了 XWUID 也只用自带表。"""
        _write_bundled(self.root)
        _write_xwuid(self.root)
        mod = _load_aliases_module(self.root, 'local')
        self.assertEqual(mod['alias_source'](), 'local')
        self.assertEqual(mod['resolve_role_name']('小西王'), '丹瑾')
        self.assertEqual(mod['resolve_role_name']('xwuid专属别名'), 'xwuid专属别名')

    def test_disabled_by_default(self) -> None:
        """默认关闭：off 时不做任何别名解析。"""
        _write_bundled(self.root)
        _write_xwuid(self.root)
        mod = _load_aliases_module(self.root, 'off')
        self.assertEqual(mod['alias_source'](), 'off')
        self.assertEqual(mod['resolve_role_name']('小西王'), '小西王')
        self.assertEqual(mod['resolve_role_name']('xwuid专属别名'), 'xwuid专属别名')

    def test_unknown_config_value_disables_aliases(self) -> None:
        """配置值异常时保守处理：关闭别名，而不是挑一份表来用。"""
        _write_bundled(self.root)
        mod = _load_aliases_module(self.root, '某个奇怪的值')
        self.assertEqual(mod['alias_source'](), 'off')
        self.assertEqual(mod['resolve_role_name']('小西王'), '小西王')


class AliasResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _write_bundled(self.root)
        _write_xwuid(self.root)
        self.mod = _load_aliases_module(self.root, 'xwuid')
        self.resolve = self.mod['resolve_role_name']

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_standard_name_stays_itself(self) -> None:
        for name in ('丹瑾', '丽贝卡', '心', '新角色'):
            with self.subTest(name=name):
                self.assertEqual(self.resolve(name), name)

    def test_unknown_name_returned_as_is(self) -> None:
        self.assertEqual(self.resolve('不存在的角色'), '不存在的角色')
        self.assertEqual(self.resolve(''), '')

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(self.resolve('  xwuid专属别名  '), '丹瑾')

    def test_all_tables_missing_degrades_to_identity(self) -> None:
        """两份表都不存在时不能报错，名字原样返回。"""
        with tempfile.TemporaryDirectory() as empty:
            mod = _load_aliases_module(Path(empty), 'xwuid')
            self.assertEqual(mod['resolve_role_name']('小西王'), '小西王')

    def test_broken_json_is_skipped(self) -> None:
        """XWUID 自定义表损坏时跳过它，其余表继续生效。"""
        bad = self.root / 'XutheringWavesUID' / 'alias' / 'char_alias.json'
        bad.write_text('{不是合法 JSON', encoding='utf-8')
        mod = _load_aliases_module(self.root, 'xwuid')
        self.assertEqual(mod['resolve_role_name']('xwuid专属别名'), '丹瑾')

    def test_index_rebuilds_when_files_change(self) -> None:
        """别名表更新后要能热生效（按 mtime 重建索引）。"""
        self.assertEqual(self.resolve('新别名'), '新别名')
        bundled = self.root / 'role_aliases.json'
        data = json.loads(bundled.read_text(encoding='utf-8'))
        data['丹瑾'] = [*data['丹瑾'], '新别名']
        _write_json(bundled, data)
        # 当前用的是 XWUID 表，改自带表不该影响结果
        self.assertEqual(self.resolve('新别名'), '新别名')
        builtin = self.root / 'XutheringWavesUID' / 'resource' / 'map' / 'alias' / 'char_alias.json'
        data = json.loads(builtin.read_text(encoding='utf-8'))
        data['丹瑾'] = [*data['丹瑾'], '新别名']
        _write_json(builtin, data)
        self.assertEqual(self.resolve('新别名'), '丹瑾')

    def test_bundled_alias_path_points_into_plugin_root(self) -> None:
        self.assertEqual(self.mod['bundled_alias_path'](), self.root / 'role_aliases.json')


class NormalizeRoleNameTests(unittest.TestCase):
    def test_normalize_applies_alias_and_separator(self) -> None:
        globals_dict: dict[str, Any] = {'resolve_role_name': lambda name: '丹瑾' if name == '小西王' else name}
        tree = ast.parse(ROLES_PATH.read_text(encoding='utf-8-sig'))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == '_normalize_role_name'
        )
        future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
        module = ast.Module(body=[future, function], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(ROLES_PATH), 'exec'), globals_dict)
        normalize = globals_dict['_normalize_role_name']

        self.assertEqual(normalize('小西王'), '丹瑾')
        self.assertEqual(normalize('折枝・x'), '折枝·x')
        self.assertEqual(normalize('  丽贝卡  '), '丽贝卡')


if __name__ == '__main__':
    unittest.main()
