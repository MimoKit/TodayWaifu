"""签名 URL 的缓存键：图库给图片 URL 加短期签名后，缓存不得因签名变化而整体失效。

背景：图库（twf-gallery）为了给每张图单独授权，会在列表返回的图片 URL 上
附加 `?e=<过期时间>&s=<HMAC 签名>`。签名按时间桶轮换，也就是说同一张图的
URL 每隔几小时就会变，但**图片内容没变**。

而 `url_hash_cache_path` 是内容寻址的：直接 sha256 整个 URL。若不处理，
签名一换：
  1. 磁盘缓存全部失效 —— 凌晨预热（23:50）暖好的图，0 点抽签时全部落空；
  2. 同一张图会按新签名再存一份，磁盘占用随轮换次数线性增长。

因此缓存键必须忽略这些「一次性授权」参数，只保留真正标识资源的路径。
"""
import sys
import unittest
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
FILE_CACHE = ROOT / 'TodayWaifu' / 'file_cache.py'


def _load_file_cache():
    spec = importlib.util.spec_from_file_location('todaywaifu_file_cache_signed', FILE_CACHE)
    if spec is None or spec.loader is None:
        raise RuntimeError('cannot load file_cache')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


file_cache = _load_file_cache()

SIGNED_A = 'https://twf.xlinxc.cn/v1/xwuid/1209/a.webp?e=1790791200&s=aaaabbbb'
SIGNED_B = 'https://twf.xlinxc.cn/v1/xwuid/1209/a.webp?e=1790812800&s=ccccdddd'
PLAIN = 'https://twf.xlinxc.cn/v1/xwuid/1209/a.webp'


class CanonicalUrlTests(unittest.TestCase):
    def test_strips_signature_params(self) -> None:
        self.assertEqual(file_cache.canonical_url(SIGNED_A), PLAIN)
        self.assertEqual(file_cache.canonical_url(SIGNED_B), PLAIN)

    def test_plain_url_is_unchanged(self) -> None:
        """不带签名的 URL 必须原样返回，否则历史缓存会全部失效。"""
        self.assertEqual(file_cache.canonical_url(PLAIN), PLAIN)

    def test_strips_token_query_param(self) -> None:
        """把令牌放在查询串里的图库同样要归一到同一个缓存键。"""
        with_token = f'{PLAIN}?token=twf_somesecret'
        self.assertEqual(file_cache.canonical_url(with_token), PLAIN)

    def test_keeps_meaningful_query_params(self) -> None:
        """真正区分资源的参数必须保留，不能把不同图片合并成一个键。"""
        variant = f'{PLAIN}?size=large'
        self.assertNotEqual(file_cache.canonical_url(variant), PLAIN)
        self.assertEqual(file_cache.canonical_url(variant), variant)

    def test_keeps_meaningful_param_when_mixed_with_signature(self) -> None:
        mixed = f'{PLAIN}?size=large&e=1790791200&s=deadbeef'
        self.assertEqual(file_cache.canonical_url(mixed), f'{PLAIN}?size=large')

    def test_different_paths_stay_distinct(self) -> None:
        other = 'https://twf.xlinxc.cn/v1/xwuid/1209/b.webp?e=1&s=x'
        self.assertNotEqual(file_cache.canonical_url(other), file_cache.canonical_url(SIGNED_A))

    def test_empty_and_malformed_input(self) -> None:
        self.assertEqual(file_cache.canonical_url(''), '')
        # 不含 query 的普通字符串原样返回，不应抛异常
        self.assertEqual(file_cache.canonical_url('not a url'), 'not a url')


class SignatureStableCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        file_cache.clear_cached_url_index()

    def test_same_image_different_signature_shares_cache_path(self) -> None:
        """核心断言：签名变化后缓存路径必须一致。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                file_cache.url_hash_cache_path(root, SIGNED_A),
                file_cache.url_hash_cache_path(root, SIGNED_B),
            )

    def test_download_then_hit_with_rotated_signature(self) -> None:
        """按旧签名缓存后，用新签名读取必须命中同一份文件（零网络）。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(file_cache.write_url_cache(root, SIGNED_A, b'IMAGEDATA'))
            self.assertEqual(file_cache.read_url_cache(root, SIGNED_B), b'IMAGEDATA')

    def test_rotated_signature_is_reported_as_cached(self) -> None:
        """内存索引同样要按归一化键判断，否则抽签不会优先挑已缓存的图。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_cache.write_url_cache(root, SIGNED_A, b'x')
            self.assertTrue(file_cache.is_url_cached(SIGNED_B), '换签名后仍应视为已缓存')
            self.assertTrue(file_cache.is_url_cached(PLAIN), '去掉签名后也应视为已缓存')

    def test_write_does_not_duplicate_files_across_rotation(self) -> None:
        """签名轮换不应在磁盘上留下同一张图的多个副本。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_cache.write_url_cache(root, SIGNED_A, b'one')
            file_cache.write_url_cache(root, SIGNED_B, b'two')
            files = [p for p in root.iterdir() if p.is_file() and not p.name.startswith('.')]
            self.assertEqual(len(files), 1, f'应只有一份缓存文件，实际 {len(files)} 份')

    def test_prefer_cached_urls_recognises_rotated_signature(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_cache.write_url_cache(root, SIGNED_A, b'x')
            urls = (SIGNED_B, PLAIN.replace('a.webp', 'z.webp'))
            self.assertEqual(file_cache.prefer_cached_urls(urls), (SIGNED_B,))

    def test_distinct_images_not_merged(self) -> None:
        """反向保证：不同的图必须仍然各占一个缓存键。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = file_cache.url_hash_cache_path(root, 'https://h/x/1.webp?e=1&s=a')
            b = file_cache.url_hash_cache_path(root, 'https://h/x/2.webp?e=1&s=a')
            self.assertNotEqual(a, b)


if __name__ == '__main__':
    unittest.main()
