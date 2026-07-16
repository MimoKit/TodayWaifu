import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'twf' / 'folder_gallery.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('todaywaifu_folder_gallery', MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load folder_gallery module')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FolderGalleryTests(unittest.TestCase):
    def test_scans_role_folders_recursively_and_ignores_other_files(self) -> None:
        gallery = _load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'pgr_wife'
            (root / '露西亚' / '皮肤').mkdir(parents=True)
            (root / '露西亚' / 'a.PNG').write_bytes(b'a')
            (root / '露西亚' / '皮肤' / 'b.jpg').write_bytes(b'b')
            (root / '露西亚' / 'note.txt').write_text('ignore', encoding='utf-8')
            (root / '.cache').mkdir()
            (root / '.cache' / 'hidden.png').write_bytes(b'c')

            rows = gallery.scan_named_role_directories(root, {'.png', '.jpg'})

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], '露西亚')
            self.assertEqual(len(rows[0][1]), 2)
            self.assertTrue(all(Path(path).is_absolute() for path in rows[0][1]))

    def test_creates_missing_root_and_returns_empty_gallery(self) -> None:
        gallery = _load_module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'missing'
            self.assertEqual(gallery.scan_named_role_directories(root, {'.png'}), ())
            self.assertTrue(root.is_dir())


if __name__ == '__main__':
    unittest.main()
