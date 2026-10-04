"""老婆列表：条目超过阈值时渲染成图片，不超过时保持纯文本。

测试不依赖框架环境：渲染模块直接用 PIL 跑真实渲染并校验 PNG 头；_send_wife_list 用
AST 提取 + 注入 fake 依赖，验证「超过阈值发图片、未超过发文本」这一行为分支。
"""

import io
import ast
import asyncio
import tempfile
import unittest
from typing import Any
from pathlib import Path

from PIL import Image, ImageFont

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'
LIST_IMAGE_PATH = PACKAGE / 'list_image.py'
DAILY_PATH = PACKAGE / 'daily.py'
CONSTANTS_PATH = PACKAGE / 'constants.py'

JPEG_MAGIC = b'\xff\xd8\xff'


def _fake_font(size: int = 24, weight: float = 630) -> ImageFont.ImageFont:
    """替代框架的 core_font：测试只关心能否出图，不关心字体。"""
    return ImageFont.load_default()


def _load_list_image_module() -> dict[str, Any]:
    """按 AST 加载 list_image.py，剥掉框架导入并注入假依赖（CI 里没有 gsuid_core）。"""
    tree = ast.parse(LIST_IMAGE_PATH.read_text(encoding='utf-8-sig'))
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.ImportFrom)
            and (node.level == 1 or (node.module or '').startswith('gsuid_core'))
        )
    ]
    globals_dict: dict[str, Any] = {
        'core_font': _fake_font,
        # 指向不存在的目录，渲染于是走渐变兜底，测试不依赖本机角色图
        'get_res_path': lambda name='': Path(tempfile.gettempdir()) / '__no_role_pile__',
    }
    exec(compile(tree, str(LIST_IMAGE_PATH), 'exec'), globals_dict)
    return globals_dict


