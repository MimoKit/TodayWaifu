# 异环角色池与统一离婚的回归守卫。
#
# 两处历史故障促成本文件：其一，异环对照表混入男性角色与两位主角，抽取结果中出现不应
# 发放的候选，修复方式是在读表后按名单与关键词过滤（828833c）；其二，各模式各自的离婚
# 实现互不一致，用户在某一模式离婚后仍可在另一模式保有同一角色，故收敛为唯一的
# _mark_all_daily_records_divorced（828833c）。
#
# 被测函数带相对导入，测试统一按 AST 抽取单个函数后编译执行，并只注入该函数真正引用的
# 名称，从而在不加载插件运行时的前提下锁定其行为。
import ast
import json
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _extract_function(path: Path, name: str, globals_dict: dict[str, Any]):
    # 源文件带 UTF-8 BOM（由编辑器写入），必须以 utf-8-sig 解码，否则首行 token 解析失败。
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    # 惰性注解要求目标模块已启用 future import；抽取后的片段脱离原模块，需补回该导入。
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), globals_dict)
    return globals_dict[name]


class NteRosterTests(unittest.TestCase):
    def test_builtin_nte_map_contains_all_current_non_protagonist_women(self) -> None:
        # 以全等断言固定名单，而非检查包含关系：异环对照表曾混入男性角色与两位主角，
        # 漏过滤会让其在融合模式下被抽中；名单扩增必须伴随本断言的显式更新。
        role_map = json.loads((ROOT / 'role_id_map.json').read_text(encoding='utf-8'))
        names = set(role_map['nte'].values())
        self.assertEqual(
            names,
            {
                '早雾',
                '安魂曲',
                '娜娜莉',
                '薄荷',
                '哈尼娅',
                '哈索尔',
                '法帝娅',
                '浔',
                '达芙蒂尔',
                '九原',
                '海月',
                '小吱',
                '伊洛伊',
                '真红',
                '残虹',
            },
        )

    def test_nte_filter_rejects_men_and_both_protagonists(self) -> None:
        # 关键词匹配在归一化后的名称上进行：对照表中主角写法存在「异能者·零(男)」等多种
        # 变体，仅按精确名单比对会大量漏判，故同时覆盖名单与关键词两条分支。
        roles_path = ROOT / 'TodayWaifu' / 'roles.py'
        is_excluded = _extract_function(
            roles_path,
            '_is_excluded_nte_role',
            {
                '_normalize_role_name': lambda name: name.replace('・', '·').strip(),
                'NTE_EXCLUDED_ROLE_NAMES': {
                    '翳',
                    '埃德嘉',
                    '白藏',
                    '阿德勒',
                    '卡厄斯',
                },
                'NTE_EXCLUDED_ROLE_KEYWORDS': (
                    '异能者·零',
                    '异能者零',
                    '男主',
                    '女主',
                ),
            },
        )

        for name in ('翳', '埃德嘉', '白藏', '阿德勒', '卡厄斯'):
            self.assertTrue(is_excluded(name), name)
        for name in ('异能者·零(男)', '异能者·零(女)', '男主', '女主'):
            self.assertTrue(is_excluded(name), name)
        for name in ('早雾', '安魂曲', '伊洛伊', '真红'):
            self.assertFalse(is_excluded(name), name)

        # 过滤必须接在读表之后：被排除的名称一旦进入 role_map，下游按 ID 建候选时无从区分。
        source = roles_path.read_text(encoding='utf-8-sig')
        self.assertIn('if not _is_excluded_nte_role(role_name)', source)


