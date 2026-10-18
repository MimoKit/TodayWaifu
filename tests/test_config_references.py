"""守卫配置键引用与每日记录桶的真实存在性。

三类退化都曾真实发生，且都被命令包装器吞掉，只在日志里留痕：

1. `daily_wife_config.py` 会删除一批旧键（*GalleryApiUrl / *LoliApiUrl 等），
   但代码里仍保留对它们的引用。这类引用永远取不到值 —— `get_config` 落兜底
   分支返回假配置，并且每次调用都追加一条 warning。
2. `TodayWaifu/kind_metadata.py` 的 `*_key` 字段属于间接引用，扫描字面量调用的
   守卫覆盖不到。normal 模式曾在此引用 4 个不存在的键（DailyWifeNormalRobEnabled
   等）。
3. `_get_today_context` 曾漏建 `normal_wives` 桶，致使普通老婆整条链路 `KeyError`，
   用户侧收不到任何回复。

三者均在 d7461ab「修复普通老婆缺桶崩溃与 4 个死配置键」中修复，本文件将其固化为
契约：键集合重新长出死引用、或桶名派生逻辑退回硬编码，都会在此处失败。
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'
# 所有会按配置键取值的读取器：新增读取器时须同步登记，否则新读取器的死引用将不被覆盖
READERS = ('_cfg', '_cfg_bool', '_cfg_int', '_cfg_probability')


def _exec_module(path: Path) -> dict[str, object]:
    """在隔离命名空间中求值一个无外部依赖的模块，返回其全局映射。

    这些模块只含字面量定义，无需导入 gsuid_core 即可求值，故直接执行；代价是模块
    顶层的副作用也会一并生效，仅对确有把握的纯数据模块使用。
    """
    namespace: dict[str, object] = {}
    exec(  # noqa: S102 - 这些模块只含字面量定义，直接求值是刻意的
        compile(path.read_text(encoding='utf-8'), path.stem, 'exec'),
        namespace,
    )
    return namespace


def _literal_constant(path: Path, name: str) -> object:
    """取出模块级的字面量赋值。

    `constants.py` 顶部有相对 import，不能整文件 exec，故只按 AST 取需要的常量。
    """
    tree = ast.parse(path.read_text(encoding='utf-8'))
    # 普通赋值与带注解赋值都要接受：两种写法在重构中互相转换过，只认其一会让守卫
    # 因格式调整而失效
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f'{path.name} 里没有字面量常量 {name}')


def _defined_keys() -> set[str]:
    # 取不到 CONFIG_DEFAULT 时直接失败而不返回空集：空集会让「无死引用」的断言恒真，
    # 守卫在重构后静默失去判别力
    #
    # 配置项已按控制台页面拆成多个模板（CONFIG_DEFAULT / APPEARANCE_CONFIG_DEFAULT /
    # QQ_BOT_CONFIG_DEFAULT），全部并入已定义集合：只取其中一个会让其余页面的配置键
    # 被误判为死引用，而漏报同样有害（新增页面的键无人守护）。
    tree = ast.parse((ROOT / 'config_default.py').read_text(encoding='utf-8-sig'))
    keys: set[str] = set()
    found_default = False
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        if 'CONFIG_DEFAULT' not in node.target.id:
            continue
        if node.target.id == 'CONFIG_DEFAULT':
            found_default = True
        keys.update(
            key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    if found_default:
        return keys
    raise AssertionError('config_default.py 里没有 CONFIG_DEFAULT')


def _extract_context_builder():
    """抽出 `_get_today_context` 单独执行，只需 _today_key / _context_key 两个桩。

    该函数位于 daily_store.py，而该模块导入链需要 gsuid_core，故用 AST 摘出函数体，
    再补一个 `from __future__ import annotations` 以保留原文件中的延迟注解求值语义。
    """
    path = PACKAGE / 'daily_store.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_get_today_context'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)

    # 记录模式与桶名的映射取自真实源码而非字面复制：守卫要验证的正是「上下文桶」与
    # 「元数据声明的桶名」是否一致，桩数据自造会让两侧同时漂移而无法发现分歧
    constants = _literal_constant(PACKAGE / 'constants.py', 'ALL_DAILY_RECORD_KINDS')
    metadata = _exec_module(PACKAGE / 'kind_metadata.py')['DAILY_KIND_METADATA']
    # 日期与桶名的桩固定取值：断言只关心「每个模式的桶是否建好」，让结果不随当天日期
    # 或事件内容变化
    namespace: dict[str, object] = {
        'ALL_DAILY_RECORD_KINDS': constants,
        '_daily_bucket_name': lambda kind: metadata[kind].bucket,
        '_today_key': lambda: '2026-01-01',
        '_context_key': lambda ev: 'onebot:G1',
    }
    exec(compile(module, str(path), 'exec'), namespace)  # noqa: S102
    # 返回惰性构造器而非已建好的上下文：每个用例各自取一份，避免字典在用例之间共享
    return lambda: namespace['_get_today_context']({'days': {}}, object())


class ConfigReferenceTests(unittest.TestCase):
    def test_every_cfg_literal_is_a_defined_key(self) -> None:
        # 只扫字面量实参，因此覆盖不到 kind_metadata 那类把键名存进变量的间接引用，
        # 后者由 test_kind_metadata_keys_are_defined 单独守护
        defined = _defined_keys()
        offenders: list[str] = []
        for path in sorted(PACKAGE.glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in READERS or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    if first.value not in defined:
                        # 记录文件与行号：死引用往往成批出现，定位依赖精确坐标
                        offenders.append(f'{path.name}:{node.lineno} -> {first.value}')
        self.assertEqual(offenders, [], '存在引用了未定义配置键的死引用')

    def test_legacy_keys_are_not_referenced_anywhere(self) -> None:
        """被 daily_wife_config 删除的旧键，代码里不应再出现。"""
        legacy = (
            'DailyWifeGalleryApiUrl',
            'DailyWifeNormalGalleryApiUrl',
            'DailyWifeLoliApiUrl',
            'DailyShotaGalleryApiUrl',
            'DailyWifePgrGalleryApiUrl',
            'DailyWifeRandomGalleryApiUrl',
        )
        # 按整包拼接后做子串匹配：旧键可能以 `or _cfg('旧键')` 的兜底形式或变量赋值形式
        # 出现，无法靠调用点扫描穷尽
        source = '\n'.join(path.read_text(encoding='utf-8') for path in sorted(PACKAGE.glob('*.py')))
        for key in legacy:
            self.assertNotIn(key, source, f'{key} 已被删除，不应再被引用')

    def test_kind_metadata_keys_are_defined(self) -> None:
        """DailyKindMetadata 的 `*_key` 字段是间接引用，必须同样存在。

        空字符串表示该模式不参与抢/送（见 kind_metadata 顶部说明），跳过。
        """
        defined = _defined_keys()
        tree = ast.parse((PACKAGE / 'kind_metadata.py').read_text(encoding='utf-8'))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != 'DailyKindMetadata':
                continue
            # 仅按 *_key 后缀收集：该后缀即「此字段存放配置键名」的约定，缺失配置项的
            # 后果是每次取该模式配置都落兜底分支
            for keyword in node.keywords:
                if keyword.arg is None or not keyword.arg.endswith('_key'):
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    key = keyword.value.value
                    # 空串是「该模式无此开关」的显式占位，不是漏配
                    if key and key not in defined:
                        offenders.append(f'kind_metadata.py:{node.lineno} {keyword.arg} -> {key}')
        self.assertEqual(offenders, [], 'DailyKindMetadata 引用了未定义的配置键')


class DailyContextBucketTests(unittest.TestCase):
    def test_every_record_kind_bucket_is_created(self) -> None:
        """`_get_today_context` 必须为 ALL_DAILY_RECORD_KINDS 的每个模式建好桶。

        调用方用 `context[bucket].get(...)` 直接下标，缺桶就 KeyError。
        """
        constants = _literal_constant(PACKAGE / 'constants.py', 'ALL_DAILY_RECORD_KINDS')
        metadata = _exec_module(PACKAGE / 'kind_metadata.py')['DAILY_KIND_METADATA']
        context = _extract_context_builder()()

        # 遍历模式全集而非逐个点名：新增记录模式时忘记建桶是本类故障的复发路径，
        # 而该写法让新模式的覆盖无需修改测试
        missing = [metadata[kind].bucket for kind in constants if metadata[kind].bucket not in context]
        self.assertEqual(missing, [], f'_get_today_context 未创建这些记录桶: {missing}')

    def test_non_record_buckets_are_still_created(self) -> None:
        """marry_members / rob_attempts / safe_wives 不属于记录模式，但同样被直接下标。"""
        # 这些桶不在 ALL_DAILY_RECORD_KINDS 中，上一条「按模式全集遍历」的守卫覆盖不到，
        # 若只依赖它，删掉任一非记录桶都不会被发现
        context = _extract_context_builder()()
        for bucket in ('marry_members', 'rob_attempts', 'safe_wives'):
            self.assertIn(bucket, context)


if __name__ == '__main__':
    unittest.main()
