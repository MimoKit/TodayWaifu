"""战双/异环老婆的文本必须与鸣潮共用同一套构造，且带上角色台词。

背景：pgr.py 曾经自己拼模板，只输出「你今天的战双老婆是XXX。」，既没有
get_role_quote 也没有署名行，导致战双老婆永远看不到台词；异环虽然走 daily 的
公共路径，但没有测试锁住，同样可能被改坏。
"""

import ast
import unittest
from typing import Any
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
DAILY_PATH = ROOT / 'TodayWaifu' / 'daily.py'
PGR_PATH = ROOT / 'TodayWaifu' / 'pgr.py'
QUOTES_MODULE = ROOT / 'TodayWaifu' / 'role_quotes.py'
BUNDLED_QUOTES = ROOT / 'role_quotes.json'

# 真台词库就在插件根；pgr.py 曾因为绕开公共构造函数而漏掉台词
QUOTES_FILE = BUNDLED_QUOTES if BUNDLED_QUOTES.is_file() else None


@dataclass
class _FakeRole:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


@dataclass
class _FakeKindMetadata:
    text_template_key: str
    text_template_default: str


@dataclass
class _FakeRecord:
    name: str
    role_ids: tuple[str, ...]

    def to_role(self) -> _FakeRole:
        return _FakeRole(self.name, self.role_ids, ('https://example.test/a.png',))


def _load_role_quotes_module() -> dict[str, Any]:
    tree = ast.parse(QUOTES_MODULE.read_text(encoding='utf-8-sig'))
    tree.body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.ImportFrom) and (node.module == 'resource_paths' or node.level == 1))
    ]
    globals_dict: dict[str, Any] = {'role_quotes_path': lambda: QUOTES_FILE}
    exec(compile(tree, str(QUOTES_MODULE), 'exec'), globals_dict)
    return globals_dict


def _extract_function(path: Path, name: str, globals_dict: dict[str, Any]) -> Any:
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), globals_dict)
    return globals_dict[name]


TEMPLATES = {
    'wife': ('DailyWifeTextTemplate', '你今天的老婆是{name}'),
    'nte': ('DailyWifeNteTextTemplate', '你今天的异环老婆是{name}。'),
    'pgr': ('DailyWifePgrTextTemplate', '你今天的战双老婆是{name}。'),
}


@unittest.skipUnless(QUOTES_FILE is not None, '缺少内置台词库，跳过文本构造测试')
class DailyWifeQuoteTextTests(unittest.TestCase):
    def setUp(self) -> None:
        quotes_mod = _load_role_quotes_module()
        self.get_role_quote = quotes_mod['get_role_quote']

        def fake_kind_metadata(mode: str) -> _FakeKindMetadata:
            key, default = TEMPLATES.get(mode, TEMPLATES['wife'])
            return _FakeKindMetadata(key, default)

        # 台词开关打开、ID 行关闭，专注验证「台词是否被带上」
        self.quote_on = {
            'RoleCandidate': _FakeRole,
            '_cfg_bool': lambda key, default=False: True if key == 'DailyWifeSendRoleQuote' else bool(default),
            '_daily_kind_metadata': fake_kind_metadata,
            '_cfg': lambda key: False,
            'get_role_quote': self.get_role_quote,
        }
        self.build_text = _extract_function(DAILY_PATH, '_build_text', dict(self.quote_on))

    def _pgr_result_text(self, record: _FakeRecord, user_id: str = ''):  # noqa: ANN202
        globals_dict = dict(self.quote_on)
        globals_dict['WifeRecord'] = _FakeRecord
        globals_dict['_build_text'] = self.build_text
        fn = _extract_function(PGR_PATH, '_pgr_result_text', globals_dict)
        return fn(record, user_id)

    def test_wife_text_has_quote(self) -> None:
        text = self.build_text(_FakeRole('折枝', ('1105',), ('x.png',)), 'wife', '123456')
        self.assertIn('折枝', text)
        self.assertIn('「', text)
        self.assertIn('——折枝', text)

    def test_nte_text_has_quote(self) -> None:
        """异环走的是 daily 的公共路径，必须同样带台词。"""
        text = self.build_text(_FakeRole('早雾', ('1003',), ('x.png',)), 'nte', '123456')
        self.assertIn('早雾', text)
        self.assertIn('「', text)
        self.assertIn('——早雾', text)

    def test_pgr_text_has_quote(self) -> None:
        """战双曾漏掉台词：pgr 现在必须复用 _build_text。"""
        text = self._pgr_result_text(_FakeRecord('露西亚', ('1001',)))
        self.assertIsNotNone(text)
        assert text is not None
        self.assertIn('露西亚', text)
        self.assertIn('「', text)
        self.assertIn('——露西亚', text)

    def test_pgr_reuses_shared_text_builder(self) -> None:
        """守卫：pgr.py 不得再自己拼模板。"""
        source = PGR_PATH.read_text(encoding='utf-8')
        self.assertIn('from .daily import _build_text', source)
        body = source[source.index('def _pgr_result_text(') : source.index('async def _send_daily_pgr_wife(')]
        self.assertIn('_build_text(', body)

    def test_quote_switch_off_drops_the_quote_line(self) -> None:
        globals_dict = dict(self.quote_on)
        globals_dict['_cfg_bool'] = lambda key, default=False: False
        build_text = _extract_function(DAILY_PATH, '_build_text', globals_dict)
        text = build_text(_FakeRole('折枝', ('1105',), ('x.png',)), 'wife', '123456')
        self.assertIn('折枝', text)
        self.assertNotIn('「', text)

    def test_pgr_text_respects_send_text_switch(self) -> None:
        globals_dict = dict(self.quote_on)
        globals_dict['WifeRecord'] = _FakeRecord
        globals_dict['_build_text'] = self.build_text
        globals_dict['_cfg_bool'] = lambda key, default=False: (
            False if key == 'DailyWifeSendText' else bool(default)
        )
        fn = _extract_function(PGR_PATH, '_pgr_result_text', globals_dict)
        self.assertIsNone(fn(_FakeRecord('露西亚', ('1001',))))


if __name__ == '__main__':
    unittest.main()
