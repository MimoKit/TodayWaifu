import ast
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOLI_PATH = ROOT / 'twf' / 'loli.py'


def _extract_function(name: str, globals_dict: dict[str, Any]) -> Any:
    tree = ast.parse(LOLI_PATH.read_text(encoding='utf-8-sig'))
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
    exec(compile(module, str(LOLI_PATH), 'exec'), globals_dict)
    return globals_dict[name]


class LoliGalleryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parse_urls = _extract_function(
            '_parse_loli_image_urls',
            {'Any': Any},
        )

    def test_parses_same_shape_as_daily_wife_gallery(self) -> None:
        payload = {
            'roles': [
                {
                    'role_ids': ['loli'],
                    'images': [
                        {'url': 'https://img.example/loli/first.webp'},
                        {'url': 'https://img.example/loli/second.webp'},
                        {'url': 'https://img.example/loli/first.webp'},
                    ],
                }
            ]
        }

        self.assertEqual(
            self.parse_urls(payload),
            (
                'https://img.example/loli/first.webp',
                'https://img.example/loli/second.webp',
            ),
        )

    def test_rejects_old_direct_image_or_incomplete_payloads(self) -> None:
        invalid_payloads = (
            {},
            {'roles': []},
            {'roles': [{'role_ids': ['loli']}]},
            {'roles': [{'role_ids': ['loli'], 'images': ['image.webp']}]},
            {
                'roles': [
                    {
                        'role_ids': ['loli'],
                        'images': [{'url': '/loli/image.webp'}],
                    }
                ]
            },
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.parse_urls(payload)

    def test_selected_url_is_persisted_and_reused_by_interactions(self) -> None:
        loli_source = LOLI_PATH.read_text(encoding='utf-8-sig')
        rob_source = (ROOT / 'twf' / 'rob.py').read_text(encoding='utf-8-sig')
        self.assertIn("role_ids=('loli',)", loli_source)
        self.assertIn('image=image_url', loli_source)
        self.assertNotIn('image=custom_url', loli_source)
        self.assertIn("_daily_rng(ev, user_key, 'loli').choice", loli_source)
        self.assertIn('target_record.image,', rob_source)


if __name__ == '__main__':
    unittest.main()
