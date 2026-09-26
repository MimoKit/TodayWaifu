"""测试今日老婆抽取时附加角色剧情/对话台词。"""

import ast
import json
import unittest
from typing import Any
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
DAILY_PATH = ROOT / "TodayWaifu" / "daily.py"
_CORE_DATA = ROOT.parents[2] / "data" / "TodayWaifu" / "role_quotes.json"
_PLUGIN_DATA = ROOT / "data" / "TodayWaifu" / "role_quotes.json"
DATA_FILE = _CORE_DATA if _CORE_DATA.is_file() else _PLUGIN_DATA
ROLE_QUOTES_PATH = ROOT / "TodayWaifu" / "role_quotes.py"


def _load_role_quotes_module() -> dict[str, Any]:
    tree = ast.parse(ROLE_QUOTES_PATH.read_text(encoding="utf-8-sig"))
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.ImportFrom)
            and (
                node.module == "resource_paths"
                or (node.level == 1 and any(a.name == "role_quotes_path" for a in node.names))
            )
        )
    ]
    globals_dict: dict[str, Any] = {
        "__name__": "gsuid_core.plugins.TodayWaifu.TodayWaifu.role_quotes",
        "__package__": "gsuid_core.plugins.TodayWaifu.TodayWaifu",
        "role_quotes_path": lambda: DATA_FILE,
    }
    exec(compile(tree, str(ROLE_QUOTES_PATH), "exec"), globals_dict)
    return globals_dict


def _extract_function(path: Path, name: str, globals_dict: dict[str, Any]) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    function = next(
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    future = ast.ImportFrom(
        module="__future__",
        names=[ast.alias(name="annotations")],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), globals_dict)
    return globals_dict[name]


@dataclass
class _FakeRoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


@dataclass
class _FakeKindMetadata:
    text_template_key: str = "DailyWifeTextTemplate"
    text_template_default: str = "你今天的老婆是{name}"


@unittest.skipUnless(DATA_FILE.is_file(), "缺少 data/TodayWaifu/role_quotes.json，跳过台词库测试")
class RoleQuotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        self.role_quotes = self.data["role_quotes"]
        self.default_quotes = self.data["default_quotes"]
        self.role_quotes_mod = _load_role_quotes_module()
        self.get_role_quote = self.role_quotes_mod["get_role_quote"]

    def test_all_role_quotes_in_data_within_limit(self) -> None:
        """确保 data/TodayWaifu/role_quotes.json 中预设台词主体不超过 20 字。"""
        for role_name, quotes in self.role_quotes.items():
            for quote in quotes:
                self.assertLessEqual(
                    len(quote),
                    20,
                    f"角色 {role_name} 的台词超过 20 字: {quote} (长度 {len(quote)})",
                )

        for quote in self.default_quotes:
            self.assertLessEqual(
                len(quote),
                20,
                f"默认台词超过 20 字: {quote} (长度 {len(quote)})",
            )

    def test_get_role_quote_format(self) -> None:
        """确保台词包含角色名称和括号，且角色名字在下一行右缩进对齐。"""
        quote_text = self.get_role_quote("今汐")
        self.assertTrue(quote_text.startswith("「"))
        self.assertIn("——今汐", quote_text)
        lines = quote_text.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("——今汐"))

        # 未知角色也能正常 fallback 并带上角色名
        fallback_quote = self.get_role_quote("某个未知角色")
        self.assertTrue(fallback_quote.startswith("「"))
        self.assertIn("——某个未知角色", fallback_quote)
        fb_lines = fallback_quote.splitlines()
        self.assertEqual(len(fb_lines), 2)
        self.assertTrue(fb_lines[1].endswith("——某个未知角色"))

    def test_build_text_includes_quote(self) -> None:
        """确保 _build_text 会附带角色的剧情/台词。"""
        globals_dict = {
            "RoleCandidate": _FakeRoleCandidate,
            "_cfg_bool": lambda key, default=True: True if key == "DailyWifeSendRoleQuote" else False,
            "_daily_kind_metadata": lambda mode: _FakeKindMetadata(),
            "_cfg": lambda key: None,
            "get_role_quote": self.get_role_quote,
        }
        build_text = _extract_function(DAILY_PATH, "_build_text", globals_dict)
        role = _FakeRoleCandidate("折枝", ("1105",), ("https://example.test/zhezhi.png",))
        text = build_text(role, mode="wife", user_id="123456")
        self.assertIn("你今天的老婆是折枝", text)
        self.assertIn("——折枝", text)
        self.assertIn("「", text)


if __name__ == "__main__":
    unittest.main()
