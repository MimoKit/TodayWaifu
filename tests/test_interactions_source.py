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
    def test_reply_prefixes_are_selected_by_kind(self) -> None:
        shared_source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        for declaration in (
            "REPLY_PREFIX = '[今日老婆]'",
            "HUSBAND_REPLY_PREFIX = '[今日老公]'",
            "LOLI_REPLY_PREFIX = '[今日萝莉]'",
            "def _get_reply_prefix(kind: str = 'wife') -> str:",
            "def _reply_text(text: str, kind: str = 'wife') -> str:",
            "def _prefix_outgoing_message(message: Any, kind: str = 'wife') -> Any:",
            "kind: str = 'wife',",
        ):
            self.assertIn(declaration, shared_source)

    def test_mode_aware_modules_forward_reply_kind(self) -> None:
        for module_name in ('daily.py', 'rob.py', 'gift.py'):
            source = (ROOT / 'twf' / module_name).read_text(encoding='utf-8')
            self.assertRegex(source, r'_send_prefixed\([\s\S]*?kind=(?:mode|kind)')

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

    def test_divorce_module_registers_one_unified_command(self) -> None:
        init_source = (ROOT / '__init__.py').read_text(encoding='utf-8')
        shared_source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        divorce_source = (ROOT / 'twf' / 'divorce.py').read_text(encoding='utf-8')
        self.assertIn('from .twf import divorce', init_source)
        self.assertIn("divorce_sv = SV('今日老婆-离婚'", shared_source)
        for word in (
            '离婚',
            '全部离婚',
            '老婆离婚',
            '离婚老公',
            '离婚萝莉',
            '异环老婆离婚',
            '战双老婆离婚',
        ):
            self.assertIn(word, divorce_source)
        self.assertIn('async def divorce_all(', divorce_source)
        self.assertNotIn('async def divorce_wife(', divorce_source)
        self.assertNotIn('async def divorce_husband(', divorce_source)
        self.assertNotIn('async def divorce_loli(', divorce_source)

    def test_natural_command_aliases_are_registered(self) -> None:
        custom_source = (ROOT / 'twf' / 'custom_role.py').read_text(encoding='utf-8')
        loli_source = (ROOT / 'twf' / 'loli.py').read_text(encoding='utf-8')
        help_source = (ROOT / 'twf' / 'help.py').read_text(encoding='utf-8')
        for word in ('创建老婆', '上传老婆图片', '查看老婆图片', '删除老婆图片', '确认删除老婆', '取消删除老婆'):
            self.assertIn(word, custom_source)
        for word in ('上传萝莉图片', '查看萝莉图片'):
            self.assertIn(word, loli_source)
        self.assertIn('老婆帮助', help_source)

    def test_divorce_marks_all_daily_records_with_divorced_state(self) -> None:
        shared_source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        divorce_source = (ROOT / 'twf' / 'divorce.py').read_text(encoding='utf-8')
        self.assertIn("raw.get('divorced')", shared_source)
        self.assertIn("return 'divorced'", shared_source)
        self.assertIn('ALL_DAILY_RECORD_KINDS', shared_source)
        self.assertIn("context['safe_wives']", shared_source)
        self.assertIn('_mark_all_daily_records_divorced(', divorce_source)
        self.assertIn('clear_pending_gifts_for_user(', divorce_source)


    def test_rob_rejects_when_robber_already_has_active_item(self) -> None:
        rob_source = (ROOT / 'twf' / 'rob.py').read_text(encoding='utf-8')
        self.assertIn('robber_data = context[bucket].get(robber_id)', rob_source)
        self.assertIn("f'你今天已经有{title}了，先离婚再抢吧~'", rob_source)


if __name__ == '__main__':
    unittest.main()
