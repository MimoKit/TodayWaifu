import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "twf" / "kind_metadata.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("todaywaifu_kind_metadata", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load kind_metadata module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DailyKindMetadataTests(unittest.TestCase):
    def test_known_kinds_have_centralized_metadata(self) -> None:
        kinds = _load_module()
        self.assertEqual(set(kinds.DAILY_KIND_METADATA), {"wife", "husband", "loli"})
        self.assertEqual(kinds.daily_kind_metadata("wife").bucket, "wives")
        self.assertEqual(kinds.daily_kind_metadata("husband").title, "老公")
        self.assertEqual(kinds.daily_kind_metadata("loli").rob_enabled_key, "DailyLoliRobEnabled")
        self.assertEqual(
            kinds.daily_kind_metadata("husband").gift_success_default,
            "你把今天的老公{name}送给了对方！",
        )

    def test_unknown_kind_keeps_legacy_wife_fallback(self) -> None:
        kinds = _load_module()
        self.assertIs(kinds.daily_kind_metadata("unknown"), kinds.DAILY_KIND_METADATA["wife"])

    def test_metadata_includes_templates_and_config_keys(self) -> None:
        kinds = _load_module()
        loli = kinds.daily_kind_metadata("loli")
        self.assertEqual(loli.rob_success_rate_key, "DailyLoliRobSuccessRate")
        self.assertEqual(loli.rob_success_default, "抢萝莉成功！你把对方今天的萝莉抢过来了！")
        self.assertEqual(loli.gift_enabled_key, "DailyLoliGiftEnabled")
        self.assertEqual(loli.gift_success_key, "DailyLoliGiftSuccessTemplate")


if __name__ == "__main__":
    unittest.main()
