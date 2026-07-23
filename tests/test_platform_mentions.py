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
        'Event': object,
        'Message': FakeMessage,
        're': __import__('re'),
        '_cfg': lambda key: values.get(key, ''),
        '_cfg_bool': lambda key, default=False: bool(values.get(key, default)),
        '_reply_text': lambda text, kind='wife': f'[{kind}]{text}',
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), 'mentions', 'exec'), namespace)
    return namespace


class PlatformMentionTests(unittest.TestCase):
    def test_personal_target_formats_are_supported(self) -> None:
        functions = _load_functions(
            {'_normalise_target_user_id', '_target_user_id_from_text', '_iter_event_messages', '_get_event_target_user_id'}
        )
        normalise = functions['_normalise_target_user_id']
        parse_text = functions['_target_user_id_from_text']
        get_target = functions['_get_event_target_user_id']

        self.assertEqual(normalise({'qq': '123456789'}), '123456789')
        self.assertEqual(normalise({'user_id': '10001'}), '10001')
        self.assertEqual(parse_text('[CQ:at,qq=123456789]'), '123456789')
        self.assertEqual(parse_text('抢老婆 @123456789'), '123456789')
        self.assertEqual(parse_text('送老婆 123456789'), '123456789')
        self.assertEqual(
            get_target(
                SimpleNamespace(
                    at_list=None,
                    at=None,
                    target_id=None,
                    target_user_id=None,
                    content=[FakeMessage('at', {'qq': '123456789'})],
                    message=None,
                    original_message=None,
                    raw_text='',
                )
            ),
            '123456789',
        )

    def test_target_user_id_falls_back_to_personal_message_text(self) -> None:
        functions = _load_functions(
            {'_normalise_target_user_id', '_target_user_id_from_text', '_iter_event_messages', '_get_event_target_user_id'}
        )
        get_target = functions['_get_event_target_user_id']
        event = SimpleNamespace(
            at_list=None,
            at=None,
            target_id=None,
            target_user_id=None,
            content=None,
            message=None,
            original_message=None,
            text='抢老婆 QQ:123456789',
            raw_text='',
            raw_message='',
        )
        self.assertEqual(get_target(event), '123456789')

    def test_generic_send_boundary_only_removes_private_mentions(self) -> None:
        functions = _load_functions(
            {'_is_at_message', '_remove_private_mentions', '_adapt_mentions_for_platform'}
        )
        adapt = functions['_adapt_mentions_for_platform']
        outgoing = [FakeMessage('at', '123456789'), '\n', '结果文字', FakeMessage('image', 'x')]

        group_bot = SimpleNamespace(ev=SimpleNamespace(user_type='group'))
        self.assertIs(adapt(group_bot, outgoing), outgoing)

        direct_bot = SimpleNamespace(ev=SimpleNamespace(user_type='direct'))
        direct = adapt(direct_bot, outgoing)
        self.assertEqual(direct[0], '结果文字')
        self.assertEqual(direct[1].type, 'image')

    def test_result_image_senders_keep_personal_message_segments(self) -> None:
        source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        for function_name in ('_send_role_image', '_send_loli_result_image', '_send_local_image'):
            start = source.index(f'async def {function_name}(')
            next_function = source.find('\nasync def ', start + 1)
            block = source[start:next_function if next_function >= 0 else None]
            self.assertIn('MessageSegment.image(', block)

    def test_personal_compatibility_is_not_removed(self) -> None:
        source = (ROOT / 'twf' / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('_target_user_id_from_text', source)
        self.assertIn('_qq_avatar_url', source)
        self.assertIn('CQ:at', source)
        self.assertNotIn('_try_send_official_qq_image_markdown', source)
        self.assertNotIn('DailyWifeOfficialImageGalleryUrl', source)
        self.assertNotIn('DailyWifeOfficialImageGalleryToken', source)

    def test_private_account_prompts_remain_compatible(self) -> None:
        daily = (ROOT / 'twf' / 'daily.py').read_text(encoding='utf-8')
        rob = (ROOT / 'twf' / 'rob.py').read_text(encoding='utf-8')
        gift = (ROOT / 'twf' / 'gift.py').read_text(encoding='utf-8')
        self.assertIn('CQ:at', daily)
        self.assertIn('对方 QQ', rob)
        self.assertIn('对方 QQ', gift)

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
