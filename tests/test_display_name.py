"""显示名解析的契约：三个来源的优先级、缓存刷新与 ID 占位。

取名逻辑此前散在 members 与 daily 两处：同一用户会拿到不同的名字，兜底也各写各的
（`str(user_id)` / `user_id` / `_user_key`），且记录里的名字一旦写入就再也不会刷新
（`display_name_updated_at` 写了但没有任何地方读）。本文件把收敛后的行为钉住。

display_name 模块顶部依赖 gsuid_core，无 core 环境下不可整模块导入，故按 AST 抽取
纯函数与常量后单独求值——与 test_config_references / test_marry_member_self_exclusion
同一口径。
"""

from __future__ import annotations

import ast
import time
import unittest
from pathlib import Path
from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'
MODULE = PACKAGE / 'display_name.py'
CONSTANTS = PACKAGE / 'constants.py'

_PURE_FUNCTIONS = {
    'usable_name',
    'name_from_mapping',
    'placeholder_name',
    'is_stale',
    'resolve_display_name',
}
_PURE_CONSTANTS = {
    '_PLACEHOLDER_VALUES',
    '_NAME_FIELDS',
    '_ID_HEAD_KEEP',
    '_ID_TAIL_KEEP',
    '_ID_KEEP_WHOLE',
}


