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


if __name__ == '__main__':
    unittest.main()
