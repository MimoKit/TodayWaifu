# 普通老婆（跨作品动漫角色）接入的契约：开关、文案模板与候选池过滤必须彼此一致。
#
# 普通老婆复用「今日老婆」的抽取与每日唯一性流程，只替换候选池与文案，因此任何一处口径
# 不一致都会表现为功能静默失效：开关开启却仍按鸣潮角色过滤（候选被清空）、图库未收录的
# 对照表角色从候选中消失（用户侧表现为某些角色永远抽不到）、接口地址回落到错误默认值。
# 本文件以抽取函数的方式锁定这些分支，无需启动框架。
import ast
import asyncio
import unittest
from typing import Any
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]
DAILY_PATH = ROOT / 'TodayWaifu' / 'daily.py'
NORMAL_WIFE_PATH = ROOT / 'TodayWaifu' / 'normal_wife.py'


def _module_defining(name: str) -> Path:
    """返回定义 name 的 TodayWaifu 模块路径，避免测试与模块划分方式耦合。

    shared.py 已按职责拆分（bc6ab41），目标函数所在文件会随职责调整迁移，故按定义位置
    动态定位，而非写死导入路径。
    """
    for path in sorted((ROOT / 'TodayWaifu').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return path
    raise AssertionError(f'{name} 未在任何 TodayWaifu 模块中定义')


def _extract_function(path: Path, name: str, globals_dict: dict[str, Any]):
    # 只抽取目标函数并注入替身全局名，使被测逻辑不依赖 gsuid_core 与真实配置系统；
    # 注入的全局名是函数体的隐式输入，缺失会直接抛 NameError。
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), globals_dict)
    return globals_dict[name]


@dataclass
class _FakeRoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


@dataclass
class _FakeKindMetadata:
    text_template_key: str = 'DailyWifeTextTemplate'
    text_template_default: str = '你今天的老婆是{name}。'


