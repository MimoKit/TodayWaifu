import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'twf' / 'folder_gallery.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('todaywaifu_folder_gallery', MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load folder_gallery module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FolderGalleryTests(unittest.TestCase):
    def test_creates_root_and_uses_folder_name_as_role_name(self) -> None:
        gallery = _load_module()
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'pgr_wife'
            self.assertEqual(gallery.scan_named_role_directories(root, {'.png'}), ())
            self.assertTrue(root.is_dir())

            role_dir = root / '赛琳娜'
            role_dir.mkdir()
            (role_dir / '立绘.png').write_bytes(b'image')
            (role_dir / '说明.txt').write_text('ignore', encoding='utf-8')

            rows = gallery.scan_named_role_directories(root, {'.png'})
            self.assertEqual(rows[0][0], '赛琳娜')
            self.assertEqual(rows[0][1], (str(role_dir / '立绘.png'),))

    def test_ignores_empty_role_directories(self) -> None:
        gallery = _load_module()
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'pgr_wife'
            (root / '露西亚').mkdir(parents=True)
            self.assertEqual(gallery.scan_named_role_directories(root, {'.jpg'}), ())


if __name__ == '__main__':
    unittest.main()
