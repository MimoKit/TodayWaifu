import ast
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SHOTA_PATH = ROOT / 'TodayWaifu' / 'shota.py'


def _extract_function(name: str, globals_dict: dict[str, Any]) -> Any:
    tree = ast.parse(SHOTA_PATH.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(SHOTA_PATH), 'exec'), globals_dict)
    return globals_dict[name]


class ShotaGalleryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parse_urls = _extract_function(
            '_parse_shota_image_urls',
            {'Any': Any},
        )
        self.normalize_url = _extract_function(
            '_normalize_shota_api_url',
            {},
        )

    def test_parses_shota_gallery_payload(self) -> None:
        payload = {
            'roles': [
                {
                    'role_ids': ['shota'],
                    'images': [
                        {'url': 'https://zt.mimokit.dpdns.org/shota/1.jpg'},
                        {'url': 'https://zt.mimokit.dpdns.org/shota/2.jpg'},
                        {'url': 'https://zt.mimokit.dpdns.org/shota/1.jpg'},
                    ],
                }
            ]
        }

        self.assertEqual(
            self.parse_urls(payload),
            (
                'https://zt.mimokit.dpdns.org/shota/1.jpg',
                'https://zt.mimokit.dpdns.org/shota/2.jpg',
            ),
        )

    def test_normalize_api_url(self) -> None:
        self.assertEqual(
            self.normalize_url('zt.mimokit.dpdns.org'),
            'https://zt.mimokit.dpdns.org',
        )
        self.assertEqual(
            self.normalize_url('http://zt.mimokit.dpdns.org'),
            'http://zt.mimokit.dpdns.org',
        )
        self.assertEqual(
            self.normalize_url('https://zt.mimokit.dpdns.org'),
            'https://zt.mimokit.dpdns.org',
        )
        self.assertEqual(self.normalize_url(''), '')

    def test_rejects_empty_or_malformed_payloads(self) -> None:
        invalid_payloads = (
            {},
            {'roles': []},
            {'roles': [{'role_ids': ['shota']}]},
            {'roles': [{'role_ids': ['shota'], 'images': []}]},
            {'roles': [{'role_ids': ['shota'], 'images': [{'url': 'invalid-url'}]}]},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.parse_urls(payload)

    def test_shota_source_code_contract(self) -> None:
        shota_source = SHOTA_PATH.read_text(encoding='utf-8-sig')
        rob_source = (ROOT / 'TodayWaifu' / 'rob.py').read_text(encoding='utf-8-sig')
        gift_source = (ROOT / 'TodayWaifu' / 'gift.py').read_text(encoding='utf-8-sig')
        divorce_source = (ROOT / 'TodayWaifu' / 'divorce.py').read_text(encoding='utf-8-sig')

        self.assertIn("role_ids=('shota',)", shota_source)
        self.assertIn("record_type='shota'", shota_source)
        self.assertIn("_daily_rng(ev, user_key, 'shota').choice", shota_source)
        self.assertIn("@shota_sv.on_fullmatch", shota_source)
        self.assertIn('今日正太', shota_source)
        self.assertIn('你今天的正太来啦！', shota_source)

        # Rob / Gift / Divorce support
        self.assertIn('_send_rob_shota', rob_source)
        self.assertIn('_send_gift_shota', gift_source)
        self.assertIn('divorce_shota', divorce_source)


if __name__ == '__main__':
    unittest.main()
