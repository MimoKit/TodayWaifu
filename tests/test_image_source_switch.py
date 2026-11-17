"""图片来源开关按功能拆分：各功能可独立选择本地图片或远程图库。

改造前 `_image_source()` 无参、只读取每日老婆的单一开关，战双、萝莉与异环的远程路径都不受
它约束：战双与萝莉始终优先请求远程接口，异环则无条件回退第三方 CDN。用户无法按功能取舍，
也无法让某项功能完全离线。dd71d75 引入按功能名寻址的开关表，本文件锁定其契约。

两条约束值得单独说明。其一，映射必须按功能名而非 role_mode：loli 的 role_mode 是 wife，
若以 role_mode 索引，萝莉会错误地读取老婆的开关，两项功能不再独立。其二，新开关的默认值
必须逐项等于改造前的实际行为（原先走远程的默认 gallery），否则升级后未改动配置的用户会看到
表现突变。

此外还要防一类不报错的退化：`_image_source` 直接以 kind 索引默认值表，两张表一旦不同构
（缺键）即抛 KeyError，因此除逐项默认值外另设同构性断言。
"""

import ast
import asyncio
import inspect
import logging
import unittest
from pathlib import Path
from collections.abc import Callable, Awaitable

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'

# 功能名 -> 配置键。loli 的 role_mode 是 wife，因此必须按功能名而非 role_mode 映射
EXPECTED_KEYS = {
    'wife': 'DailyWifeImageSource',
    'husband': 'DailyWifeImageSource',
    'nte': 'DailyWifeNteImageSource',
    'pgr': 'DailyWifePgrImageSource',
    'loli': 'DailyLoliImageSource',
}

# 新开关的默认值必须等于改造前的实际行为，否则升级后表现会突变
EXPECTED_DEFAULTS = {
    'wife': 'local',
    'husband': 'local',
    'nte': 'gallery',
    'pgr': 'gallery',
    'loli': 'gallery',
}


def _module_mapping(path: Path, name: str) -> dict[str, str]:
    """在不导入被测模块的前提下读取其模块级字典常量，供断言与源码真值直接比对。"""
    # 以 AST 取字面量而非导入模块：constants.py 依赖包内相对导入，导入会连带拉起
    # gsuid_core；literal_eval 同时排除了表达式求值，确保读到的是静态常量本身。
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value = ast.literal_eval(node.value)
            if not isinstance(value, dict):
                raise AssertionError(f'{name} 不是字典字面量')
            return {str(key): str(item) for key, item in value.items()}
    raise AssertionError(f'{path.name} 里没有 {name}')


