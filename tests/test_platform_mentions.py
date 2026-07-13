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


class FakeMessageSegment:
    @staticmethod
    def markdown(content: str) -> list[FakeMessage]:
        return [FakeMessage('markdown', content)]


def _load_mention_functions() -> dict[str, Any]:
    source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    names = {
        '_needs_qq_markdown_mention',
        '_official_image_gallery_url',
        '_is_at_message',
        '_remove_private_mentions',
        '_convert_official_qq_mentions',
        '_adapt_mentions_for_platform',
    }
    body = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace: dict[str, Any] = {
        'Any': Any,
        'Bot': object,
        'Message': FakeMessage,
        'MessageSegment': FakeMessageSegment,
        'QQ_MARKDOWN_MENTION_BOT_IDS': {'qqgroup'},
        '_cfg': lambda key: 'https://gallery.example.test' if key == 'DailyWifeOfficialImageGalleryUrl' else '',
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), 'mentions', 'exec'), namespace)
    return namespace


class PlatformMentionTests(unittest.TestCase):
    def test_runtime_platform_conversion(self) -> None:
        functions = _load_mention_functions()
        adapt = functions['_adapt_mentions_for_platform']
        at = FakeMessage('at', 'OPENID_123')
        image = FakeMessage('image', 'base64://image')
        outgoing = [at, '\n', '[今日老婆]你今天的老婆是今汐', image]

        official_bot = SimpleNamespace(
            ev=SimpleNamespace(
                real_bot_id='qqgroup:official-1',
                bot_id='qqgroup',
                user_type='group',
            )
        )
        official = adapt(official_bot, outgoing)
        self.assertEqual(official[0].type, 'markdown')
        self.assertEqual(
            official[0].data,
            '<@OPENID_123>\n[今日老婆]你今天的老婆是今汐',
        )
        self.assertIs(official[1], image)

        personal_bot = SimpleNamespace(
            ev=SimpleNamespace(
                real_bot_id='onebot:llbot',
                bot_id='onebot',
                user_type='group',
            )
        )
        self.assertIs(adapt(personal_bot, outgoing), outgoing)

        guild_bot = SimpleNamespace(
            ev=SimpleNamespace(
                real_bot_id='qqguild:official-channel',
                bot_id='qqguild',
                user_type='group',
            )
        )
        self.assertIs(adapt(guild_bot, outgoing), outgoing)

        direct_bot = SimpleNamespace(
            ev=SimpleNamespace(
                real_bot_id='qqgroup:official-1',
                bot_id='qqgroup',
                user_type='direct',
            )
        )
        direct = adapt(direct_bot, outgoing)
        self.assertEqual(direct[0], '[今日老婆]你今天的老婆是今汐')
        self.assertIs(direct[1], image)

    def test_all_mentions_are_adapted_at_the_send_boundary(self) -> None:
        source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        self.assertIn(
            'message = _adapt_mentions_for_platform(bot, message)',
            source,
        )
        self.assertIn("QQ_MARKDOWN_MENTION_BOT_IDS = {'qqgroup'}", source)
        self.assertIn("_cfg('DailyWifeOfficialImageGalleryUrl')", source)
        self.assertIn("markdown_parts.append(f'<@{item.data}>')", source)
        self.assertIn('return _remove_private_mentions(message)', source)
        self.assertIn('return message', source)

    def test_empty_gallery_address_disables_official_markdown_mentions(self) -> None:
        functions = _load_mention_functions()
        functions['_cfg'] = lambda key: ''
        bot = SimpleNamespace(
            ev=SimpleNamespace(
                real_bot_id='qqgroup:official-1',
                bot_id='qqgroup',
                user_type='group',
            )
        )
        self.assertFalse(functions['_needs_qq_markdown_mention'](bot))

    def test_image_senders_keep_one_cross_platform_at_segment(self) -> None:
        source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        for function_name in (
            '_send_role_image',
            '_send_loli_result_image',
            '_send_local_image',
        ):
            start = source.index(f'async def {function_name}(')
            next_function = source.find('\nasync def ', start + 1)
            block = source[start:next_function if next_function >= 0 else None]
            self.assertIn('messages.append(MessageSegment.at(user_id))', block)

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
