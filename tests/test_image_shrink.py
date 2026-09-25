import io
import ast
import unittest
from types import SimpleNamespace
from typing import Any
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def _load_shrink(limit_mb: int) -> Any:
    source = (ROOT / 'TodayWaifu' / 'senders.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    body = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_shrink_image_sync'
    ]
    namespace: dict[str, Any] = {
        'Any': Any,
        'io': io,
        'Path': Path,
        'Image': Image,
        'LOG_PREFIX': '[test]',
        'logger': SimpleNamespace(info=lambda *args, **kwargs: None),
        '_cfg': lambda key: limit_mb,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), 'shrink', 'exec'), namespace)
    return namespace['_shrink_image_sync']


def _noise_image(side: int) -> bytes:
    image = Image.effect_noise((side, side), 100).convert('RGB')
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=95)
    return buffer.getvalue()


class ImageShrinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.limit_mb = 2
        self.shrink = _load_shrink(self.limit_mb)

    def test_oversized_bytes_are_compressed(self) -> None:
        raw = _noise_image(2000)
        self.assertGreater(len(raw), self.limit_mb * 1024 * 1024)

        result = self.shrink(raw)

        self.assertIsInstance(result, bytes)
        self.assertLessEqual(len(result), self.limit_mb * 1024 * 1024)
        with Image.open(io.BytesIO(result)) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertLessEqual(max(image.size), 1920)

    def test_broken_payload_is_returned_untouched(self) -> None:
        raw = b'not an image at all' * 200000
        self.assertEqual(self.shrink(raw), raw)

    def test_animated_gif_is_returned_untouched(self) -> None:
        frames = [Image.new('P', (600, 600), index) for index in range(2)]
        buffer = io.BytesIO()
        frames[0].save(buffer, save_all=True, append_images=frames[1:], format='GIF')
        raw = buffer.getvalue() * 400
        self.assertEqual(self.shrink(raw), raw)

    def test_zero_threshold_disables_compression(self) -> None:
        shrink = _load_shrink(0)
        raw = _noise_image(800)
        self.assertEqual(shrink(raw), raw)

    def test_threshold_boundary_keeps_small_payload(self) -> None:
        raw = _noise_image(200)
        self.assertLessEqual(len(raw), self.limit_mb * 1024 * 1024)
        self.assertEqual(self.shrink(raw), raw)


if __name__ == '__main__':
    unittest.main()
