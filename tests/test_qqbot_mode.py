"""QQ 官方机器人模式：开关驱动、无平台自动检测、图床后端可选。

核心契约：
1. 是否走官方机器人链路**只由 DailyWifeQQBotEnabled 决定**；
2. 源码中不得出现任何平台自动检测（bot_id 前缀嗅探）；
3. 图床后端支持 cos / cnb / off。
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QQBOT_PATH = ROOT / 'TodayWaifu' / 'qqbot.py'
SENDERS_PATH = ROOT / 'TodayWaifu' / 'senders.py'

# 参考实现里用于嗅探平台的常量，本插件明确不要
FORBIDDEN_PLATFORM_SNIFFING = (
    'QQ_OFFICIAL_BOT_IDS',
    '_is_official_qq_bot',
    'qqgroup',
    'qqguild',
)


def _qqbot_source() -> str:
    return QQBOT_PATH.read_text(encoding='utf-8-sig')


def _extract(path: Path, name: str, globals_dict: dict):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    namespace = dict(globals_dict)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<test>', 'exec'), namespace)
    return namespace[name]


def _cfg_stub(values: dict):
    def cfg(key: str):
        return values.get(key, '')

    def cfg_bool(key: str, default: bool = False) -> bool:
        return bool(values.get(key, default))

    return cfg, cfg_bool


class QQBotNoAutoDetectTests(unittest.TestCase):
    def test_qqbot_module_has_no_platform_sniffing(self) -> None:
        source = _qqbot_source()
        for token in FORBIDDEN_PLATFORM_SNIFFING:
            self.assertNotIn(token, source, f'qqbot.py 不应出现平台自动检测: {token}')

    def test_senders_do_not_sniff_platform(self) -> None:
        source = SENDERS_PATH.read_text(encoding='utf-8-sig')
        for token in FORBIDDEN_PLATFORM_SNIFFING:
            self.assertNotIn(token, source, f'senders.py 不应出现平台自动检测: {token}')

    def test_qqbot_enabled_reads_only_the_switch(self) -> None:
        enabled = _extract(
            QQBOT_PATH,
            'qqbot_enabled',
            {
                '_qqbot_cfg_bool': lambda key, default=False: key == 'DailyWifeQQBotEnabled',
            },
        )
        self.assertTrue(enabled())


class QQBotSwitchTests(unittest.TestCase):
    def test_switch_defaults_to_off(self) -> None:
        source = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        block = next(
            node
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == 'QQBOT_CONFIG_DEFAULT'
        )
        values = {
            key.value: value
            for key, value in zip(block.value.keys, block.value.values)
            if isinstance(key, ast.Constant)
        }
        enabled_expr = ast.unparse(values['DailyWifeQQBotEnabled'])
        self.assertIn('False', enabled_expr)
        self.assertNotIn('True', enabled_expr)

    def test_all_three_backends_declared(self) -> None:
        source = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        block = next(
            node
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == 'QQBOT_CONFIG_DEFAULT'
        )
        values = {
            key.value: value
            for key, value in zip(block.value.keys, block.value.values)
            if isinstance(key, ast.Constant)
        }
        host_expr = ast.unparse(values['DailyWifeQQBotImageHost'])
        for backend in ('cos', 'cnb', 'off'):
            self.assertIn(f"'{backend}'", host_expr)

    def test_secrets_default_to_empty(self) -> None:
        source = (ROOT / 'config_default.py').read_text(encoding='utf-8-sig')
        for key in ('DailyWifeCosSecretId', 'DailyWifeCosSecretKey', 'DailyWifeCnbToken'):
            index = source.index(f"'{key}'")
            segment = source[index:index + 400]
            self.assertIn("''", segment, f'{key} 的默认值必须留空，不得硬编码密钥')


class QQBotMarkdownTests(unittest.TestCase):
    def _build(self, image_url, size, text, user_id, is_group, *, at_user=True):
        cfg, cfg_bool = _cfg_stub({'DailyWifeQQBotAtUser': at_user})
        return _extract(
            QQBOT_PATH,
            'build_image_markdown',
            {
                '_qqbot_cfg_bool': cfg_bool,
                'MARKDOWN_IMAGE_MAX_WIDTH': 260,
                'MARKDOWN_IMAGE_MAX_HEIGHT': 360,
                '_markdown_size': _extract(
                    QQBOT_PATH,
                    '_markdown_size',
                    {
                        'MARKDOWN_IMAGE_MAX_WIDTH': 260,
                        'MARKDOWN_IMAGE_MAX_HEIGHT': 360,
                    },
                ),
            },
        )(image_url, size, text, user_id, is_group)

    def test_group_message_contains_at_and_image(self) -> None:
        markdown = self._build('https://img.test/a.png', (1000, 1500), '文案', '12345', True)
        self.assertIn('<@12345>', markdown)
        self.assertIn('文案', markdown)
        self.assertIn('https://img.test/a.png', markdown)
        self.assertIn('![image #', markdown)

    def test_private_message_never_ats(self) -> None:
        markdown = self._build('https://img.test/a.png', (1000, 1500), '文案', '12345', False)
        self.assertNotIn('<@', markdown)

    def test_oversized_image_is_scaled_into_box(self) -> None:
        # 4000x6000 等比缩到 260x360 框内 -> 受高度限制，240x360
        markdown = self._build('https://img.test/a.png', (4000, 6000), None, None, True)
        self.assertIn('#240px #360px', markdown)
        self.assertNotIn('#4000px', markdown)


class QQBotKeyboardTests(unittest.TestCase):
    def _build(self, enabled: bool):
        def cfg_bool(key, default=False):
            return enabled

        return _extract(
            QQBOT_PATH,
            'build_marry_member_keyboard',
            {'_qqbot_cfg_bool': cfg_bool, '_command_button': lambda label, cmd: (label, cmd)},
        )()

    def test_keyboard_contains_four_commands(self) -> None:
        keyboard = self._build(True)
        labels = [label for row in keyboard for label, _ in row]
        self.assertEqual(labels, ['摸头', '离婚', '今日老婆', '今日萝莉'])

    def test_keyboard_disabled_returns_none(self) -> None:
        self.assertIsNone(self._build(False))


class QQBotImageHostTests(unittest.TestCase):
    def _host(self, backend: str) -> str:
        return _extract(
            QQBOT_PATH,
            '_image_host',
            {'_qqbot_cfg': lambda key: backend},
        )()

    def test_known_backends(self) -> None:
        for backend in ('cos', 'cnb', 'off'):
            self.assertEqual(self._host(backend), backend)

    def test_unknown_backend_falls_back_to_off(self) -> None:
        self.assertEqual(self._host('weibo'), 'off')


if __name__ == '__main__':
    unittest.main()
