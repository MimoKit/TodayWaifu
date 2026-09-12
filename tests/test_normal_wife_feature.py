import ast
import asyncio
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
DAILY_PATH = ROOT / 'twf' / 'daily.py'
SHARED_PATH = ROOT / 'twf' / 'shared.py'
NORMAL_WIFE_PATH = ROOT / 'twf' / 'normal_wife.py'


def _extract_function(path: Path, name: str, globals_dict: dict[str, Any]):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    future = ast.ImportFrom(
        module='__future__',
        names=[ast.alias(name='annotations')],
        level=0,
    )
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), globals_dict)
    return globals_dict[name]


@dataclass
class _FakeRoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


@dataclass
class _FakeKindMetadata:
    text_template_key: str = 'DailyWifeTextTemplate'
    text_template_default: str = '你今天的老婆是{name}。'


class NormalWifeFeatureTests(unittest.IsolatedAsyncioTestCase):
    def test_build_text_when_normal_wife_disabled(self) -> None:
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_cfg_bool': lambda key, default=False: False,
            '_daily_kind_metadata': lambda mode: _FakeKindMetadata(),
            '_cfg': lambda key: None,
        }
        build_text = _extract_function(DAILY_PATH, '_build_text', globals_dict)
        role = _FakeRoleCandidate('秧秧', ('1201',), ('https://example.test/yangyang.png',))
        text = build_text(role, mode='wife')
        self.assertIn('秧秧', text)
        self.assertNotIn('你的老婆来啦！', text)

    def test_build_text_when_normal_wife_enabled(self) -> None:
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_cfg_bool': lambda key, default=False: key == 'DailyWifeNormalEnabled',
            '_daily_kind_metadata': lambda mode: _FakeKindMetadata(),
            '_cfg': lambda key: None,
        }
        build_text = _extract_function(DAILY_PATH, '_build_text', globals_dict)
        role = _FakeRoleCandidate('普通角色', ('9999',), ('https://example.test/normal.png',))
        text = build_text(role, mode='wife')
        self.assertEqual(text, '你的老婆来啦！')

    def test_filter_by_mode_preserves_candidates_when_normal_wife_enabled(self) -> None:
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_role_mode': lambda mode: mode,
            '_cfg_bool': lambda key, default=False: key == 'DailyWifeNormalEnabled',
            '_load_mode_role_map': lambda mode: {},
            '_load_custom_upload_role_map': lambda: {},
            '_normalize_role_name': lambda name: name,
        }
        filter_by_mode = _extract_function(SHARED_PATH, '_filter_by_mode', globals_dict)
        role = _FakeRoleCandidate('未知角色', ('unknown_id',), ('https://example.test/pic.png',))
        candidates = (role,)
        filtered = filter_by_mode(candidates, mode='wife')
        self.assertEqual(filtered, candidates)

    def test_filter_by_mode_filters_when_normal_wife_disabled(self) -> None:
        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            '_role_mode': lambda mode: mode,
            '_cfg_bool': lambda key, default=False: False,
            '_load_mode_role_map': lambda mode: {'1201': '秧秧'},
            '_load_custom_upload_role_map': lambda: {},
            '_normalize_role_name': lambda name: name,
        }
        filter_by_mode = _extract_function(SHARED_PATH, '_filter_by_mode', globals_dict)
        role1 = _FakeRoleCandidate('秧秧', ('1201',), ('https://example.test/1.png',))
        role2 = _FakeRoleCandidate('未知角色', ('9999',), ('https://example.test/2.png',))
        filtered = filter_by_mode((role1, role2), mode='wife')
        self.assertEqual(filtered, (role1,))

    def test_normal_gallery_api_url_default(self) -> None:
        globals_dict = {
            '_cfg': lambda key: '',
        }
        get_url = _extract_function(NORMAL_WIFE_PATH, '_normal_gallery_api_url', globals_dict)
        self.assertEqual(get_url(), 'https://ceshi.mimokit.dpdns.org/api/ceshi/roles')

    def test_normal_gallery_api_url_custom(self) -> None:
        globals_dict = {
            '_cfg': lambda key: 'https://custom.api.test/roles' if key == 'DailyWifeNormalGalleryApiUrl' else '',
        }
        get_url = _extract_function(NORMAL_WIFE_PATH, '_normal_gallery_api_url', globals_dict)
        self.assertEqual(get_url(), 'https://custom.api.test/roles')

    async def test_gallery_mode_supplements_local_images_for_missing_roles(self) -> None:
        role_map = {'1201': '秧秧', '9999': '折枝'}
        gallery_role = _FakeRoleCandidate('秧秧', ('1201',), ('https://gallery.test/yangyang.png',))
        local_role_1201 = _FakeRoleCandidate('秧秧', ('1201',), ('/local/yangyang.png',))
        local_role_9999 = _FakeRoleCandidate('折枝', ('9999',), ('/local/zhezhi.png',))

        from unittest.mock import MagicMock
        fake_logger = MagicMock()
        fake_time = MagicMock()
        fake_time.time.return_value = 1000.0

        globals_dict = {
            'RoleCandidate': _FakeRoleCandidate,
            'CANDIDATE_CACHE': {},
            'CACHE_TTL_SECONDS': 300,
            'LOG_PREFIX': '[测试]',
            'logger': fake_logger,
            'time': fake_time,
            'asyncio': asyncio,
            '_image_source': lambda: 'gallery',
            '_role_mode': lambda mode: mode,
            '_role_map_title': lambda mode: '老婆',
            '_load_mode_role_map': lambda mode: dict(role_map),
            '_load_custom_upload_candidates': lambda: (),
            '_fetch_gallery_payload_sync': lambda: {'roles': []},
            '_parse_role_candidates': lambda payload, mode, rmap: (gallery_role,),
            '_load_local_candidates': lambda mode: ((local_role_1201, local_role_9999), None),
            '_merge_role_candidates': lambda base, extra: base,
            '_normalize_role_name': lambda name: name,
        }
        load_uncached = _extract_function(SHARED_PATH, '_load_wuwa_candidates_uncached', globals_dict)
        candidates, err = await load_uncached('wife')
        self.assertIsNone(err)
        self.assertIsNotNone(candidates)
        # 秧秧应保留图库图片，折枝应补充为本地图片
        names = {c.name: c.images[0] for c in candidates}
        self.assertEqual(names['秧秧'], 'https://gallery.test/yangyang.png')
        self.assertEqual(names['折枝'], '/local/zhezhi.png')


if __name__ == '__main__':
    unittest.main()
