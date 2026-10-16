"""候选缓存键必须按加载器唯一：缓存值的结构不同，共用键会让对方按错误的结构解包。

`gallery.py` 的鸣潮加载器曾与 `roles.py` 的 `_load_local_candidates` 共用 `local:{role_mode}`
键，但两者写入的值结构并不一致——前者是候选元组本身，后者是 `(候选, 失败原因)` 二元组。
当同一进程内先以 local 为图片来源加载过候选（`DailyWifeImageSource` 默认即 local，运行时
改开关也是改这个配置，且改开关不会清候选缓存），随后图库模式下为对照表中尚未收录的角色做
本地补位时，`local_candidates, _ = await run_blocking(_load_local_candidates, role_mode)`
读到的就是候选元组，候选数不等于 2 即抛
`ValueError: too many values to unpack (expected 2)`——与用户上报的堆栈完全一致。

反向的破坏同样存在且更隐蔽：local 模式读到 `(候选, 原因)` 会被当成候选元组继续使用，不报错
但数据是错的。因此这里锁定两点：鸣潮加载器的键带独立前缀，且读缓存、写缓存与在途去重
三处必须使用同一个键。
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
GALLERY = PLUGIN / 'gallery.py'
ROLES = PLUGIN / 'roles.py'


class CandidateCacheKeyCollisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gallery = GALLERY.read_text(encoding='utf-8')
        self.roles = ROLES.read_text(encoding='utf-8')

    def test_wuwa_loader_uses_its_own_key_prefix(self) -> None:
        """鸣潮加载器的键必须与本地扫描的 `local:{role_mode}` 区分开。"""
        self.assertIn("cache_key = f'wuwa:{source}:{role_mode}'", self.gallery)

    def test_wuwa_loader_no_longer_writes_the_local_key(self) -> None:
        """不许退回 `{source}:{role_mode}`：source 为 local 时它会撞上本地扫描的键。"""
        self.assertNotIn("cache_key = f'{source}:{role_mode}'", self.gallery)
        self.assertNotIn("cache_key = f'{_image_source(role_mode)}:{role_mode}'", self.gallery)

    def test_wuwa_dedup_key_matches_the_cache_key(self) -> None:
        """在途去重与缓存必须同键，否则同一份候选会被并发加载多次。"""
        self.assertIn("cache_key = f'wuwa:{_image_source(role_mode)}:{role_mode}'", self.gallery)

    def test_sharing_a_key_would_break_both_shapes(self) -> None:
        """两个加载器的值结构不同，这正是共用键必然出错的原因（改动前的前提）。"""
        self.assertIn('CANDIDATE_CACHE[cache_key] = (now, result)', self.roles)
        self.assertIn("cache_key = f'local:{role_mode}'", self.roles)
        self.assertIn('CANDIDATE_CACHE[cache_key] = (now, candidates)', self.gallery)


if __name__ == '__main__':
    unittest.main()
