import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ResultImageSenderTests(unittest.TestCase):
    def test_rob_and_gift_legacy_senders_delegate_to_shared_sender(self) -> None:
        for filename, wrapper in (
            ("rob.py", "_send_rob_result_image"),
            ("gift.py", "_send_gift_result_image"),
        ):
            with self.subTest(filename=filename):
                tree = ast.parse((ROOT / "TodayWaifu" / filename).read_text(encoding="utf-8"))
                function = next(
                    node
                    for node in tree.body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == wrapper
                )
                calls = [
                    node
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                ]
                self.assertEqual([call.func.id for call in calls], ["_send_daily_result_image"])

    def test_shared_sender_keeps_kind_specific_result_selection(self) -> None:
        source = (ROOT / "TodayWaifu" / "senders.py").read_text(encoding="utf-8")
        self.assertIn("async def _send_daily_result_image(", source)
        self.assertIn("if kind != 'loli':", source)
        self.assertIn("_send_role_image(bot, role, image, text, user_id, is_group, kind)", source)
        self.assertIn("_send_loli_result_image(bot, image, text, user_id, is_group, kind)", source)

    def test_result_senders_inject_ai_readable_summary(self) -> None:
        """AI 调用工具时需拿到文字摘要，否则只能看到图片资源 ID、答不出"抽到了谁"。"""
        source = (ROOT / "TodayWaifu" / "senders.py").read_text(encoding="utf-8")
        self.assertIn("def _ai_return_draw(", source)

        role_fn = source[
            source.index("async def _send_role_image("):
            source.index("async def _send_daily_result_image(")
        ]
        self.assertIn("_ai_return_draw(kind, role.name, text)", role_fn)

        loli_fn = source[source.index("async def _send_loli_result_image("):source.index("_send_shota_result_image = ")]
        self.assertIn("_ai_return_draw(kind, '', text)", loli_fn)

    def test_loli_sender_downloads_remote_images_before_building_segment(self) -> None:
        source = (ROOT / "TodayWaifu" / "senders.py").read_text(encoding="utf-8")
        function_start = source.index("async def _send_loli_result_image(")
        function_end = source.index("async def _send_local_image(", function_start)
        function = source[function_start:function_end]

        self.assertIn("image_ref = await _download_image(image)", function)
        self.assertNotIn(
            "if image.startswith(('http://', 'https://')):\n            image_ref = image",
            function,
        )


if __name__ == "__main__":
    unittest.main()
