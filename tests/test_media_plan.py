"""QQ 素材图床的配额与复用判定。

素材管理的正确性全压在这几个纯函数上：单机器人 100 张、按「最后一次被引用」滑动过期
90 天。判错「能不能复用」会让引用失效（用户看到空图），判错「该删哪几张」会删掉仍在
用的素材、或在上传时被平台以 40035007 拒绝。

被测模块不依赖 gsuid_core 与包内其它模块，故用 importlib 按文件路径独立加载。
"""
import unittest
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'TodayWaifu' / 'media_plan.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('todaywaifu_media_plan', MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load media_plan module')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntParsingTests(unittest.TestCase):
    def test_accepts_config_and_api_shapes(self) -> None:
        plan = _load_module()
        # WebConsole 的整数项可能是 int / 字符串 / 浮点；凭证接口的 expires_in 文档里
        # 就写成字符串 "7200"，统一在这里收口。
        self.assertEqual(plan.as_int(10, 0), 10)
        self.assertEqual(plan.as_int('10', 0), 10)
        self.assertEqual(plan.as_int(' 10 ', 0), 10)
        self.assertEqual(plan.as_int(10.9, 0), 10)
        self.assertEqual(plan.as_int(True, 0), 1)
        self.assertEqual(plan.as_int(None, 7), 7)
        self.assertEqual(plan.as_int('abc', 7), 7)
        self.assertEqual(plan.as_int([], 7), 7)


class QuotaTests(unittest.TestCase):
    def test_quota_is_capped_by_platform_limit(self) -> None:
        # 平台硬限 100：配置写 500 也只能按 100 算，否则清理永远不会触发。
        plan = _load_module()
        self.assertEqual(plan.clamp_quota(500, 10), (100, 10))
        self.assertEqual(plan.clamp_quota(100, 10), (100, 10))
        self.assertEqual(plan.clamp_quota('50', '5'), (50, 5))

    def test_reserve_never_eats_the_whole_quota(self) -> None:
        # 预留 ≥ 上限时若不收敛，可用位置为 0，上传必然失败。
        plan = _load_module()
        self.assertEqual(plan.clamp_quota(100, 200), (100, 99))
        self.assertEqual(plan.clamp_quota(100, 100), (100, 99))
        self.assertEqual(plan.clamp_quota(100, -5), (100, 0))

    def test_invalid_quota_falls_back_to_platform_limit(self) -> None:
        plan = _load_module()
        self.assertEqual(plan.clamp_quota('abc', 'abc'), (100, 0))
        self.assertEqual(plan.clamp_quota(0, 0), (1, 0))


class ReuseTests(unittest.TestCase):
    NOW = int(datetime(2026, 9, 24, 12, 0, 0).timestamp())

    def test_fresh_asset_is_reusable(self) -> None:
        plan = _load_module()
        expires = self.NOW + 60 * 24 * 60 * 60
        self.assertTrue(plan.should_reuse(expires, self.NOW))

    def test_asset_close_to_expiry_is_not_reused(self) -> None:
        # 距过期不足安全余量时宁可重传：本地时钟与平台时钟有偏差，卡点复用会拿到失效引用。
        plan = _load_module()
        self.assertFalse(plan.should_reuse(self.NOW + 60, self.NOW))
        self.assertFalse(plan.should_reuse(self.NOW, self.NOW))
        self.assertFalse(plan.should_reuse(self.NOW - 1000, self.NOW))

    def test_unparseable_expiry_is_never_reused(self) -> None:
        plan = _load_module()
        self.assertFalse(plan.should_reuse(None, self.NOW))
        self.assertFalse(plan.should_reuse('', self.NOW))
        self.assertFalse(plan.should_reuse('not-a-time', self.NOW))
        self.assertFalse(plan.should_reuse(0, self.NOW))


