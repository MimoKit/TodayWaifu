import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "twf" / "image_input.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("todaywaifu_image_input", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load image_input module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImageInputTests(unittest.TestCase):
    def test_collect_image_refs_deduplicates_in_original_order(self) -> None:
        image_input = _load_module()
        event = SimpleNamespace(
            content=[SimpleNamespace(type="image", data=" a "), SimpleNamespace(type="text", data="x")],
            image_list=["a", "b"],
            image="c",
        )
        self.assertEqual(image_input.collect_image_refs(event), ("a", "b", "c"))

    def test_detect_image_suffix_prefers_file_signature(self) -> None:
        image_input = _load_module()
        self.assertEqual(image_input.detect_image_suffix(b"\x89PNG\r\n\x1a\nrest", "fake.jpg"), ".png")
        self.assertEqual(image_input.detect_image_suffix(b"RIFFxxxxWEBPrest", "fake.jpg"), ".webp")

    def test_read_image_bytes_supports_base64_and_size_limit(self) -> None:
        image_input = _load_module()
        encoded = "base64://iVBORw0KGgpyZXN0"
        self.assertEqual(image_input.read_image_bytes(encoded, 1024), (b"\x89PNG\r\n\x1a\nrest", ".png"))
        self.assertIsNone(image_input.read_image_bytes(encoded, 4))

    def test_image_hash_id_uses_filename_for_backward_compatibility(self) -> None:
        image_input = _load_module()
        self.assertEqual(image_input.image_hash_id(Path("/tmp/example.png")), "ca75a0b8")


if __name__ == "__main__":
    unittest.main()
