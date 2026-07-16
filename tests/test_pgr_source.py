import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PgrFeatureSourceTests(unittest.TestCase):
    def test_pgr_config_and_command_are_registered(self) -> None:
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        source = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)

        self.assertIn("'DailyWifePgrEnabled'", config)
        self.assertIn("'DailyWifePgrGalleryPath'", config)
        self.assertIn("'DailyWifePgrTextTemplate'", config)
        self.assertTrue(
            any(
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'daily_pgr_wife'
                for node in tree.body
            )
        )
        self.assertIn("('今日战双老婆', 'jrzslp')", source)

    def test_pgr_uses_independent_daily_bucket_and_configurable_gallery(self) -> None:
        shared = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8-sig')
        metadata = (ROOT / 'twf' / 'kind_metadata.py').read_text(encoding='utf-8-sig')
        source = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn("context.setdefault('pgr_wives', {})", shared)
        self.assertIn('bucket="pgr_wives"', metadata)
        self.assertIn("_cfg('DailyWifePgrGalleryPath')", shared)
        self.assertIn("_daily_rng(ev, key, 'pgr_wife')", source)
        self.assertIn("Path(record.image).is_file()", source)
        self.assertIn("_get_other_daily_wife_name(ev, 'pgr')", source)
        self.assertIn('不要贪心！', source)

    def test_pgr_gallery_is_wired_into_mixed_mode(self) -> None:
        shared = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8-sig')

        self.assertIn("_cfg_bool('DailyWifeNteMixedEnabled', False)", shared)
        self.assertIn('_load_pgr_local_candidates()', shared)
        self.assertIn("_merge_role_candidates(candidates, pgr_candidates)", shared)

    def test_daily_wife_variants_share_an_exclusive_daily_choice(self) -> None:
        shared = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8-sig')
        daily = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8-sig')

        self.assertIn("DAILY_WIFE_KINDS = ('wife', 'nte', 'pgr')", shared)
        self.assertIn('_get_other_daily_wife_name(ev, mode)', daily)
        self.assertIn('不要贪心！', daily)

    def test_owner_debug_mode_bypasses_shared_daily_choice(self) -> None:
        daily_source = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8-sig')
        pgr_source = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')

        daily_function = ast.get_source_segment(
            daily_source,
            next(
                node
                for node in ast.parse(daily_source).body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == '_send_daily_wife'
            ),
        ) or ''
        pgr_function = ast.get_source_segment(
            pgr_source,
            next(
                node
                for node in ast.parse(pgr_source).body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == '_send_daily_pgr_wife'
            ),
        ) or ''

        for source in (daily_function, pgr_function):
            self.assertIn("_cfg_bool('DailyWifeDebugMode', False) and _is_master(ev)", source)
            self.assertLess(source.index('is_debug_active ='), source.index('_get_other_daily_wife_name('))
            self.assertIn('if not is_debug_active:', source)

    def test_pgr_owner_debug_draw_is_random_and_not_persisted(self) -> None:
        source = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')
        function = ast.get_source_segment(
            source,
            next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == '_send_daily_pgr_wife'
            ),
        ) or ''

        self.assertIn('if is_debug_active:', function)
        self.assertIn('_load_pgr_wife_candidates()', function)
        self.assertIn("_pick_nsfw_checked_role_record(candidates, random, 'pgr')", function)
        self.assertIn('else:\n        record = await _ensure_daily_pgr_wife_record(ev)', function)


if __name__ == '__main__':
    unittest.main()