class EvictionPlanTests(unittest.TestCase):
    def _images(self, count: int) -> list[dict[str, object]]:
        base = int(datetime(2026, 9, 1, 0, 0, 0).timestamp())
        return [
            {'image_id': f'img{index}', 'expires_at': base + index * 3600}
            for index in range(count)
        ]

    def test_no_eviction_while_there_is_room(self) -> None:
        plan = _load_module()
        # 上限 100、预留 10 → 可用 90；当前 80 张时还有位置，不该删任何东西。
        self.assertEqual(plan.plan_evictions(self._images(80), 100, 10), [])

    def test_evicts_oldest_until_a_slot_frees(self) -> None:
        plan = _load_module()
        victims = plan.plan_evictions(self._images(90), 100, 10)
        # 90 张时可用上限已满：删 1 张（最久未引用的 img0）才腾出位置
        self.assertEqual(victims, ['img0'])

    def test_evicts_enough_when_way_over_limit(self) -> None:
        plan = _load_module()
        # 已 100 张（例如别处也上传过素材）：要删到 89 再传 1 张，共 11 张
        victims = plan.plan_evictions(self._images(100), 100, 10)
        self.assertEqual(len(victims), 11)
        self.assertEqual(victims[0], 'img0')
        self.assertEqual(victims[-1], 'img10')

    def test_reserve_zero_still_keeps_one_free(self) -> None:
        plan = _load_module()
        # 预留 0 时仍需为本次上传留 1 个位置，否则「清完还是满」
        self.assertEqual(len(plan.plan_evictions(self._images(100), 100, 0)), 1)

    def test_entries_without_image_id_are_skipped(self) -> None:
        plan = _load_module()
        images = self._images(90)
        images[0] = {'image_id': '', 'expires_at': 0}
        victims = plan.plan_evictions(images, 100, 10)
        self.assertNotIn('', victims)
        self.assertEqual(victims, ['img1'])

    def test_unparseable_expiry_sorts_first(self) -> None:
        plan = _load_module()
        images = self._images(90)
        images[-1]['expires_at'] = None
        victims = plan.plan_evictions(images, 100, 10)
        # 过期时间读不出来说明该素材状态不明，优先删掉它而不是删确定在用的
        self.assertEqual(victims, ['img89'])


class NamingTests(unittest.TestCase):
    def test_file_name_is_content_addressed(self) -> None:
        plan = _load_module()
        name = plan.media_file_name('PNG', 'a' * 64)
        self.assertEqual(name, f'todaywaifu-{"a" * 32}.png')
        # 同内容不同大小写扩展名得到同一文件名（扩展名统一小写）
        self.assertEqual(plan.media_file_name('png', 'a' * 64), name)

    def test_content_type_matches_extension(self) -> None:
        plan = _load_module()
        # 平台会校验扩展名与 Content-Type 一致，不一致直接 40035001
        self.assertEqual(plan.content_type_for_extension('jpg'), 'image/jpeg')
        self.assertEqual(plan.content_type_for_extension('JPEG'), 'image/jpeg')
        self.assertEqual(plan.content_type_for_extension('png'), 'image/png')
        self.assertEqual(plan.content_type_for_extension('webp'), 'image/webp')
        self.assertEqual(plan.content_type_for_extension('bmp'), 'application/octet-stream')

    def test_only_platform_formats_are_supported(self) -> None:
        plan = _load_module()
        for extension in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'PNG'):
            self.assertTrue(plan.supported_extension(extension), extension)
        for extension in ('bmp', 'tiff', 'svg', ''):
            self.assertFalse(plan.supported_extension(extension), extension)


class SwitchTests(unittest.TestCase):
    def test_webconsole_booleans_are_understood(self) -> None:
        plan = _load_module()
        for raw in (True, 1, 'true', 'True', '1', 'on', '开启'):
            self.assertTrue(plan.is_enabled(raw), raw)
        for raw in (False, 0, 'false', '0', 'off', '关闭', None, '', 'maybe'):
            self.assertFalse(plan.is_enabled(raw), raw)


class ConstantsTests(unittest.TestCase):
    def test_platform_numbers_match_the_documented_limits(self) -> None:
        plan = _load_module()
        # 文档：单机器人 100 张、滑动有效期 90 天。数字写错会让复用判定与清理全部错位。
        self.assertEqual(plan.PLATFORM_MEDIA_LIMIT, 100)
        self.assertEqual(plan.PLATFORM_MEDIA_TTL_SECONDS, 90 * 24 * 60 * 60)
        self.assertEqual(plan.REUSE_SAFETY_SECONDS, 24 * 60 * 60)
        # 安全余量必须明显小于有效期，否则刚上传的素材就会被判为不可复用
        self.assertLess(plan.REUSE_SAFETY_SECONDS, plan.PLATFORM_MEDIA_TTL_SECONDS // 10)


class SafetyMarginTests(unittest.TestCase):
    def test_reuse_window_shrinks_as_deadline_approaches(self) -> None:
        plan = _load_module()
        now = datetime(2026, 9, 24, 12, 0, 0)
        ttl = plan.PLATFORM_MEDIA_TTL_SECONDS
        self.assertTrue(plan.should_reuse(int(now.timestamp()) + ttl, int(now.timestamp())))
        # 越过「有效期 - 安全余量」这条线后必须转为不可复用
        boundary = int(now.timestamp()) + plan.REUSE_SAFETY_SECONDS
        self.assertFalse(plan.should_reuse(boundary, int(now.timestamp())))
        self.assertFalse(plan.should_reuse(boundary - 1, int(now.timestamp())))
        self.assertTrue(
            plan.should_reuse(int((now + timedelta(seconds=ttl)).timestamp()), int(now.timestamp()))
        )


if __name__ == '__main__':
    unittest.main()
