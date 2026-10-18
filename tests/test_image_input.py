# 图片输入的准入契约：文件名与声明后缀都不构成类型证明，只有真实解码成功才算图片。
#
# 背景（a982e77「harden image input and atomic record writes」）：改动前 detect_image_suffix 仅比对
# 魔数前缀并信任来源后缀，read_image_bytes 也以 validate=False 解码、按扩展名接受输入，
# 于是「名字是 .png、内容不是图片」的载荷可以一路进入落盘与后续处理，直到 Pillow 在更
# 晚的环节抛错，故障点远离根因。该提交改为一律以 Pillow 实际解码判定，并在解码前按
# base64 长度预估拦截，避免为超限载荷分配整块解码缓冲。
#
# 因此本文件守护：三类来源（消息段 / image_list / image）合并去重且保持原序（顺序会影响
# 落盘命名）、缺少可选字段的事件按「无图片」处理（b36eaa8）、非图片载荷在读入阶段即被
# 拒绝、合法的 PNG/JPEG/WEBP 不被误杀，以及 image_hash_id 的取值保持向后兼容。
import base64
import unittest
import importlib.util
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TodayWaifu" / "image_input.py"


def _load_module():
    # 以独立模块名直接加载源码文件：插件模块依赖 gsuid_core，整包导入不可行；
    # 同时不写入 sys.modules，保证每个用例拿到的是未受前一用例影响的模块状态
    spec = importlib.util.spec_from_file_location("todaywaifu_image_input", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load image_input module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImageInputTests(unittest.TestCase):
    def test_collect_image_refs_deduplicates_in_original_order(self) -> None:
        # 同一张图可能在 content 与 image_list 中重复上报，去重是必需的；但顺序同样
        # 不可交换：调用方按出现次序落盘文件名，重排会让同一次上传得出不同结果
        image_input = _load_module()
        event = SimpleNamespace(
            content=[SimpleNamespace(type="image", data=" a "), SimpleNamespace(type="text", data="x")],
            image_list=["a", "b"],
            image="c",
        )
        self.assertEqual(image_input.collect_image_refs(event), ("a", "b", "c"))

    def test_collect_image_refs_allows_missing_optional_event_fields(self) -> None:
        # b36eaa8：部分适配器上报的事件不含 image / image_list 字段，早先的属性直读会
        # 抛 AttributeError 并中断整条命令；缺字段须等价于「没有图片」而非报错
        image_input = _load_module()
        event = SimpleNamespace(content=[SimpleNamespace(type="image", data=" a ")])
        self.assertEqual(image_input.collect_image_refs(event), ("a",))

    def test_detect_image_suffix_rejects_signature_only_data(self) -> None:
        # 仅凭魔数前缀不足以证明载荷可用：必须以真实解码成功为准，否则截断或伪造的
        # 数据会被当作合法图片接受，错误推迟到下游才暴露
        image_input = _load_module()
        self.assertEqual(image_input.detect_image_suffix(b"\x89PNG\r\n\x1a\nrest", "fake.jpg"), "")
        self.assertEqual(image_input.detect_image_suffix(b"RIFFxxxxWEBPrest", "fake.jpg"), "")

    def test_read_image_bytes_rejects_encoded_payload_before_decode(self) -> None:
        # 超限载荷必须在解码前拦截。解码本身会一次性分配与载荷等长的缓冲，若先解码
        # 后判长度，max_bytes 对内存峰值不再具备约束力；此处以「b64decode 从未被调用」
        # 证明拦截确实发生在解码之前
        image_input = _load_module()
        encoded = "base64://" + ("A" * 10000)
        with patch.object(image_input.base64, "b64decode", side_effect=AssertionError):
            self.assertIsNone(image_input.read_image_bytes(encoded, 64))

    def test_read_image_bytes_validates_actual_image_not_extension(self) -> None:
        # 扩展名可由调用方任意伪造，因此两条输入路径（本地路径与 base64 引用）都必须
        # 以内容判定：声明为 png 而内容不是图片时一律拒绝，不允许任一入口放宽
        image_input = _load_module()
        self.assertIsNone(image_input.read_image_bytes("fake.png", 1024))
        self.assertIsNone(image_input.read_image_bytes("base64://" + base64.b64encode(b"not png").decode(), 1024))

    def test_read_image_bytes_preserves_valid_png_jpeg_webp(self) -> None:
        image_input = _load_module()
        # Use Pillow-generated fixtures to avoid relying on signature-only bytes.
        # 校验加严后最直接的风险是误杀合法输入，故对三种真实编码各验证一次，并断言
        # 返回后缀与实际格式一致：该后缀决定落盘文件名，错配会让后续读取找不到文件
        from io import BytesIO

        from PIL import Image
        for fmt, suffix in (("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")):
            buf = BytesIO()
            Image.new("RGB", (2, 2), "red").save(buf, format=fmt)
            encoded = "base64://" + base64.b64encode(buf.getvalue()).decode()
            result = image_input.read_image_bytes(encoded, 1024 * 1024)
            self.assertIsNotNone(result)
            self.assertEqual(result[1], suffix)

    def test_image_hash_id_uses_filename_for_backward_compatibility(self) -> None:
        # 摘要取自文件名（不含目录）并截断为 8 位十六进制，该取值已随记录写入历史数据：
        # 摘要改取内容或纳入目录前缀都会让既有引用全部失效。此处的固定期望值即兼容契约，
        # 有意固化为常量以便任何改动都会立即失败
        image_input = _load_module()
        self.assertEqual(image_input.image_hash_id(Path("/tmp/example.png")), "ca75a0b8")


if __name__ == "__main__":
    unittest.main()