def _constant(name: str) -> Any:
    tree = ast.parse(CONSTANTS_PATH.read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != name:
            continue
        return eval(compile(ast.Expression(node.value), '<constants>', 'eval'), {'__builtins__': {}}, {})
    raise AssertionError(f'常量 {name} 不存在')


def _extract_send_wife_list(items: list[tuple[int, str, str]], sent: list[Any]) -> Any:
    """提取 _send_wife_list，注入 fake 依赖后返回协程函数。"""
    tree = ast.parse(DAILY_PATH.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == '_send_wife_list'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)

    async def fake_items(ev: Any, mode: str) -> tuple[str, list[tuple[int, str, str]]]:
        return '今日老婆列表：', items

    async def fake_safe_send(bot: Any, message: Any) -> None:
        sent.append(message)

    async def fake_run_blocking(func: Any, *args: Any) -> Any:
        return func(*args)

    async def fake_image_message(data: bytes) -> str:
        return f'IMAGE:{len(data)}'

    globals_dict: dict[str, Any] = {
        'WIFE_LIST_IMAGE_THRESHOLD': _constant('WIFE_LIST_IMAGE_THRESHOLD'),
        'LOG_PREFIX': '[测试]',
        'logger': type('L', (), {'debug': staticmethod(lambda *a, **k: None)})(),
        '_wife_list_items': fake_items,
        '_wife_list_text_from_items': lambda title, rows: f'TEXT:{len(rows)}',
        '_safe_send': fake_safe_send,
        'run_blocking': fake_run_blocking,
        'render_wife_list_image': lambda title, rows: JPEG_MAGIC + b'fake',
        '_image_message': fake_image_message,
    }
    exec(compile(module, str(DAILY_PATH), 'exec'), globals_dict)
    return globals_dict['_send_wife_list']


class RenderWifeListImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_list_image_module()
        self.render = self.mod['render_wife_list_image']

    def test_renders_png_for_various_sizes(self) -> None:
        for count in (0, 1, 5, 6, 31):
            with self.subTest(count=count):
                items = [(i, f'群友{i}', f'角色{i}') for i in range(1, count + 1)]
                data = self.render('今日老婆列表：', items)
                self.assertTrue(data.startswith(JPEG_MAGIC), '必须输出合法图片')
                # 空列表只有标题，画布本就很小
                if count:
                    self.assertGreater(len(data), 1000)

    def test_long_names_do_not_break_rendering(self) -> None:
        items = [(1, '很长的群昵称' * 6, '今汐'), (2, '乙', '名字很长的角色' * 4)]
        self.assertTrue(self.render('今日老婆列表：', items).startswith(JPEG_MAGIC))

    def test_rows_are_single_column(self) -> None:
        """竖排单列：条目越多图越高、宽度基本不变。"""
        small = self.render('今日老婆列表：', [(i, f'群友{i}', f'角色{i}') for i in range(1, 4)])
        large = self.render('今日老婆列表：', [(i, f'群友{i}', f'角色{i}') for i in range(1, 40)])
        small_box = Image.open(io.BytesIO(small)).size
        large_box = Image.open(io.BytesIO(large)).size
        self.assertEqual(small_box[0], large_box[0], '单列时宽度不应随条数变化')
        self.assertGreater(large_box[1], small_box[1] * 5, '高度应随条数显著增长')

    def test_index_column_is_not_drawn_from_item_key(self) -> None:
        """序号必须按显示顺序重新编号。

        items 的首元素是排序用的时间戳，直接画出来会变成一串长数字。
        """
        src = LIST_IMAGE_PATH.read_text(encoding='utf-8')
        self.assertIn('enumerate(items, 1)', src)
        self.assertNotIn('str(index)', src)

    def test_long_item_key_does_not_widen_row(self) -> None:
        """首元素（时间戳）不参与排版，故不影响列宽。"""
        short_key = self.render('今日老婆列表：', [(1, '甲', '乙')])
        long_key = self.render('今日老婆列表：', [(1789001234, '甲', '乙')])
        self.assertEqual(
            Image.open(io.BytesIO(short_key)).size,
            Image.open(io.BytesIO(long_key)).size,
        )


class _FakeEvent:
    user_id = '123456'
    group_id = '654321'


class SendWifeListBranchTests(unittest.TestCase):
    def test_above_threshold_sends_image(self) -> None:
        sent: list[Any] = []
        items = [(i, f'群友{i}', f'角色{i}') for i in range(1, 7)]
        send = _extract_send_wife_list(items, sent)
        asyncio.run(send(object(), _FakeEvent()))
        self.assertEqual(len(sent), 1)
        self.assertTrue(str(sent[0]).startswith('IMAGE:'), f'应为图片，实际 {sent[0]}')

    def test_at_threshold_sends_text(self) -> None:
        sent: list[Any] = []
        items = [(i, f'群友{i}', f'角色{i}') for i in range(1, 6)]
        send = _extract_send_wife_list(items, sent)
        asyncio.run(send(object(), _FakeEvent()))
        self.assertEqual(len(sent), 1)
        self.assertEqual(str(sent[0]), 'TEXT:5')

    def test_empty_list_sends_text(self) -> None:
        sent: list[Any] = []
        send = _extract_send_wife_list([], sent)
        asyncio.run(send(object(), _FakeEvent()))
        self.assertEqual(str(sent[0]), 'TEXT:0')


class ThresholdConstantTests(unittest.TestCase):
    def test_threshold_is_five(self) -> None:
        self.assertEqual(_constant('WIFE_LIST_IMAGE_THRESHOLD'), 5)

    def test_old_forward_constant_is_gone(self) -> None:
        """旧常量已由渲染阈值取代，避免两套阈值并存。"""
        source = CONSTANTS_PATH.read_text(encoding='utf-8')
        self.assertNotIn('LIST_FORWARD_THRESHOLD', source)

    def test_daily_uses_image_threshold(self) -> None:
        source = DAILY_PATH.read_text(encoding='utf-8')
        self.assertIn('WIFE_LIST_IMAGE_THRESHOLD', source)
        self.assertIn('render_wife_list_image', source)
        self.assertNotIn('MessageSegment.node', source)


if __name__ == '__main__':
    unittest.main()