class NormalWifeFeatureTests(unittest.IsolatedAsyncioTestCase):
    def test_build_text_when_normal_wife_disabled(self) -> None:
        # 开关关闭时必须走类型元数据的常规模板，且不得出现普通老婆专属文案：
        # 两者若同时生效，未开启该功能的部署也会收到普通老婆提示。
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_cfg_bool': lambda key, default=False: False,
            '_daily_kind_metadata': lambda mode: _FakeKindMetadata(),
            '_cfg': lambda key: None,
        }
        build_text = _extract_function(DAILY_PATH, '_build_text', globals_dict)
        role = _FakeRoleCandidate('秧秧', ('1201',), ('https://example.test/yangyang.png',))
        text = build_text(role, mode='wife')
        self.assertIn('秧秧', text)
        self.assertNotIn('你的老婆来啦！', text)

    def test_build_text_when_normal_wife_enabled(self) -> None:
        # 开启普通老婆时走普通老婆模板，正常带上作品来源与角色名
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_cfg_bool': lambda key, default=False: key == 'DailyWifeNormalEnabled',
            '_daily_kind_metadata': lambda mode: _FakeKindMetadata(
                text_template_key='DailyWifeNormalTextTemplate',
                text_template_default='你今天的老婆是来自{role_id}的{name}！',
            )
            if mode == 'normal'
            else _FakeKindMetadata(),
            '_cfg': lambda key: None,
        }
        build_text = _extract_function(DAILY_PATH, '_build_text', globals_dict)
        role = _FakeRoleCandidate('雷电将军', ('原神', '原神!雷电将军'), ('https://example.test/normal.png',))
        text = build_text(role, mode='wife')
        self.assertEqual(text, '你今天的老婆是来自原神的雷电将军！')

    def test_build_text_normal_mode_with_work(self) -> None:
        # normal 模式的 role_id 取 role_ids[0]，仅当它不等于角色名时才作为「来源作品」渲染；
        # 断言中来源与角色名并存，锁定该分支不得退化为去重后的单一字段。
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_cfg_bool': lambda key, default=False: False,
            '_daily_kind_metadata': lambda mode: _FakeKindMetadata(
                text_template_key='DailyWifeNormalTextTemplate',
                text_template_default='你今天的老婆是来自{role_id}的{name}！',
            ),
            '_cfg': lambda key: None,
        }
        build_text = _extract_function(DAILY_PATH, '_build_text', globals_dict)
        role = _FakeRoleCandidate('雷电将军', ('原神', '原神!雷电将军'), ('https://example.test/raiden.png',))
        text = build_text(role, mode='normal')
        self.assertEqual(text, '你今天的老婆是来自原神的雷电将军！')

    def test_build_text_normal_mode_without_work(self) -> None:
        # role_ids[0] 等于角色名时视为没有来源作品，须切换到不含 {role_id} 的模板：
        # 若仍套用带来源的模板，输出会出现「来自初音未来的初音未来！」这类重复。
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_cfg_bool': lambda key, default=False: False,
            '_daily_kind_metadata': lambda mode: _FakeKindMetadata(
                text_template_key='DailyWifeNormalTextTemplate',
                text_template_default='你今天的老婆是来自{role_id}的{name}！',
            ),
            '_cfg': lambda key: None,
        }
        build_text = _extract_function(DAILY_PATH, '_build_text', globals_dict)
        role = _FakeRoleCandidate('初音未来', ('初音未来',), ('https://example.test/miku.png',))
        text = build_text(role, mode='normal')
        self.assertEqual(text, '你今天的老婆是初音未来！')

    def test_filter_by_mode_preserves_candidates_when_normal_wife_enabled(self) -> None:
        # 普通老婆角色不在鸣潮对照表内，过滤必须整体短路，否则候选池被清空、
        # 抽卡直接失败；此处以「不在对照表中的角色仍原样返回」固定该短路条件。
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_role_mode': lambda mode: mode,
            '_cfg_bool': lambda key, default=False: key == 'DailyWifeNormalEnabled',
            '_load_mode_role_map': lambda mode: {},
            '_load_custom_upload_role_map': lambda: {},
            '_normalize_role_name': lambda name: name,
        }
        filter_by_mode = _extract_function(_module_defining('_filter_by_mode'), '_filter_by_mode', globals_dict)
        role = _FakeRoleCandidate('未知角色', ('unknown_id',), ('https://example.test/pic.png',))
        candidates = (role,)
        filtered = filter_by_mode(candidates, mode='wife')
        self.assertEqual(filtered, candidates)

    def test_filter_by_mode_filters_when_normal_wife_disabled(self) -> None:
        # 开关关闭时按对照表的 ID 与规范化角色名双重匹配筛选：只按 ID 匹配会让
        # 本地图片目录名与对照表键写法不一致的角色被误删。
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_role_mode': lambda mode: mode,
            '_cfg_bool': lambda key, default=False: False,
            '_load_mode_role_map': lambda mode: {'1201': '秧秧'},
            '_load_custom_upload_role_map': lambda: {},
            '_normalize_role_name': lambda name: name,
        }
        filter_by_mode = _extract_function(_module_defining('_filter_by_mode'), '_filter_by_mode', globals_dict)
        role1 = _FakeRoleCandidate('秧秧', ('1201',), ('https://example.test/1.png',))
        role2 = _FakeRoleCandidate('未知角色', ('9999',), ('https://example.test/2.png',))
        filtered = filter_by_mode((role1, role2), mode='wife')
        self.assertEqual(filtered, (role1,))

    def test_normal_gallery_api_url_default(self) -> None:
        # 配置项已合并为统一的 DailyWifeApiUrl，默认地址取自 DEFAULT_GALLERY_BASE_URL
        globals_dict = {
            '_cfg': lambda key: '',
            'DEFAULT_GALLERY_BASE_URL': 'https://twfapi.xlinxc.cn',
        }
        get_url = _extract_function(NORMAL_WIFE_PATH, '_normal_gallery_api_url', globals_dict)
        self.assertEqual(get_url(), 'https://twfapi.xlinxc.cn/api/ceshi/roles')

    def test_normal_gallery_api_url_custom(self) -> None:
        # 自定义地址必须优先于默认地址：4a5513e 合并配置项后若漏掉该优先级，
        # 所有自建图库的部署都会被静默切回官方地址。
        globals_dict = {
            '_cfg': lambda key: 'https://custom.api.test/roles' if key == 'DailyWifeApiUrl' else '',
            'DEFAULT_GALLERY_BASE_URL': 'https://twfapi.xlinxc.cn',
        }
        get_url = _extract_function(NORMAL_WIFE_PATH, '_normal_gallery_api_url', globals_dict)
        self.assertEqual(get_url(), 'https://custom.api.test/roles')

    async def test_gallery_mode_supplements_local_images_for_missing_roles(self) -> None:
        # 图库模式下对照表角色可能尚未被图库收录，须用本地图片补位，否则这些角色
        # 会从候选集中静默消失；同时已有图库图片的角色必须保留图库地址而非被本地覆盖。
        role_map = {'1201': '秧秧', '9999': '折枝'}
        gallery_role = _FakeRoleCandidate('秧秧', ('1201',), ('https://gallery.test/yangyang.png',))
        local_role_1201 = _FakeRoleCandidate('秧秧', ('1201',), ('/local/yangyang.png',))
        local_role_9999 = _FakeRoleCandidate('折枝', ('9999',), ('/local/zhezhi.png',))

        from unittest.mock import MagicMock
        fake_logger = MagicMock()
        fake_time = MagicMock()
        fake_time.time.return_value = 1000.0

        async def fake_run_blocking(func, *args):
            # 测试里不需要真的线程池，直接同步执行，保持原有语义
            return func(*args)

        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            'CANDIDATE_CACHE': {},
            'CACHE_TTL_SECONDS': 300,
            'LOG_PREFIX': '[测试]',
            'logger': fake_logger,
            'time': fake_time,
            'asyncio': asyncio,
            'run_blocking': fake_run_blocking,
            '_image_source': lambda kind='wife': 'gallery',
            '_role_mode': lambda mode: mode,
            '_role_map_title': lambda mode: '老婆',
            '_load_mode_role_map': lambda mode: dict(role_map),
            '_load_custom_upload_candidates': lambda: (),
            '_fetch_gallery_payload_sync': lambda: {'roles': []},
            '_parse_role_candidates': lambda payload, mode, rmap: (gallery_role,),
            '_load_local_candidates': lambda mode: ((local_role_1201, local_role_9999), None),
            '_merge_role_candidates': lambda base, extra: base,
            '_normalize_role_name': lambda name: name,
        }
        load_uncached = _extract_function(
            _module_defining('_load_wuwa_candidates_uncached'),
            '_load_wuwa_candidates_uncached',
            globals_dict,
        )
        candidates, err = await load_uncached('wife')
        self.assertIsNone(err)
        self.assertIsNotNone(candidates)
        # 秧秧应保留图库图片，折枝应补充为本地图片
        names = {c.name: c.images[0] for c in candidates}
        self.assertEqual(names['秧秧'], 'https://gallery.test/yangyang.png')
        self.assertEqual(names['折枝'], '/local/zhezhi.png')


if __name__ == '__main__':
    unittest.main()
