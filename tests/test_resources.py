import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "ICON.png": ((1024, 1024), "RGBA"),
    "fb93f5370f556a51db172863420aa50e.png": ((1440, 730), "RGB"),
    "texture2d/bg.jpg": ((1283, 1919), "RGB"),
}


class ResourceIntegrityTests(unittest.TestCase):
    def test_help_resources_remain_readable_with_compatible_dimensions(self) -> None:
        for relative, (size, mode) in EXPECTED.items():
            path = ROOT / relative
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                self.assertEqual(image.size, size, relative)
                self.assertEqual(image.mode, mode, relative)


if __name__ == "__main__":
    unittest.main()
