import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InteractionConfigSourceTests(unittest.TestCase):
    def test_new_interaction_configs_exist(self) -> None:
        source = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        keys = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        keys.add(key.value)
        expected = {
            'DailyHusbandRobEnabled',
            'DailyHusbandRobSuccessTemplate',
            'DailyHusbandGiftEnabled',
            'DailyHusbandGiftSuccessTemplate',
            'DailyLoliRobEnabled',
            'DailyLoliRobSuccessRate',
            'DailyLoliRobSuccessTemplate',
            'DailyLoliGiftEnabled',
            'DailyLoliGiftSuccessTemplate',
        }
        self.assertTrue(expected.issubset(keys), expected - keys)


class InteractionSourceTests(unittest.TestCase):
    def test_rob_and_gift_register_husband_and_loli_commands(self) -> None:
        rob_source = (ROOT / 'twf' / 'rob.py').read_text(encoding='utf-8')
        gift_source = (ROOT / 'twf' / 'gift.py').read_text(encoding='utf-8')
        for word in ('抢老公', '抢今日老公', '抢萝莉', '抢今日萝莉'):
            self.assertIn(word, rob_source)
        for word in ('送老公', '送今日老公', '同意送老公', '拒绝送老公', '送萝莉', '送今日萝莉', '同意送萝莉', '拒绝送萝莉'):
            self.assertIn(word, gift_source)

    def test_loli_daily_record_is_persisted(self) -> None:
        source = (ROOT / 'twf' / 'loli.py').read_text(encoding='utf-8')
        self.assertIn("context['lolis']", source)
        self.assertIn('_record_to_dict', source)

    def test_result_sender_calls_pass_kind_argument(self) -> None:
        rob_source = (ROOT / 'twf' / 'rob.py').read_text(encoding='utf-8')
        gift_source = (ROOT / 'twf' / 'gift.py').read_text(encoding='utf-8')
        self.assertIn('ev.group_id is not None,\n        kind,', rob_source)
        self.assertIn('ev.group_id is not None,\n        kind,', gift_source)

    def test_loli_gift_request_does_not_include_image_id_name(self) -> None:
        gift_source = (ROOT / 'twf' / 'gift.py').read_text(encoding='utf-8')
        self.assertIn("item_text = title if kind == 'loli' else f'{title}{role.name}'", gift_source)


if __name__ == '__main__':
    unittest.main()
