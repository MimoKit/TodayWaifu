import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InteractionConfigSourceTests(unittest.TestCase):
    def test_help_appearance_config_page_exists(self) -> None:
        config_source = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        daily_config_source = (ROOT / 'daily_wife_config.py').read_text(encoding='utf-8')
        help_source = (ROOT / 'twf' / 'help.py').read_text(encoding='utf-8')
        for key in (
            'DailyWifeHelpBannerBgUpload',
            'DailyWifeHelpBgUpload',
            'DailyWifeHelpIconUpload',
            'DailyWifeHelpColumn',
        ):
            self.assertIn(key, config_source)
            self.assertIn(key, help_source)
        self.assertIn("DailyWifeShowConfig = StringConfig(", daily_config_source)
        self.assertIn("'今日老婆外观配置'", daily_config_source)
        self.assertIn('column=column', help_source)

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
            'DailyWifeLoliApiUrl',
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
        for word in ('接受老婆赠送', '拒绝老婆赠送', '接受老公赠送', '拒绝老公赠送', '接受萝莉赠送', '拒绝萝莉赠送'):
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

    def test_divorce_module_registers_independent_commands(self) -> None:
        init_source = (ROOT / '__init__.py').read_text(encoding='utf-8')
        shared_source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        divorce_source = (ROOT / 'twf' / 'divorce.py').read_text(encoding='utf-8')
        self.assertIn('from .twf import divorce', init_source)
        self.assertIn("divorce_sv = SV('今日老婆-离婚'", shared_source)
        for word in ('离婚', '离婚老公', '离婚萝莉'):
            self.assertIn(word, divorce_source)
        for word in ('老婆离婚', '老公离婚', '萝莉离婚'):
            self.assertIn(word, divorce_source)

    def test_natural_command_aliases_are_registered(self) -> None:
        custom_source = (ROOT / 'twf' / 'custom_role.py').read_text(encoding='utf-8')
        loli_source = (ROOT / 'twf' / 'loli.py').read_text(encoding='utf-8')
        help_source = (ROOT / 'twf' / 'help.py').read_text(encoding='utf-8')
        for word in ('创建老婆', '上传老婆图片', '查看老婆图片', '删除老婆图片', '确认删除老婆', '取消删除老婆'):
            self.assertIn(word, custom_source)
        for word in ('上传萝莉图片', '查看萝莉图片'):
            self.assertIn(word, loli_source)
        self.assertIn('老婆帮助', help_source)

    def test_divorce_uses_separate_state_not_stolen_or_gifted(self) -> None:
        shared_source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        divorce_source = (ROOT / 'twf' / 'divorce.py').read_text(encoding='utf-8')
        self.assertIn("raw.get('divorced')", shared_source)
        self.assertIn("return 'divorced'", shared_source)
        self.assertIn("current['divorced'] = True", divorce_source)
        self.assertNotIn("current['stolen_by']", divorce_source)
        self.assertNotIn("current['gifted_to']", divorce_source)


if __name__ == '__main__':
    unittest.main()
