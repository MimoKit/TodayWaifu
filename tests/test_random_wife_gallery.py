import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'twf' / 'random_wife.py'


def _load_parser():
    source = MODULE.read_text(encoding='utf-8')
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == '_parse_random_gallery_candidates'
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(module='__future__', names=[ast.alias('annotations')], level=0),
            ast.ImportFrom(module='typing', names=[ast.alias('Any')], level=0),
            ast.parse(
                'class RoleCandidate:\n'
                '    def __init__(self, name, role_ids, images):\n'
                '        self.name = name\n'
                '        self.role_ids = role_ids\n'
                '        self.images = images\n'
            ).body[0],
            ast.ImportFrom(module='urllib.parse', names=[ast.alias('urlparse')], level=0),
            function,
        ],
        type_ignores=[],
    )
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(MODULE), 'exec'), namespace)
    return namespace['_parse_random_gallery_candidates']


class RandomWifeGalleryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parse = _load_parser()

    def test_parses_https_images_and_deduplicates_urls(self) -> None:
        candidates = self.parse(
            {
                'roles': [
                    {
                        'role_ids': ['ceshi'],
                        'name': '老婆',
                        'images': [
                            {'url': 'https://example.test/a.webp'},
                            {'url': 'https://example.test/a.webp'},
                            {'url': 'http://example.test/unsafe.webp'},
                        ],
                    }
                ]
            }
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, '老婆')
        self.assertEqual(candidates[0].role_ids, ('ceshi',))
        self.assertEqual(candidates[0].images, ('https://example.test/a.webp',))

    def test_uses_role_id_when_name_is_missing(self) -> None:
        candidates = self.parse(
            {
                'roles': [
                    {
                        'role_ids': ['ceshi'],
                        'images': [{'url': 'https://example.test/a.webp'}],
                    }
                ]
            }
        )
        self.assertEqual(candidates[0].name, 'ceshi')

    def test_rejects_empty_or_invalid_gallery(self) -> None:
        for payload in (
            {},
            {'roles': []},
            {'roles': [{'role_ids': ['ceshi'], 'images': []}]},
            {'roles': [{'role_ids': ['ceshi'], 'images': [{'url': '/local.webp'}]}]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):
                    self.parse(payload)

    def test_command_is_exact_and_does_not_use_daily_state(self) -> None:
        source = MODULE.read_text(encoding='utf-8')
        self.assertIn("random_wife_sv.on_fullmatch('来点老婆', block=True)", source)
        for forbidden in (
            '_load_wife_data',
            '_save_wife_data',
            '_get_today_context',
            '_daily_rng',
        ):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
