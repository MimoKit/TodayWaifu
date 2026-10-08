"""角色台词的匹配、排版与开关契约：只读内置库、精确匹配优先、默认关闭。

台词库是随插件分发的只读资源，用户自定义图片目录只提供图像而不参与覆盖：允许覆盖会让
升级后的新旧文案混杂，同一角色在不同部署下呈现不一致（role_quotes.py 的模块约定）。
匹配方向的历史故障见 b915108——子串兜底曾为双向，未收录的「心」被已知键「鉴心」截胡，
且 data 目录的一份旧库永远压过升级后的内置库，导致新角色台词全部不可达。本文件因此同时
固定匹配方向与「数据只来自内置文件」两点。
"""

import ast
import json
import unittest
from typing import Any
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
DAILY_PATH = ROOT / "TodayWaifu" / "daily.py"
DATA_FILE = ROOT / "role_quotes.json"
ROLE_QUOTES_PATH = ROOT / "TodayWaifu" / "role_quotes.py"


def _load_role_quotes_module() -> dict[str, Any]:
    # 以语法树执行模块并剔除其对 resource_paths 的导入，改注入指向随包文件的路径提供者：
    # 这样测试断言的正是「运行时唯一真值源是内置 role_quotes.json」这一契约，
    # 同时绕开 resource_paths 对 GsCore 数据目录的依赖。
    # 注入包内模块身份（__name__ / __package__）以维持相对导入的解析语义。
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
    # 只抽取目标函数并复用已加载的台词模块命名空间，使被测文案逻辑不依赖 gsuid_core；
    # 注入的全局名是函数体的隐式输入，缺失会直接抛 NameError。
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


@unittest.skipUnless(DATA_FILE.is_file(), "缺少插件内置的 role_quotes.json，跳过台词库测试")
class RoleQuotesTests(unittest.TestCase):
    def setUp(self) -> None:
        # 逐条用例重新加载数据与模块：模块内缓存以文件 mtime 为准，共用实例会让
        # 「缓存未变更即复用」的逻辑掩盖解析错误。
        self.data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        self.role_quotes = self.data["role_quotes"]
        self.default_quotes = self.data["default_quotes"]
        self.role_quotes_mod = _load_role_quotes_module()
        self.get_role_quote = self.role_quotes_mod["get_role_quote"]

    def test_all_role_quotes_within_limit(self) -> None:
        """确保台词库中预设台词主体不超过 role_quotes.py 的截断阈值。"""
        # 该上限是排版约束而非风格偏好：台词与署名同处一张卡片，超长文本会被截断，
        # 用户看到的句子在语义中途断开。逐条遍历而非抽查，是因为新增角色时最易越界。
        # 阈值取自实现里的常量，避免测试与运行时各写一个数字而分叉。
        limit = int(self.role_quotes_mod["MAX_QUOTE_LENGTH"])
        for role_name, quotes in self.role_quotes.items():
            for quote in quotes:
                self.assertLessEqual(
                    len(quote),
                    limit,
                    f"角色 {role_name} 的台词超过 {limit} 字: {quote} (长度 {len(quote)})",
                )

        for quote in self.default_quotes:
            self.assertLessEqual(
                len(quote),
                limit,
                f"默认台词超过 {limit} 字: {quote} (长度 {len(quote)})",
            )

    def test_get_role_quote_format(self) -> None:
        """确保台词包含角色名称和括号，且角色名字在下一行右缩进对齐。"""
        # 格式由两行构成：正文包裹在「」内，署名单独成行并以破折号起首。
        # 未知角色必须走同构的兜底文案而非返回空串，否则未收录角色会整行缺失署名。
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

    def test_unlisted_name_never_borrows_another_role(self) -> None:
        """未收录的名字只能走兜底台词，不能被同名子串的其它角色截胡（曾把「心」算成「鉴心」）。"""
        # 以「鉴」固定匹配方向：反向匹配（已知键包含于角色名之外的另一方向）会让它
        # 命中「鉴心」并署上别人的名字，属 b915108 修复的回归。
        quote_text = self.get_role_quote("鉴")
        self.assertEqual(len(quote_text.splitlines()), 2)
        self.assertTrue(quote_text.splitlines()[1].endswith("——鉴"), quote_text)

    def test_build_text_includes_quote(self) -> None:
        """确保 _build_text 会附带角色的剧情/台词。"""
        # 开启台词时正文与署名必须同时出现：只拼正文会让用户无法分辨台词归属，
        # 只拼署名则失去内容本身。
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

    def test_build_text_omits_quote_when_disabled(self) -> None:
        """台词开关关闭时不附带台词；关闭是默认值。"""
        # 关闭时不得残留任何台词痕迹（含书名号）：残留会让未开启该功能的用户
        # 收到半截排版，且难以判断来自何处。
        globals_dict = {
            "RoleCandidate": _FakeRoleCandidate,
            "_cfg_bool": lambda key, default=False: False,
            "_daily_kind_metadata": lambda mode: _FakeKindMetadata(),
            "_cfg": lambda key: None,
            "get_role_quote": self.get_role_quote,
        }
        build_text = _extract_function(DAILY_PATH, "_build_text", globals_dict)
        role = _FakeRoleCandidate("折枝", ("1105",), ("https://example.test/zhezhi.png",))
        text = build_text(role, mode="wife", user_id="123456")
        self.assertIn("你今天的老婆是折枝", text)
        self.assertNotIn("——折枝", text)
        self.assertNotIn("「", text)

    def test_quote_switch_defaults_to_disabled(self) -> None:
        """守卫：台词开关默认必须关闭，避免台词库缺失时静默开个空开关。"""
        # 配置默认值与代码兜底默认值必须同时为 False：二者不一致时，配置缺失的部署会
        # 依代码兜底开启该功能，而控制台显示为关闭，形成无从排查的状态分歧。
        config_default = (ROOT / "config_default.py").read_text(encoding="utf-8")
        block = config_default[config_default.index("'DailyWifeSendRoleQuote'") :]
        block = block[: block.index("),")]
        self.assertIn("False", block, "DailyWifeSendRoleQuote 默认值应为 False")

        daily = DAILY_PATH.read_text(encoding="utf-8")
        self.assertIn("_cfg_bool('DailyWifeSendRoleQuote', False)", daily, "代码兜底默认值应为 False")


if __name__ == "__main__":
    unittest.main()
