# 兼容性契约守卫：把配置项、命令/权限等触发器声明、必需资源路径与插件加载顺序钉在
# tests/compatibility_manifest.json 的快照上。
#
# 快照产生于 a3f080a「refactor: lighten internals while preserving compatibility」——
# 一次以「对外行为零变化」为前提的重构，manifest 与测试在同一提交落地，作为该承诺的
# 可执行证据。manifest 的 base_commit（fc02294）标记了快照采集时的基线，比对失败本身
# 不说明改动有错，只说明对外契约发生了变化：要么回退，要么同步重生成 manifest。
#
# 逐一钉死而非只断言「键集合」的原因在于，各条断言防的是不同形态的退化：
#
# 1. 配置项的键名、类型表达式与默认值同时被比对。用户配置以 JSON 持久化，键名或默认值
#    一旦漂移，既有用户的配置会被静默重置，控制台也会新增或丢失条目。
# 2. 触发器整行比对（文件、函数名、装饰器文本），覆盖命令别名、权限组与 AI 描述。
#    提交 98446b8「honor TodayWaifu service permission for wife assignment」表明权限
#    参数被误改时不会有任何报错，只会让命令对错误的人群可见或不可见。
# 3. 资源路径与加载顺序无法由运行时行为稳定观测，只能以静态形式锁定。
#
# 本文件不执行被测代码，仅做 AST 解析与源码字符串匹配：这不依赖 gsuid_core，可在缺失
# core 依赖的环境中运行。代价是对格式敏感——被测源码的写法变化必须先重生成 manifest。
import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "tests" / "compatibility_manifest.json").read_text(encoding="utf-8"))


def _config_manifest() -> dict[str, list[dict[str, str]]]:
    # 用 ast.unparse 而非 ast.literal_eval：默认值里含 GsStrConfig(...) 等调用表达式，
    # 只有还原源码文本才能把「类型、默认值与 options 列表」一并纳入比对
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
            # 跳过非字面量键而非报错：与 manifest 生成脚本保持同一口径，避免因无关的
            # 动态键导致两侧结果长度不一致而产生噪声失败
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                rows.append({"key": key.value, "expr": ast.unparse(value)})
        result[node.target.id] = rows
    return result


def _trigger_manifest() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted((ROOT / "TodayWaifu").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        # 只取模块顶层函数定义：触发器必须在此处注册才会在插件加载时被 core 收集，
        # 嵌套函数里的装饰器不产生注册效果，纳入比对只会放宽契约
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                # 按 on_ 前缀识别触发器，可覆盖 on_command / on_prefix 等全部注册形式；
                # 装饰器名被改写时该触发器会整体从清单消失，索引位错位即可暴露
                if decorator.func.attr.startswith("on_"):
                    rows.append(
                        {
                            "file": path.relative_to(ROOT).as_posix(),
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
        # resources 是安装后必须随包提供的静态资产（文本映射、帮助页与图标贴图）；
        # 缺其一即导致对应功能在运行期抛错，而打包时漏文件不会有任何其它信号
        for relative in MANIFEST["resources"]:
            self.assertTrue((ROOT / relative).exists(), relative)

    def test_plugin_loading_order_remains_compatible(self) -> None:
        source = (ROOT / "TodayWaifu" / "__init__.py").read_text(encoding="utf-8-sig")
        # 只锁定顺序，不锁定 import 语句的其它部分：导入即注册触发器，重排会改变命令
        # 优先级（帮助被指定老婆命令拦截即属此类），而下方的 index 查找会在语句被删除
        # 或改名时直接抛 ValueError
        modules = ["shared", "help", "normal_wife", "daily", "rob", "gift", "divorce", "loli", "custom_role"]
        positions = [source.index(f"from . import {name}") for name in modules]
        self.assertEqual(positions, sorted(positions))

    def test_runtime_data_paths_remain_compatible(self) -> None:
        # shared 已按职责拆分，路径常量散落在 paths/constants 等模块，故扫描整个 TodayWaifu 包。
        # 比对的是拼接后的文本而非具体文件：断言只关心「这些路径字面量仍被引用」，
        # 锁定归属文件会让后续拆分再次触发误报
        twf_source = "\n".join(
            path.read_text(encoding="utf-8-sig") for path in sorted((ROOT / "TodayWaifu").glob("*.py"))
        )
        config = (ROOT / "daily_wife_config.py").read_text(encoding="utf-8-sig")
        # 每一项都是磁盘上的既有目录或文件名，改动即等于用户数据「换址」：旧数据不会
        # 被搬走，只会在新位置重建，表现为所有人的记录与已下载图片凭空消失
        for text in (
            "get_res_path('TodayWaifu')",
            "custom_role_map.json",
            "custom_role_map.txt",
            "custom_role_pile",
            "loli_images",
            "group_member_avatar_cache",
        ):
            self.assertIn(text, twf_source + config)


if __name__ == "__main__":
    unittest.main()
