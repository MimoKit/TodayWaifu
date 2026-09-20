import ast
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SHOTA_PATH = ROOT / 'twf' / 'shota.py'


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

    def test_parses_same_shape_as_daily_wife_gallery(self) -> None:
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

    def test_parses_zt_role_id(self) -> None:
        payload = {
            'roles': [
                {
                    'role_ids': ['zt'],
                    'images': [
                        {'url': 'https://zt.mimokit.dpdns.org/shota/1.jpg'},
                    ],
                }
            ]
        }

        self.assertEqual(
            self.parse_urls(payload),
            ('https://zt.mimokit.dpdns.org/shota/1.jpg',),
        )

    def test_rejects_invalid_payloads(self) -> None:
        invalid_payloads = (
            {},
            {'roles': []},
            {'roles': [{'role_ids': ['shota']}]},
            {'roles': [{'role_ids': ['shota'], 'images': ['image.webp']}]},
            {
                'roles': [
                    {
                        'role_ids': ['shota'],
                        'images': [{'url': '/shota/image.webp'}],
                    }
                ]
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.parse_urls(payload)

    def test_selected_url_is_persisted_and_reused_by_interactions(self) -> None:
        shota_source = SHOTA_PATH.read_text(encoding='utf-8-sig')
        self.assertIn("role_ids=('shota',)", shota_source)
        self.assertIn('image=image_url', shota_source)
        self.assertIn("_daily_rng(ev, user_key, 'shota').choice", shota_source)


if __name__ == '__main__':
    unittest.main()