class UnifiedDivorceTests(unittest.TestCase):
    # 各模式的桶名由被测实现通过 _daily_bucket_name 解析，此处以字典给出与生产同构的映射，
    # 使断言只验证「遍历了哪些 kind」，而不重复实现桶名规则本身。
    def _mark_all(self):
        bucket_names = {
            'wife': 'wives',
            'nte': 'nte_wives',
            'pgr': 'pgr_wives',
            'husband': 'husbands',
            'loli': 'lolis',
        }
        return _extract_function(
            ROOT / 'TodayWaifu' / 'daily_store.py',
            '_mark_all_daily_records_divorced',
            {
                'Any': Any,
                'ALL_DAILY_RECORD_KINDS': tuple(bucket_names),
                '_daily_bucket_name': bucket_names.__getitem__,
            },
        )

    def test_unified_divorce_marks_every_mode_and_only_current_user(self) -> None:
        # context 覆盖全部五个记录桶，外加不在 ALL_DAILY_RECORD_KINDS 中的 safe_wives：
        # 后者由实现单独处理，若把它并入种类表，safe_wife 会在返回列表中被重复标记，
        # 通知条数将与实际状态变更次数不符。
        context = {
            'wives': {'u1': {'name': '今汐'}, 'u2': {'name': '长离'}},
            'nte_wives': {'u1': {'name': '早雾'}},
            'pgr_wives': {'u1': {'name': '露西亚'}},
            'husbands': {'u1': {'name': '忌炎'}},
            'lolis': {'u1': {'name': '萝莉'}},
            'safe_wives': {'u1': {'name': '珂莱塔', 'safe': True}},
        }

        divorced = self._mark_all()(context, 'u1', 123456)

        self.assertEqual(len(divorced), 6)
        for bucket in (
            'wives',
            'nte_wives',
            'pgr_wives',
            'husbands',
            'lolis',
            'safe_wives',
        ):
            self.assertTrue(context[bucket]['u1']['divorced'])
            self.assertEqual(context[bucket]['u1']['divorced_at'], 123456)
        # 离婚仅作用于发起者：u2 的记录若被一并标记，同群其他用户的当日老婆会被误清。
        self.assertNotIn('divorced', context['wives']['u2'])

    def test_unified_divorce_is_idempotent(self) -> None:
        # 已离婚的记录不重复计入返回值，且 divorced_at 保持首次时间戳；重复执行时改写时间
        # 会让「离婚时刻」随命令次数漂移，令按时间排序的后续逻辑失去依据。
        context = {
            'wives': {'u1': {'name': '今汐'}},
            'nte_wives': {},
            'pgr_wives': {},
            'husbands': {},
            'lolis': {},
            'safe_wives': {},
        }
        mark_all = self._mark_all()

        self.assertEqual(mark_all(context, 'u1', 100), [('wife', '今汐')])
        self.assertEqual(mark_all(context, 'u1', 200), [])
        self.assertEqual(context['wives']['u1']['divorced_at'], 100)

    def test_divorced_state_takes_precedence_over_old_transfer_flags(self) -> None:
        # 同一条记录可能同时带 stolen_by、gifted_to 与 divorced：这些字段由互不协调的抢、
        # 送路径写入。若判定顺序反转，显式离婚会被误报为「被抢走」，用户看到的原因与事实不符。
        wife_state = _extract_function(
            ROOT / 'TodayWaifu' / 'daily_store.py',
            '_wife_state',
            {'Any': Any},
        )

        self.assertEqual(
            wife_state(
                {
                    'name': '露西亚',
                    'stolen_by': 'u2',
                    'gifted_to': 'u3',
                    'divorced': True,
                }
            ),
            'divorced',
        )

    def test_loli_divorce_result_hides_internal_image_id(self) -> None:
        # 萝莉记录名取自图片文件名（如「萝莉图b1b06da1」），直接回显会向用户暴露内部图片
        # 标识，且与用户所见的「今日萝莉」不一致（8c89b79）。
        result_name = _extract_function(
            ROOT / 'TodayWaifu' / 'divorce.py',
            '_divorce_result_name',
            {},
        )

        self.assertEqual(result_name('loli', '萝莉图b1b06da1'), '今日萝莉')
        self.assertEqual(result_name('pgr', '露西亚'), '露西亚')


if __name__ == '__main__':
    unittest.main()
