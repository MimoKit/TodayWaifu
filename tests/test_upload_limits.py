"""上传图片的接收阈值：上传者是主人/白名单，应比图库路径宽松，但仍须有上限。

背景：2026-09-27 一次「上传战双老婆图片 赛琳娜」6 张图里 3 张失败——1 张 10MB 撞
UPLOAD_IMAGE_MAX_BYTES（当时 10MB），2 张超宽壁纸（13670x7215 / 8681x4134）撞
MAX_IMAGE_PIXELS（1600 万）。这里锁定「上传放宽、其他路径保持严格」的契约。
"""

import tempfile
import unittest
import importlib.util
from types import ModuleType
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'TodayWaifu'
IMAGE_INPUT = PACKAGE / 'image_input.py'

# 该模块只依赖 Pillow 与标准库，可独立加载，无需拉起整个包
UPLOAD_BYTES = 32 * 1024 * 1024


def _load_image_input() -> ModuleType:
    spec = importlib.util.spec_from_file_location('twf_image_input_under_test', IMAGE_INPUT)
    if spec is None or spec.loader is None:
        raise AssertionError('无法加载 image_input 模块')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UploadLimitContractTests(unittest.TestCase):
    def test_upload_pixel_limit_is_laxer_than_the_strict_one(self) -> None:
        module = _load_image_input()
        self.assertGreater(
            module.UPLOAD_IMAGE_MAX_PIXELS,
            module.MAX_IMAGE_PIXELS,
            '上传上限必须比图库/发送路径更宽松，否则超宽壁纸照样传不上来',
        )

    def test_upload_byte_limit_is_raised(self) -> None:
        constants = (PACKAGE / 'constants.py').read_text(encoding='utf-8')
        self.assertIn('UPLOAD_IMAGE_MAX_BYTES = 32 * 1024 * 1024', constants)

    def test_every_upload_entrypoint_passes_the_upload_limits(self) -> None:
        for name in ('custom_role.py', 'loli.py', 'pgr.py'):
            source = (PACKAGE / name).read_text(encoding='utf-8')
            self.assertIn('UPLOAD_IMAGE_MAX_PIXELS', source, f'{name} 未使用上传专用像素上限')
            self.assertIn('read_image_bytes(', source, name)

    def test_oversized_pixels_pass_only_with_the_upload_limit(self) -> None:
        """2000 万像素：默认（图库路径）拒绝，带上传上限通过。"""
        module = _load_image_input()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'wide.jpg'
            Image.new('RGB', (5000, 4000), (12, 34, 56)).save(path, 'JPEG', quality=85)
            self.assertIsNone(
                module.read_image_bytes(str(path), UPLOAD_BYTES),
                '默认参数必须保持严格，图库/发送路径不能被放宽',
            )
            allowed = module.read_image_bytes(str(path), UPLOAD_BYTES, module.UPLOAD_IMAGE_MAX_PIXELS)
            self.assertIsNotNone(allowed, '上传路径应接受超宽幅面')
            self.assertEqual(allowed[1], '.jpg')

    def test_limit_still_blocks_an_insane_pixel_count(self) -> None:
        """放宽不等于取消：仍然要拦住远超上限的尺寸（防解压炸弹）。"""
        module = _load_image_input()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'huge.jpg'
            Image.new('RGB', (12000, 12001), (12, 34, 56)).save(path, 'JPEG', quality=60)
            self.assertIsNone(
                module.read_image_bytes(str(path), UPLOAD_BYTES, module.UPLOAD_IMAGE_MAX_PIXELS),
                '1.44 亿像素超过上传上限，必须拒绝',
            )


if __name__ == '__main__':
    unittest.main()
