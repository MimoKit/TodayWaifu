# R18 过滤开关的接线契约：开关只改写到服务端确实提供 nor18 变体的三个端点。
#
# 服务端只有鸣潮（/api/xwuid/roles/nor18）、萝莉（/loli/nor18）、正太（/shota/nor18）
# 三个 nor18 端点；战双与测试图库不含 R18 内容，也没有对应端点，套用会直接 404。
# 因此本文件同时锁定「该接的接上了」与「不该接的没接」两件事。
import ast
import unittest
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'
CONSTANTS_PATH = PACKAGE / 'constants.py'


def _load_constants_functions(*names: str) -> dict[str, Any]:
    # 只抽取目标函数并注入替身：constants.py 顶部有相对 import，整文件执行需要
    # gsuid_core 环境。_apply_no_r18 依赖 _no_r18_enabled，两者一起抽取。
    tree = ast.parse(CONSTANTS_PATH.read_text(encoding='utf-8'))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(
        body=[
            ast.ImportFrom(module='__future__', names=[ast.alias('annotations')], level=0),
            *wanted,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {}
    exec(compile(module, str(CONSTANTS_PATH), 'exec'), namespace)  # noqa: S102
    return namespace


def _url_builder(path: Path, name: str, no_r18: bool) -> Any:
    """抽取某个地址构造函数，注入固定开关值。"""
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(module='__future__', names=[ast.alias('annotations')], level=0),
            function,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace: dict[str, Any] = {
        '_cfg': lambda key: 'https://twfapi.xlinxc.cn' if key == 'DailyWifeApiUrl' else '',
        'DEFAULT_GALLERY_BASE_URL': 'https://twfapi.xlinxc.cn',
        '_apply_no_r18': lambda url: f'{url.rstrip("/")}/nor18' if no_r18 and url else url,
    }
    exec(compile(module, str(path), 'exec'), namespace)  # noqa: S102
    return namespace[name]


class NoR18RewriteTests(unittest.TestCase):
    def setUp(self) -> None:
        namespace = _load_constants_functions('_no_r18_enabled', '_apply_no_r18')
        self.apply = namespace['_apply_no_r18']

    def test_disabled_keeps_url_untouched(self) -> None:
        # 默认关闭：升级后行为必须与升级前逐字节一致，不能凭开关「顺手」改写地址
        namespace = _load_constants_functions('_no_r18_enabled', '_apply_no_r18')
        namespace['_cfg_bool'] = lambda key, default=False: default
        self.assertEqual(
            namespace['_no_r18_enabled'](),
            False,
            '开关默认值必须为关闭',
        )

    def test_suffix_is_appended_once(self) -> None:
        # 只做后缀追加而非整体替换：自定义图库的部署地址必须原样保留，否则所有
        # 自建部署都会被静默指向官方端点
        globals_dict = self.apply.__globals__
        original = globals_dict['_no_r18_enabled']
        globals_dict['_no_r18_enabled'] = lambda: True
        try:
            self.assertEqual(
                self.apply('https://custom.example.test/gallery'),
                'https://custom.example.test/gallery/nor18',
            )
            self.assertEqual(
                self.apply('https://custom.example.test/gallery/nor18'),
                'https://custom.example.test/gallery/nor18',
                '已带 nor18 后缀时不得重复拼接',
            )
            self.assertEqual(self.apply(''), '', '空地址保持为空，交由调用方报未配置')
        finally:
            globals_dict['_no_r18_enabled'] = original


class UrlBuilderWiringTests(unittest.TestCase):
    def test_wuwa_appends_nor18_when_enabled(self) -> None:
        build = _url_builder(PACKAGE / 'gallery.py', '_gallery_api_url', no_r18=True)
        off = _url_builder(PACKAGE / 'gallery.py', '_gallery_api_url', no_r18=False)
        self.assertEqual(build(), 'https://twfapi.xlinxc.cn/api/xwuid/roles/nor18')
        self.assertEqual(off(), 'https://twfapi.xlinxc.cn/api/xwuid/roles')

    def test_loli_appends_nor18_when_enabled(self) -> None:
        build = _url_builder(PACKAGE / 'loli.py', '_loli_api_url', no_r18=True)
        off = _url_builder(PACKAGE / 'loli.py', '_loli_api_url', no_r18=False)
        self.assertEqual(build(), 'https://twfapi.xlinxc.cn/loli/nor18')
        self.assertEqual(off(), 'https://twfapi.xlinxc.cn/loli')

    def test_shota_appends_nor18_when_enabled(self) -> None:
        build = _url_builder(PACKAGE / 'shota.py', '_shota_api_url', no_r18=True)
        off = _url_builder(PACKAGE / 'shota.py', '_shota_api_url', no_r18=False)
        self.assertEqual(build(), 'https://twfapi.xlinxc.cn/shota/nor18')
        self.assertEqual(off(), 'https://twfapi.xlinxc.cn/shota')

    def test_untouched_categories_have_no_nor18_endpoint(self) -> None:
        # 战双与测试图库在服务端没有 nor18 端点，接线上去会 404 并让整条抽卡链路失败。
        # 这条断言防的是「为了统一而顺手给所有分类都套上开关」。
        for module, builder in (
            ('gallery.py', '_pgr_gallery_api_url'),
            ('normal_wife.py', '_normal_gallery_api_url'),
        ):
            source = (PACKAGE / module).read_text(encoding='utf-8')
            tree = ast.parse(source)
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == builder
            )
            body = ast.unparse(function)
            self.assertNotIn('_apply_no_r18', body, f'{module} 不应接入 R18 开关')


if __name__ == '__main__':
    unittest.main()
