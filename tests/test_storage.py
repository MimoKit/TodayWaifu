import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "twf" / "storage.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("todaywaifu_storage", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load storage module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StorageTests(unittest.TestCase):
    def test_atomic_write_preserves_unicode_and_schema(self) -> None:
        storage = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "daily_wife_data.json"
            payload = {"days": {"2026-07-11": {"bot:group": {"wives": {"1": {"name": "今汐"}}}}}}
            storage.atomic_write_json(path, payload)
            self.assertEqual(storage.read_json_dict(path), payload)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_read_invalid_json_returns_empty_dict(self) -> None:
        storage = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "daily_wife_data.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(storage.read_json_dict(path), {})

    def test_failed_replace_keeps_previous_file(self) -> None:
        storage = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "daily_wife_data.json"
            path.write_text('{"days":{"old":{}}}', encoding="utf-8")
            original_replace = storage.os.replace

            def fail_replace(source: Path, target: Path) -> None:
                raise OSError("replace failed")

            storage.os.replace = fail_replace
            try:
                with self.assertRaises(OSError):
                    storage.atomic_write_json(path, {"days": {"new": {}}})
            finally:
                storage.os.replace = original_replace
            self.assertEqual(path.read_text(encoding="utf-8"), '{"days":{"old":{}}}')
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_each_write_uses_a_unique_temporary_file(self) -> None:
        storage = _load_module()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "daily_wife_data.json"
            names: list[str] = []
            original_replace = storage.os.replace

            def record_replace(source: Path, target: Path) -> None:
                names.append(Path(source).name)
                original_replace(source, target)

            storage.os.replace = record_replace
            try:
                storage.atomic_write_json(path, {"days": {"first": {}}})
                storage.atomic_write_json(path, {"days": {"second": {}}})
            finally:
                storage.os.replace = original_replace
            self.assertEqual(len(names), 2)
            self.assertNotEqual(names[0], names[1])


if __name__ == "__main__":
    unittest.main()
