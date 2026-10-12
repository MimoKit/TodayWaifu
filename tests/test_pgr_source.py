# 战双（PGR）老婆功能的源码级契约：配置键、命令注册、图库接线与权限入口。
#
# 战双与鸣潮、异环共享「每日只能有一位老婆」的互斥语义，但拥有独立的记录桶、随机盐与
# 图库来源。历史故障集中在这类「复制一份鸣潮实现」的分支上：1edf2c4 发现战双自行拼接文案，
# 既未调用 get_role_quote 也无署名行，导致战双老婆永远看不到台词；d7461ab 又发现记录桶名
# 存在第二份真相源。这些断言以源码文本与语法树为对象，锁定各分支不得退回私有实现。
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PgrFeatureSourceTests(unittest.TestCase):
    def test_pgr_config_and_command_are_registered(self) -> None:
        # 配置键与命令词必须成对存在：只注册命令而无配置项则该功能无法关闭，
        # 只写配置项而无命令则控制台出现无效开关。别名（'jrzslp' 等）同样属契约，
        # 用户已习惯的简写一旦移除等同改坏指令。
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        source = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)

        self.assertIn("'DailyWifePgrEnabled'", config)
        self.assertIn("'DailyWifePgrGalleryPath'", config)
        # 战双图库地址已并入统一的 DailyWifeApiUrl
        self.assertIn("'DailyWifeApiUrl'", config)
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

    def test_pgr_remote_gallery_is_used_with_local_fallback(self) -> None:
        # 远端图库为主、本地目录为兜底：远端不可用时必须回落本地而非返回空候选，
        # 否则自建或离线部署的战双功能会整体失效。断言本地回落分支逐字存在，
        # 是因为该分支可被删除而所有导入与类型检查照常通过。
        gallery = (ROOT / 'TodayWaifu' / 'gallery.py').read_text(encoding='utf-8-sig')
        source = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn('async def _load_pgr_wife_candidates()', gallery)
        # 战双图库地址已并入统一的 DailyWifeApiUrl，旧键 DailyWifePgrGalleryApiUrl 已删除
        self.assertIn("_cfg('DailyWifeApiUrl')", gallery)
        self.assertIn('_parse_pgr_gallery_candidates(payload)', gallery)
        self.assertIn('return await run_blocking(_load_pgr_local_candidates)', gallery)
        self.assertIn('await _load_pgr_wife_candidates()', source)
        self.assertIn("record.image.startswith(('http://', 'https://'))", source)
        self.assertIn('await _send_role_image(', source)

    def test_all_image_uploads_share_global_whitelist(self) -> None:
        # 三条上传链路（自定义、萝莉、战双）必须共用同一白名单与同一服务权限门：
        # 任一处漏掉门禁，都会让未授权用户经该入口写入服务器文件，
        # 且因入口分散而难以在日常使用中察觉。
        shared = (ROOT / 'TodayWaifu' / 'shared.py').read_text(encoding='utf-8-sig')
        custom = (ROOT / 'TodayWaifu' / 'custom_role.py').read_text(encoding='utf-8-sig')
        loli = (ROOT / 'TodayWaifu' / 'loli.py').read_text(encoding='utf-8-sig')
        pgr = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn("image_upload_sv = SV('今日老婆-图片上传'", shared)
        self.assertIn("_cfg('DailyWifeImageUploadWhitelist')", shared)
        for source in (custom, loli, pgr):
            self.assertIn('if not _can_upload_images(ev):', source)
            self.assertIn('@image_upload_sv.', source)

    def test_pgr_upload_requires_existing_role_directory(self) -> None:
        # 上传只允许写入已存在的角色目录，且禁止在此处 mkdir：允许按用户输入创建目录，
        # 等于把角色名当作路径片段交给文件系统，存在路径穿越与目录膨胀两类风险；
        # 先校验存在性则把可写范围限定在既有目录内。
        source = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn('find_named_role_directory(_pgr_wife_root(), role_name)', source)
        self.assertIn('不存在角色文件夹', source)
        self.assertNotIn('role_dir.mkdir(', source)

    def test_pgr_uses_independent_daily_bucket_and_configurable_gallery(self) -> None:
        # 战双必须使用独立记录桶与独立随机盐：共用鸣潮的桶或盐会让两个功能互相覆盖记录，
        # 并使同一天的抽取结果在两条命令间耦合。同时锁定「已离手记录不得覆盖」这一状态
        # 校验——它是离婚后重抽能生效、且不会复用旧记录的关键。
        #
        # shared 已按职责拆分，故扫描整个 TodayWaifu 包而非单文件。
        shared = '\n'.join(
            path.read_text(encoding='utf-8-sig') for path in sorted((ROOT / 'TodayWaifu').glob('*.py'))
        )
        metadata = (ROOT / 'TodayWaifu' / 'kind_metadata.py').read_text(encoding='utf-8-sig')
        source = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn("for kind in ALL_DAILY_RECORD_KINDS", shared)
        self.assertIn("context.setdefault(_daily_bucket_name(kind), {})", shared)
        self.assertIn('bucket="pgr_wives"', metadata)
        self.assertIn("_cfg('DailyWifePgrGalleryPath')", shared)
        self.assertIn("_daily_rng(ev, key, 'pgr_wife')", source)
        self.assertIn("Path(record.image).is_file()", source)
        self.assertIn("_wife_state(existing_raw) != 'owned'", source)
        self.assertIn('写入前发现已离手的战双老婆记录，拒绝覆盖', source)
        self.assertIn("_get_other_daily_wife_name(ev, 'pgr')", source)
        self.assertIn('不要贪心！', source)

    def test_daily_wife_variants_share_an_exclusive_daily_choice(self) -> None:
        # 三个游戏共用同一条互斥规则：用户当天已在任一模式获得老婆，另一模式必须提示
        # 「不要贪心」而非再发一位。该提示属用户可见文案，不得改写。
        shared = '\n'.join(
            path.read_text(encoding='utf-8-sig') for path in sorted((ROOT / 'TodayWaifu').glob('*.py'))
        )
        daily = (ROOT / 'TodayWaifu' / 'daily.py').read_text(encoding='utf-8-sig')

        self.assertIn("DAILY_WIFE_KINDS = ('wife', 'nte', 'pgr')", shared)
        self.assertIn('_get_other_daily_wife_name(ev, mode)', daily)
        self.assertIn('不要贪心！', daily)

    def test_owner_debug_mode_bypasses_shared_daily_choice(self) -> None:
        # 调试模式仅对主人生效（is_master 与配置项必须同时满足），且随机源的初始化顺序
        # 必须先于互斥查询：顺序颠倒会让调试预览提前消耗随机数或被互斥逻辑拦下，
        # 表现为主人调试时复现不出线上抽取结果。
        daily_source = (ROOT / 'TodayWaifu' / 'daily.py').read_text(encoding='utf-8-sig')
        pgr_source = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')

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

    def test_role_selection_has_independent_sv_and_whitelist(self) -> None:
        # 指定老婆使用独立服务与白名单，与图片上传权限分离：两者混用会迫使只想开放指定
        # 的部署同时开放文件写入。指定结果同样落每日记录（不再只做临时预览、0 点随记录
        # 重置），故 is_transient_draw 只允许在调试路径为真。
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        shared = (ROOT / 'TodayWaifu' / 'shared.py').read_text(encoding='utf-8-sig')
        daily = (ROOT / 'TodayWaifu' / 'daily.py').read_text(encoding='utf-8-sig')
        pgr = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')

        self.assertIn("'_DividerAssignWife': GsDivider('分配老婆', '')", config)
        self.assertIn("'DailyWifeSpecifyWhitelist'", config)
        self.assertIn("specify_wife_sv = SV('今日老婆-指定老婆'", shared)
        self.assertIn("_cfg('DailyWifeSpecifyWhitelist')", shared)
        self.assertIn('def _can_specify_wife(ev: Event) -> bool:', shared)
        for source in (daily, pgr):
            self.assertIn('can_specify_role = _can_specify_wife(ev)', source)
            # 主人指定不再走临时预览：与普通抽取一样写入每日记录，0 点随记录重置
            self.assertIn('is_transient_draw = is_debug_active', source)
            self.assertIn('specified_role=specified_role', source)
        self.assertIn('@specify_wife_sv.on_prefix(', daily)
        self.assertIn('@specify_wife_sv.on_prefix(', pgr)

    def test_pgr_owner_debug_draw_is_random_and_not_persisted(self) -> None:
        # 调试抽取必须走随机挑选且不写库，同时保留指定名字的精确匹配入口：
        # 若误用 _ensure_daily_pgr_wife_record，主人每次调试都会占掉当天的唯一名额。
        source = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')
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
        self.assertIn('只有机器人主人或指定老婆白名单用户', function)
        self.assertIn('战双老婆图库中没有角色', function)
        self.assertIn('_pick_role_record(candidates, random)', function)
        self.assertIn(
            'else:\n        record = await _ensure_daily_pgr_wife_record(ev, specified_role=specified_role)',
            function,
        )

    def test_pgr_prefix_passes_specified_name(self) -> None:
        # 带参前缀命令把原文交给指定匹配：参数解析若在此处截断或改写，
        # 含空格与特殊字符的角色名将永远匹配不到。
        source = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')
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
        # 指定名字必须经规范化 + casefold 后整体相等比较，而非逐字比较或子串包含：
        # 不同图库来源对间隔号等写法不一致，逐字比较会把同一角色判成未命中；
        # 而包含匹配会让「心」命中「鉴心」（b915108 修正的正是台词库同类问题）。
        source = (ROOT / 'TodayWaifu' / 'pgr.py').read_text(encoding='utf-8-sig')
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
