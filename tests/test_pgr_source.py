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
        self.assertIn("'DailyWifeImageUploadWhitelist'", config)
        self.assertTrue(
            any(
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'daily_pgr_wife'
                for node in tree.body
            )
        )
        self.assertTrue(
            any(
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'daily_pgr_wife_prefix'
                for node in tree.body
            )
        )
        self.assertIn("('今日战双老婆', 'jrzslp')", source)
        self.assertIn("('上传战双老婆图片', '战双老婆上传图片')", source)

    def test_all_image_uploads_share_global_whitelist(self) -> None:
        shared = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8-sig')
        custom = (ROOT / 'twf' / 'custom_role.py').read_text(encoding='utf-8-sig')
        loli = (ROOT / 'twf' / 'loli.py').read_text(encoding='utf-8-sig')
        pgr = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn("image_upload_sv = SV('今日老婆-图片上传'", shared)
        self.assertIn("_cfg('DailyWifeImageUploadWhitelist')", shared)
        for source in (custom, loli, pgr):
            self.assertIn('if not _can_upload_images(ev):', source)
            self.assertIn('@image_upload_sv.', source)

    def test_pgr_upload_requires_existing_role_directory(self) -> None:
        source = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn('find_named_role_directory(_pgr_wife_root(), role_name)', source)
        self.assertIn('不存在角色文件夹', source)
        self.assertNotIn('role_dir.mkdir(', source)

    def test_pgr_uses_independent_daily_bucket_and_configurable_gallery(self) -> None:
        shared = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8-sig')
        metadata = (ROOT / 'twf' / 'kind_metadata.py').read_text(encoding='utf-8-sig')
        source = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn("context.setdefault('pgr_wives', {})", shared)
        self.assertIn('bucket="pgr_wives"', metadata)
        self.assertIn("_cfg('DailyWifePgrGalleryPath')", shared)
        self.assertIn("_daily_rng(ev, key, 'pgr_wife')", source)
        self.assertIn("Path(record.image).is_file()", source)
        self.assertIn("_wife_state(existing_raw) != 'owned'", source)
        self.assertIn('写入前发现已离手的战双老婆记录，拒绝覆盖', source)
        self.assertIn("_get_other_daily_wife_name(ev, 'pgr')", source)
        self.assertIn('不要贪心！', source)

    def test_pgr_gallery_is_wired_into_mixed_mode(self) -> None:
        shared = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8-sig')
        daily = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8-sig')

        self.assertIn("_cfg_bool('DailyWifeNteMixedEnabled', False)", shared)
        self.assertIn('_load_pgr_local_candidates()', shared)
        self.assertIn("pools.append(('pgr', pgr_candidates))", shared)
        self.assertIn('source, candidates = rng.choice(pools)', daily)
        self.assertNotIn('for source, candidates in pool_order:', daily)
        self.assertIn('_pick_mixed_wife_record(pools, rng, key)', daily)

    def test_owner_debug_random_draw_uses_the_same_game_pool_logic(self) -> None:
        source = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8-sig')
        function = ast.get_source_segment(
            source,
            next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == '_send_daily_wife'
            ),
        ) or ''

        self.assertIn("if mode == 'wife' and not specified_name", function)
        self.assertIn('_load_wife_candidate_pools()', function)
        self.assertIn(
            "record = await _pick_mixed_wife_record(\n                pools,\n                random,",
            function,
        )

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
            self.assertIn("is_master = _is_master(ev)", source)
            self.assertIn("_cfg_bool('DailyWifeDebugMode', False) and is_master", source)
            self.assertLess(source.index('is_debug_active ='), source.index('_get_other_daily_wife_name('))
            self.assertIn('if not is_transient_draw:', source)

    def test_owner_role_selection_is_independent_from_debug_mode(self) -> None:
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        daily = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8-sig')
        pgr = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn("'_DividerAssignWife': GsDivider('分配老婆', '')", config)
        self.assertIn("'DailyWifeOwnerSpecifyEnabled'", config)
        for source in (daily, pgr):
            self.assertIn("_cfg_bool('DailyWifeOwnerSpecifyEnabled', False)", source)
            self.assertIn('is_transient_draw = is_debug_active or bool(specified_name)', source)

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

        self.assertIn('if is_transient_draw:', function)
        self.assertIn('_load_pgr_wife_candidates()', function)
        self.assertIn('_pgr_candidates_by_name(candidates, specified_name)', function)
        self.assertIn('只有开启“主人指定老婆”后', function)
        self.assertIn('战双老婆图库中没有角色', function)
        self.assertIn("_pick_nsfw_checked_role_record(candidates, random, 'pgr')", function)
        self.assertIn('else:\n        record = await _ensure_daily_pgr_wife_record(ev)', function)

    def test_pgr_prefix_passes_specified_name(self) -> None:
        source = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')
        function = ast.get_source_segment(
            source,
            next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.AsyncFunctionDef)
                and node.name == 'daily_pgr_wife_prefix'
            ),
        ) or ''

        self.assertIn("str(ev.text or '').strip()", function)

    def test_pgr_specified_name_uses_normalized_exact_match(self) -> None:
        source = (ROOT / 'twf' / 'pgr.py').read_text(encoding='utf-8-sig')
        function = ast.get_source_segment(
            source,
            next(
                node
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef)
                and node.name == '_pgr_candidates_by_name'
            ),
        ) or ''

        self.assertIn('_normalize_role_name(specified_name).casefold()', function)
        self.assertIn('_normalize_role_name(candidate.name).casefold() == target', function)


if __name__ == '__main__':
    unittest.main()
