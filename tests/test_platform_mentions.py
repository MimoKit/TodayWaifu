import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class FakeMessage:
    def __init__(self, type: str, data: Any):
        self.type = type
        self.data = data


def _load_functions(names: set[str], config: dict[str, Any] | None = None) -> dict[str, Any]:
    source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    body = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    values = config or {}
    namespace: dict[str, Any] = {
        'Any': Any,
        'Bot': object,
        'Message': FakeMessage,
        'QQ_OFFICIAL_BOT_IDS': {'qqgroup'},
        '_cfg': lambda key: values.get(key, ''),
        '_cfg_bool': lambda key, default=False: bool(values.get(key, default)),
        '_reply_text': lambda text, kind='wife': f'[{kind}]{text}',
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), 'mentions', 'exec'), namespace)
    return namespace


class PlatformMentionTests(unittest.TestCase):
    def test_platform_detection_only_matches_qq_official(self) -> None:
        functions = _load_functions({'_is_official_qq_bot'})
        detect = functions['_is_official_qq_bot']
        official = SimpleNamespace(ev=SimpleNamespace(real_bot_id='qqgroup:official-1', bot_id='qqgroup'))
        personal = SimpleNamespace(ev=SimpleNamespace(real_bot_id='onebot:llbot', bot_id='onebot'))
        guild = SimpleNamespace(ev=SimpleNamespace(real_bot_id='qqguild:channel', bot_id='qqguild'))
        self.assertTrue(detect(official))
        self.assertFalse(detect(personal))
        self.assertFalse(detect(guild))

    def test_generic_send_boundary_only_removes_private_mentions(self) -> None:
        functions = _load_functions(
            {'_is_at_message', '_remove_private_mentions', '_adapt_mentions_for_platform'}
        )
        adapt = functions['_adapt_mentions_for_platform']
        outgoing = [FakeMessage('at', 'OPENID_123'), '\n', '结果文字', FakeMessage('image', 'x')]

        group_bot = SimpleNamespace(ev=SimpleNamespace(user_type='group'))
        self.assertIs(adapt(group_bot, outgoing), outgoing)

        direct_bot = SimpleNamespace(ev=SimpleNamespace(user_type='direct'))
        direct = adapt(direct_bot, outgoing)
        self.assertEqual(direct[0], '结果文字')
        self.assertEqual(direct[1].type, 'image')

    def test_official_markdown_combines_mention_text_and_image(self) -> None:
        functions = _load_functions(
            {'_official_markdown_image_size', '_build_official_qq_image_markdown'},
            {
                'DailyWifeAtUser': True,
                'DailyWifeReplyPrefixEnabled': True,
            },
        )
        markdown = functions['_build_official_qq_image_markdown'](
            'https://gallery.example.test/todaywaifu.png',
            (1000, 1500),
            '你今天的老婆是今汐',
            'OPENID_123',
            True,
            'wife',
        )
        self.assertEqual(
            markdown,
            '<@OPENID_123> [wife]你今天的老婆是今汐\n\n'
            '![image #240px #360px](https://gallery.example.test/todaywaifu.png)',
        )

    def test_official_private_markdown_has_no_mention(self) -> None:
        functions = _load_functions(
            {'_official_markdown_image_size', '_build_official_qq_image_markdown'},
            {
                'DailyWifeAtUser': True,
                'DailyWifeReplyPrefixEnabled': True,
            },
        )
        markdown = functions['_build_official_qq_image_markdown'](
            'https://gallery.example.test/todaywaifu.png',
            (800, 1200),
            '你今天的老婆是今汐',
            'OPENID_123',
            False,
            'wife',
        )
        self.assertNotIn('<@', markdown)
        self.assertIn('[wife]你今天的老婆是今汐', markdown)

    def test_repository_defaults_do_not_contain_private_gallery_config(self) -> None:
        source = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        self.assertIn("'DailyWifeOfficialImageGalleryUrl'", source)
        self.assertIn("'DailyWifeOfficialImageGalleryToken'", source)
        self.assertNotIn('qq.xlinxc.cn', source)

    def test_all_result_image_senders_try_plugin_scoped_markdown(self) -> None:
        source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        for function_name in ('_send_role_image', '_send_loli_result_image', '_send_local_image'):
            start = source.index(f'async def {function_name}(')
            next_function = source.find('\nasync def ', start + 1)
            block = source[start:next_function if next_function >= 0 else None]
            self.assertIn('_try_send_official_qq_image_markdown(', block)

    def test_daily_loli_results_use_the_shared_sender(self) -> None:
        source = (ROOT / 'twf' / 'loli.py').read_text(encoding='utf-8')
        self.assertIn('_send_loli_result_image(', source)
        self.assertNotIn('_with_loli_reply_prefix(', source)

    def test_assignment_has_no_platform_specific_branch(self) -> None:
        source = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8')
        assignment_start = source.index('async def _send_assign_wife(')
        assignment_end = source.index('\nasync def ', assignment_start + 1)
        assignment = source[assignment_start:assignment_end]
        self.assertNotIn('official_qq_mention', assignment)
        self.assertIn('_send_role_image(', assignment)


if __name__ == '__main__':
    unittest.main()