def _literal_constant(path: Path, name: str) -> object:
    """按 AST 取出模块级常量的值。

    constants.py 顶部有相对 import，不能整文件 exec；literal_eval 又只认字面量，而目标
    常量写作 ``24 * 60 * 60``。故只把该常量表达式的 AST 在空 builtins 下求值——作用面
    仅限这一行纯算术，不引入任何可调用对象。
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        expression: ast.expr | None = None
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            expression = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            expression = node.value
        if expression is None:
            continue
        wrapper = ast.Expression(body=expression)
        ast.fix_missing_locations(wrapper)
        return eval(compile(wrapper, str(path), 'eval'), {'__builtins__': {}}, {})  # noqa: S307
    raise AssertionError(f'{path.name} 里没有常量 {name}')


def _assigned_names(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _load_pure_namespace() -> dict[str, object]:
    tree = ast.parse(MODULE.read_text(encoding='utf-8'))
    body: list[ast.stmt] = [
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
    ]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _PURE_FUNCTIONS:
            body.append(node)
        elif _assigned_names(node) & _PURE_CONSTANTS:
            body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: dict[str, object] = {
        'time': time,
        'Mapping': Mapping,
        # 刷新间隔定义在 constants.py，模块里是 import 进来的，抽取时须一并补上
        'DISPLAY_NAME_REFRESH_SECONDS': _literal_constant(CONSTANTS, 'DISPLAY_NAME_REFRESH_SECONDS'),
    }
    exec(compile(module, str(MODULE), 'exec'), namespace)  # noqa: S102 - 只含纯函数与字面量常量
    return namespace


class UsableNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.usable_name = _load_pure_namespace()['usable_name']

    def test_placeholders_are_treated_as_missing(self) -> None:
        """上游会把 None / "1" / NULL 直接写进展示名，这些值不能进文案。"""
        for value in (None, '', '1', 'None', 'none', 'NULL', 'null', '   '):
            with self.subTest(value=value):
                self.assertEqual(self.usable_name(value), '')

    def test_name_equal_to_user_id_is_missing(self) -> None:
        """显示名等于 ID 时视为没有昵称，否则文案会「12345 → 角色」两边同值。"""
        self.assertEqual(self.usable_name('12345', '12345'), '')
        self.assertEqual(self.usable_name('12345', '99999'), '12345')

    def test_real_name_is_kept_with_surrounding_space_stripped(self) -> None:
        self.assertEqual(self.usable_name('  早柚  '), '早柚')


class NameFromMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.name_from_mapping = _load_pure_namespace()['name_from_mapping']

    def test_field_priority_is_card_then_nickname(self) -> None:
        """群名片优先于昵称：同一个人在不同平台下的字段语义不同，按由具体到宽泛回退。"""
        data = {'card': '群名片', 'nickname': '昵称', 'name': '名字'}
        self.assertEqual(self.name_from_mapping(data), '群名片')
        self.assertEqual(self.name_from_mapping({'nickname': '昵称', 'name': '名字'}), '昵称')
        self.assertEqual(self.name_from_mapping({'username': '用户名'}), '用户名')
        self.assertEqual(self.name_from_mapping({'user_name': '库里的名字'}), '库里的名字')

    def test_placeholder_field_falls_through_to_next(self) -> None:
        self.assertEqual(self.name_from_mapping({'card': '1', 'nickname': '昵称'}), '昵称')

    def test_non_mapping_returns_empty(self) -> None:
        self.assertEqual(self.name_from_mapping(None), '')
        self.assertEqual(self.name_from_mapping('早柚'), '')


class PlaceholderNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.placeholder_name = _load_pure_namespace()['placeholder_name']

    def test_numeric_id_is_kept_verbatim(self) -> None:
        """纯数字 ID 就是 QQ 号，可读也可复制去加好友，不该截断。"""
        self.assertEqual(self.placeholder_name('12345678'), '12345678')

    def test_short_identifier_is_kept_verbatim(self) -> None:
        self.assertEqual(self.placeholder_name('abcdefgh'), 'abcdefgh')

    def test_long_openid_is_truncated_keeping_both_ends(self) -> None:
        """openid 整串铺进列表会撑破排版，保留首尾以便人工区分。"""
        got = self.placeholder_name('A1B2C3D4E5F67890ABCDEF1234567890')
        self.assertEqual(got, 'A1B2C3…7890')

    def test_empty_id_yields_empty_placeholder(self) -> None:
        self.assertEqual(self.placeholder_name(''), '')
        self.assertEqual(self.placeholder_name(None), '')


class StalenessTests(unittest.TestCase):
    def setUp(self) -> None:
        namespace = _load_pure_namespace()
        self.is_stale = namespace['is_stale']
        self.ttl = namespace['DISPLAY_NAME_REFRESH_SECONDS']

    def test_missing_timestamp_counts_as_stale(self) -> None:
        """取不到时间戳时按过期处理，让老记录有机会被刷新一次。"""
        for value in (None, '', 'abc', True, []):
            with self.subTest(value=value):
                self.assertTrue(self.is_stale(value))

    def test_fresh_and_expired_timestamps(self) -> None:
        now = 1_800_000_000.0
        self.assertFalse(self.is_stale(now - 10, now))
        self.assertTrue(self.is_stale(now - self.ttl - 1, now))

    def test_numeric_string_timestamp_is_accepted(self) -> None:
        now = 1_800_000_000.0
        self.assertFalse(self.is_stale(str(int(now - 10)), now))
        self.assertTrue(self.is_stale(str(int(now - self.ttl - 1)), now))


class ResolveDisplayNameTests(unittest.TestCase):
    def setUp(self) -> None:
        namespace = _load_pure_namespace()
        self.resolve = namespace['resolve_display_name']
        self.ttl = namespace['DISPLAY_NAME_REFRESH_SECONDS']
        self.now = time.time()

    def _resolve(self, stored, group_names, user_id='88888', updated_at=None):
        return self.resolve(
            stored=stored,
            group_names=group_names,
            user_id=user_id,
            updated_at=self.now if updated_at is None else updated_at,
        )

    def test_missing_stored_name_adopts_group_name_and_asks_for_write(self) -> None:
        name, need_write = self._resolve(None, {'88888': '群友甲'})
        self.assertEqual(name, '群友甲')
        self.assertTrue(need_write)

    def test_fresh_stored_name_is_kept(self) -> None:
        """记录里的名字没过期就不再回写，避免每次列表都产生一次写。"""
        name, need_write = self._resolve('记录里的名字', {'88888': '群友甲'})
        self.assertEqual(name, '记录里的名字')
        self.assertFalse(need_write)

    def test_expired_stored_name_is_refreshed_from_group(self) -> None:
        """用户改了群名片后必须能更新，否则会永久显示旧名。"""
        name, need_write = self._resolve('旧名字', {'88888': '新名字'}, updated_at=self.now - self.ttl - 1)
        self.assertEqual(name, '新名字')
        self.assertTrue(need_write)

    def test_expired_name_keeps_stored_when_group_has_nothing(self) -> None:
        """群名单查不到时保留旧名字，总好过退回占位。"""
        name, need_write = self._resolve('旧名字', {}, updated_at=self.now - self.ttl - 1)
        self.assertEqual(name, '旧名字')
        self.assertFalse(need_write)

    def test_group_name_identical_to_stored_needs_no_write(self) -> None:
        name, need_write = self._resolve(None, {'88888': '群友甲'})
        name2, need_write2 = self._resolve(name, {'88888': '群友甲'}, updated_at=self.now - self.ttl - 1)
        self.assertEqual(name2, '群友甲')
        self.assertFalse(need_write2)
        self.assertTrue(need_write)

    def test_placeholder_used_when_no_source_available(self) -> None:
        name, need_write = self._resolve(None, {})
        self.assertEqual(name, '88888')
        self.assertFalse(need_write)

    def test_long_id_falls_back_to_truncated_placeholder(self) -> None:
        openid = 'A1B2C3D4E5F67890ABCDEF1234567890'
        name, need_write = self._resolve(None, {}, user_id=openid)
        self.assertEqual(name, 'A1B2C3…7890')
        self.assertFalse(need_write)

    def test_stored_name_equal_to_id_is_treated_as_missing(self) -> None:
        """老记录可能把 ID 当名字存过，此处必须重新解析而不是原样显示。"""
        name, need_write = self._resolve('88888', {'88888': '群友甲'})
        self.assertEqual(name, '群友甲')
        self.assertTrue(need_write)


class SingleSourceTests(unittest.TestCase):
    def test_name_field_probe_is_declared_in_one_place(self) -> None:
        """昵称字段探测只允许有一个真值源：散成两套正是「同一人两个名字」的成因。"""
        hits: list[str] = []
        for path in sorted(PACKAGE.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Tuple):
                    continue
                values = {e.value for e in node.elts if isinstance(e, ast.Constant)}
                if {'card', 'nickname'} <= values:
                    hits.append(path.name)
                    break
        self.assertEqual(hits, ['display_name.py'], '昵称字段探测必须只在 display_name.py 声明')

    def test_naming_helpers_are_not_redefined_outside_the_module(self) -> None:
        offenders: list[str] = []
        for path in sorted(PACKAGE.glob('*.py')):
            if path.name == 'display_name.py':
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _PURE_FUNCTIONS:
                    offenders.append(f'{path.name}:{node.name}')
        self.assertEqual(offenders, [], '取名函数不得在 display_name.py 之外重新定义')


if __name__ == '__main__':
    unittest.main()
