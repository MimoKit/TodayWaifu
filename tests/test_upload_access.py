import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'twf' / 'upload_access.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('todaywaifu_upload_access', MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load upload_access module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UploadAccessTests(unittest.TestCase):
    def test_whitelisted_user_can_upload(self) -> None:
        access = _load_module()
        self.assertTrue(access.can_upload_images('10001', [], ['10001']))

    def test_master_bypasses_empty_whitelist(self) -> None:
        access = _load_module()
        self.assertTrue(access.can_upload_images('10001', ['10001'], []))

    def test_unlisted_user_cannot_upload(self) -> None:
        access = _load_module()
        self.assertFalse(access.can_upload_images('10001', ['20002'], ['30003']))

    def test_string_whitelist_accepts_common_separators(self) -> None:
        access = _load_module()
        self.assertTrue(access.can_upload_images('10002', [], '10001, 10002\n10003'))


if __name__ == '__main__':
    unittest.main()
