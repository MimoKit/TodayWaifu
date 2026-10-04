"""按 test_compatibility_contract 的口径重生成 configs 段。

用法: python tools/regen_compat_manifest.py
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "compatibility_manifest.json"


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


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    before = json.dumps(manifest.get("configs"), ensure_ascii=False)
    manifest["configs"] = _config_manifest()
    after = json.dumps(manifest["configs"], ensure_ascii=False)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("configs 已更新" if before != after else "configs 无变化")


if __name__ == "__main__":
    main()
