import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "tests" / "compatibility_manifest.json").read_text(encoding="utf-8"))


def _config_manifest() -> dict[str, list[dict[str, str]]]:
    tree = ast.parse((ROOT / "config_default.py").read_text(encoding="utf-8-sig"))
    result: dict[str, list[dict[str, str]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or not isinstance(node.value, ast.Dict):
            continue
        if node.target.id not in {"CONFIG_DEFAULT", "APPEARANCE_CONFIG_DEFAULT"}:
            continue
        rows: list[dict[str, str]] = []
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                rows.append({"key": key.value, "expr": ast.unparse(value)})
        result[node.target.id] = rows
    return result


def _trigger_manifest() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted((ROOT / "twf").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                if decorator.func.attr.startswith("on_"):
                    rows.append(
                        {
                            "file": str(path.relative_to(ROOT)),
                            "function": node.name,
                            "decorator": ast.unparse(decorator),
                        }
                    )
    return rows


class CompatibilityContractTests(unittest.TestCase):
    def test_config_keys_types_and_defaults_remain_compatible(self) -> None:
        self.assertEqual(_config_manifest(), MANIFEST["configs"])

    def test_commands_aliases_permissions_and_ai_descriptions_remain_compatible(self) -> None:
        self.assertEqual(_trigger_manifest(), MANIFEST["triggers"])

    def test_required_resources_keep_their_existing_paths(self) -> None:
        for relative in MANIFEST["resources"]:
            self.assertTrue((ROOT / relative).exists(), relative)

    def test_plugin_loading_order_remains_compatible(self) -> None:
        source = (ROOT / "__init__.py").read_text(encoding="utf-8-sig")
        modules = ["shared", "help", "daily", "pgr", "rob", "gift", "divorce", "loli", "custom_role"]
        positions = [source.index(f"from .twf import {name}") for name in modules]
        self.assertEqual(positions, sorted(positions))

    def test_runtime_data_paths_remain_compatible(self) -> None:
        shared = (ROOT / "twf" / "shared.py").read_text(encoding="utf-8-sig")
        config = (ROOT / "daily_wife_config.py").read_text(encoding="utf-8-sig")
        for text in (
            "get_res_path('TodayWaifu')",
            "daily_wife_data.json",
            "custom_role_map.txt",
            "custom_role_pile",
            "loli_images",
            "group_member_avatar_cache",
        ):
            self.assertIn(text, shared + config)


if __name__ == "__main__":
    unittest.main()