def _config_exprs() -> dict[str, str]:
    """把 CONFIG_DEFAULT 的每个配置项还原成源码表达式，便于断言选项与默认值。"""
    # 保留源码表达式而非求值结果，才能同时断言 options 列表与默认值这两部分内容。
    tree = ast.parse((ROOT / 'config_default.py').read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == 'CONFIG_DEFAULT' and isinstance(node.value, ast.Dict):
                return {
                    key.value: ast.unparse(value)
                    for key, value in zip(node.value.keys, node.value.values)
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
    raise AssertionError('config_default.py 里没有 CONFIG_DEFAULT')


def _extract_function(
    path: Path,
    name: str,
    extra_globals: dict[str, object],
) -> Callable[..., object]:
    """按名字抽出单个函数（同步或协程均可），配合注入的 fake 依赖做行为验证。"""
    # 抽出而非导入同样是为了绕开 gsuid_core 依赖；注入的 fake 让网络与磁盘可被观测，
    # 从而断言「是否真的发起了远程请求」这类行为，而非仅核对源码文本。
    tree = ast.parse(path.read_text(encoding='utf-8'))
    wanted = [
        node for node in tree.body if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name
    ]
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, *wanted], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(extra_globals)
    exec(compile(module, str(path), 'exec'), namespace)
    extracted = namespace[name]
    if not callable(extracted):
        raise AssertionError(f'{name} 不是可调用对象')
    return extracted


async def _resolve(awaitable: object) -> object:
    """把提取出的协程包一层供 asyncio.run 消费；抽到同步函数时会在这里明确失败。"""
    if not inspect.isawaitable(awaitable):
        raise AssertionError('提取出的函数不是协程函数')
    return await awaitable


class ImageSourceMappingTests(unittest.TestCase):
    def test_each_kind_maps_to_its_own_config_key(self) -> None:
        mapping = _module_mapping(PACKAGE / 'constants.py', '_IMAGE_SOURCE_CONFIG_KEYS')
        self.assertEqual(mapping, EXPECTED_KEYS)

    def test_new_switches_default_to_the_previous_behaviour(self) -> None:
        defaults = _module_mapping(PACKAGE / 'constants.py', '_IMAGE_SOURCE_DEFAULTS')
        self.assertEqual(defaults, EXPECTED_DEFAULTS)

    def test_both_source_tables_cover_the_same_kinds(self) -> None:
        """两张表必须同构：`_image_source` 用 kind 直接索引默认值表，缺键会 KeyError。"""
        keys = _module_mapping(PACKAGE / 'constants.py', '_IMAGE_SOURCE_CONFIG_KEYS')
        defaults = _module_mapping(PACKAGE / 'constants.py', '_IMAGE_SOURCE_DEFAULTS')
        self.assertEqual(set(keys), set(defaults))

    def test_switches_are_editable_in_the_console_with_both_options(self) -> None:
        # 仅存在于代码中的默认值不算开关：配置项必须在 CONFIG_DEFAULT 中出现，且同时
        # 提供 local 与 gallery 两个选项，否则用户只能接受默认行为。
        exprs = _config_exprs()
        for key, default in (
            ('DailyWifeImageSource', 'local'),
            ('DailyWifeNteImageSource', 'gallery'),
            ('DailyWifePgrImageSource', 'gallery'),
            ('DailyLoliImageSource', 'gallery'),
        ):
            self.assertIn(key, exprs, key)
            self.assertIn(f"'{default}'", exprs[key], key)
            self.assertIn("options=['local', 'gallery']", exprs[key], key)


class SourceSwitchWiringTests(unittest.TestCase):
    """每个取图点都必须按自己的功能名查询，不能退回无参的全局开关。"""

    def test_wuwa_passes_role_mode(self) -> None:
        gallery = (PACKAGE / 'gallery.py').read_text(encoding='utf-8')
        self.assertIn('_image_source(role_mode)', gallery)

    def test_pgr_checks_its_own_switch_before_going_remote(self) -> None:
        # local 判断必须早于远程地址拼接：顺序颠倒时即使配置为 local，仍会先构造并
        # 访问远程地址，开关形同虚设。
        gallery = (PACKAGE / 'gallery.py').read_text(encoding='utf-8')
        body = gallery[gallery.index('async def _load_pgr_wife_candidates(') : gallery.index('def _gallery_api_url(')]
        self.assertIn("_image_source('pgr') == 'local'", body)
        self.assertLess(
            body.index("_image_source('pgr')"),
            body.index('_pgr_gallery_api_url()'),
            'local 判断必须早于远程地址拼接',
        )

    def test_nte_switch_controls_the_official_cdn_fallback(self) -> None:
        # 异环的开关同时控制两处：候选加载侧是否允许官方 CDN 兜底，以及本地扫描侧
        # 是否在无图时继续走远程。二者共用 allow_remote_fallback 参数，任一处未接线
        # 都会使 local 模式下仍然访问官方 CDN。
        gallery = (PACKAGE / 'gallery.py').read_text(encoding='utf-8')
        nte_body = gallery[
            gallery.index('async def _load_nte_candidates(') : gallery.index('async def _load_candidates(')
        ]
        self.assertIn("_image_source('nte')", nte_body)
        self.assertIn("source == 'gallery'", nte_body)

        roles = (PACKAGE / 'roles.py').read_text(encoding='utf-8')
        local_body = roles[
            roles.index('def _load_nte_local_candidates(') : roles.index('def _load_pgr_local_candidates(')
        ]
        self.assertIn('allow_remote_fallback: bool = True', local_body)
        self.assertIn('if not images and allow_remote_fallback:', local_body)
        self.assertIn('continue', local_body)

    def test_pgr_candidates_route_to_the_pgr_loader(self) -> None:
        """_load_candidates('pgr') 曾落到鸣潮分支并必然报错，零点预热因此完全空转。"""
        gallery = (PACKAGE / 'gallery.py').read_text(encoding='utf-8')
        body = gallery[gallery.index('async def _load_candidates(') :]
        self.assertIn("if role_mode == 'pgr':", body)
        self.assertIn('await _load_pgr_wife_candidates()', body)

    def test_loli_switch_gates_the_remote_api_url(self) -> None:
        # local 模式下把接口地址置为空串，使下游的远程请求分支自然失效；这比在
        # loli.py 内部另加分支更不易漏改。
        loli = (PACKAGE / 'loli.py').read_text(encoding='utf-8')
        self.assertIn("_loli_api_url() if _image_source('loli') == 'gallery' else ''", loli)

class PgrBehaviourTests(unittest.TestCase):
    """行为级验证：local 完全不碰网络，gallery 优先远程、失败才回退本地。"""

    def _load_pgr(self, source: str, recorder: list[str]) -> Callable[..., object]:
        # 远程抓取一律抛错，使 gallery 模式必然走到回退分支；recorder 据此还原实际
        # 调用次序，用于证明 local 模式未发起任何远程请求（而非仅「最终结果相同」）。
        def fake_fetch(url: str) -> dict[str, object]:
            recorder.append(url)
            raise RuntimeError('远程图库不可用')

        def fake_local() -> tuple[str, ...]:
            recorder.append('local')
            return ('本地战双角色',)

        async def fake_run_blocking(func: Callable[..., object], *args: object) -> object:
            return func(*args)

        class _FakeCache:
            async def get(self, key: str, loader: Callable[[], Awaitable[object]]) -> object:
                return await loader()

        return _extract_function(
            PACKAGE / 'gallery.py',
            '_load_pgr_wife_candidates',
            {
                'run_blocking': fake_run_blocking,
                'logger': logging.getLogger('test'),
                'LOG_PREFIX': '[测试]',
                '_image_source': lambda kind='wife': source,
                '_pgr_gallery_api_url': lambda: 'https://gallery.test/api/pgr/roles',
                '_fetch_gallery_payload_from_url_sync': fake_fetch,
                '_parse_pgr_gallery_candidates': lambda payload: (),
                '_PGR_CANDIDATE_CACHE': _FakeCache(),
                '_load_pgr_local_candidates': fake_local,
            },
        )

    def test_local_mode_never_touches_the_remote_gallery(self) -> None:
        recorder: list[str] = []
        result = asyncio.run(_resolve(self._load_pgr('local', recorder)()))
        self.assertEqual(result, ('本地战双角色',))
        self.assertEqual(recorder, ['local'], 'local 模式不应发起任何远程请求')

    def test_gallery_mode_prefers_remote_then_falls_back_to_local(self) -> None:
        recorder: list[str] = []
        result = asyncio.run(_resolve(self._load_pgr('gallery', recorder)()))
        self.assertEqual(result, ('本地战双角色',))
        self.assertEqual(recorder[0], 'https://gallery.test/api/pgr/roles')
        self.assertIn('local', recorder)


class NteBehaviourTests(unittest.TestCase):
    """异环：local 跳过无图角色并把「一张都没有」报成明确原因，gallery 用官方资源兜底。"""

    class _FakeRole:
        def __init__(self, name: str, role_ids: tuple[str, ...], images: tuple[str, ...]) -> None:
            self.name = name
            self.role_ids = role_ids
            self.images = images

    def _load_nte(self) -> Callable[..., object]:
        # 角色表提供「角色A」，但不注入任何本地图片根，从而稳定复现「有角色但无图」，
        # 这正是 local 模式下必须给出明确原因的边界输入。
        return _extract_function(
            PACKAGE / 'roles.py',
            '_load_nte_local_candidates',
            {
                'Path': Path,
                'logger': logging.getLogger('test'),
                'LOG_PREFIX': '[测试]',
                'IMAGE_EXTENSIONS': ('.png',),
                'NTE_DETAIL_CDN_BASE': 'https://cdn.test',
                'RoleCandidate': self._FakeRole,
                '_resolve_role_map_path': lambda mode: Path('role_id_map.json'),
                '_load_role_map': lambda path, mode: {'1001': '角色A'},
                '_is_excluded_nte_role': lambda name: False,
                '_resolve_nte_custom_panel_root': lambda: None,
                '_resolve_nte_default_panel_root': lambda: None,
                '_nte_static_resource_roots': lambda: (),
                '_collect_role_candidates': lambda *args: (),
            },
        )

    def test_local_mode_without_any_local_image_reports_a_reason(self) -> None:
        # _load_nte_local_candidates 是同步函数，直接调用即可。
        # 本地无图时必须返回 (None, 原因) 而非空候选：((), None) 表示「确实没有角色」，
        # 上层据此只能给出笼统提示，真实原因是「本地图片缺失、可切图库」。
        result = self._load_nte()(False)
        self.assertIsInstance(result, tuple)
        candidates, error = result
        self.assertIsNone(candidates, '本地无图时不能返回空候选，否则上层只能报“没有可用角色”')
        self.assertIn('本地没有找到可用的异环角色图片', error or '')

    def test_gallery_mode_falls_back_to_the_official_cdn(self) -> None:
        # 开启远程兜底时，图片地址由 role_id 拼接官方 CDN 基址；断言到具体 URL 而非
        # 仅断言非空，可发现拼接格式（分隔符、ID 取值）的漂移。
        result = self._load_nte()(True)
        self.assertIsInstance(result, tuple)
        candidates, error = result
        self.assertIsNone(error)
        self.assertIsNotNone(candidates)
        self.assertEqual(candidates[0].images, ('https://cdn.test/1001.png',))


if __name__ == '__main__':
    unittest.main()
