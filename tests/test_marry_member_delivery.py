"""娶群友统一投递的回归：单头像，以及受开关控制的群聊提及。"""
from __future__ import annotations

import ast
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = {
    name: ROOT / 'TodayWaifu' / name
    for name in ('senders.py', 'qq_official.py', 'delivery.py', 'members.py')
}


class _Message:
    def __init__(self, type: str, data: Any) -> None:
        self.type = type
        self.data = data


def _markdown(text: str, buttons: object = None) -> list[_Message]:
    # 与框架一致返回扁平消息列表，嵌套 append 的回归会在出站断言中失败。
    return [_Message('markdown', text)]


def _load_delivery(config: dict[str, Any]) -> tuple[dict[str, Any], list[str], AsyncMock]:
    """运行真实投递与发送兼容函数，仅隔离框架引导、图片编码和外部 I/O。"""
    functions = {
        '_send_marry_member_result_image', '_fetch_member_avatar_bytes',
        '_send_plain_member_avatar', '_qq_bot_mode_enabled',
        '_is_official_qq_bot', '_is_official_qq_event', '_markdown_escape',
        '_safe_send', '_adapt_mentions_for_platform', '_remove_private_mentions',
        '_is_at_message',
        '_resolve_member_avatar', '_valid_member_text', '_qq_avatar_url',
        '_read_legacy_member_avatar',
    }
    constants = {'QQ_OFFICIAL_BOT_IDS'}
    body: list[ast.stmt] = [
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
    ]
    for path in SOURCE_FILES.values():
        for node in ast.parse(path.read_text(encoding='utf-8-sig')).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
                body.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in constants for target in node.targets
            ):
                body.append(node)

    downloads: list[str] = []

    def download_avatar(url: str) -> bytes | None:
        downloads.append(url)
        return None if url.endswith('/missing') else b'MEMBER_AVATAR'

    async def run_inline(function: Any, *args: Any) -> Any:
        return function(*args)

    async def image_message(data: bytes) -> _Message:
        return _Message('image', data)

    upload = AsyncMock(side_effect=AssertionError('娶群友不应上传 COS'))
    namespace: dict[str, Any] = {
        'Message': _Message,
        'MessageSegment': SimpleNamespace(
            at=lambda user_id: _Message('at', user_id), markdown=_markdown,
        ),
        '_cfg': lambda key: config.get(key),
        '_cfg_bool': lambda key, default=False: bool(config.get(key, default)),
        '_download_member_avatar': download_avatar,
        '_upload_image': upload,
        '_image_message': image_message,
        '_shrink_image_sync': lambda image: image,
        'run_blocking': run_inline,
        'logger': SimpleNamespace(warning=lambda *args: None),
        'LOG_PREFIX': '[TEST]',
        'Path': Path,
    }
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(module, 'marry_member_delivery', 'exec'), namespace)
    return namespace, downloads, upload


def _outgoing(message: Any) -> Any:
    if isinstance(message, list):
        result = []
        for item in message:
            if isinstance(item, list):
                raise AssertionError('出站消息含嵌套列表，Markdown 消息段必须展开')
            result.append(_outgoing(item))
        return result
    if isinstance(message, _Message):
        return message.type, message.data
    return message


class MarryMemberDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_avatar_and_mentions_follow_config_and_platform(self) -> None:
        text = '你今天娶到的群友是 a*b_c[d]'
        escaped = '你今天娶到的群友是 a\\*b\\_c\\[d\\]'
        image = ('image', b'MEMBER_AVATAR')
        cases = [
            ('official', True, 'qqgroup:1', True, True, 'U1', text,
             [('markdown', f'<@U1>\n\n{escaped}'), image]),
            ('official-guild', True, 'qqguild:1', True, True, 'U1', text,
             [('markdown', f'<@U1>\n\n{escaped}'), image]),
            ('mode-off', False, 'qqgroup:1', True, True, 'U1', text,
             [('at', 'U1'), '\n', text, image]),
            ('mode-unset', None, 'qqgroup:1', True, True, 'U1', text,
             [('at', 'U1'), '\n', text, image]),
            ('personal-account', True, 'onebot:1', True, True, 'U1', text,
             [('at', 'U1'), '\n', text, image]),
            ('mentions-off', True, 'qqgroup:1', False, True, 'U1', text,
             [text, image]),
            ('direct', True, 'qqgroup:1', True, False, 'U1', text,
             [text, image]),
            ('no-user', True, 'qqgroup:1', True, True, None, text,
             [text, image]),
            ('official-no-text', True, 'qqgroup:1', True, True, 'U1', None,
             [('markdown', '<@U1>'), image]),
            ('personal-no-text', False, 'onebot:1', True, True, 'U1', None,
             [('at', 'U1'), '\n', image]),
            ('direct-no-text', True, 'qqgroup:1', True, False, 'U1', None, image),
        ]
        for label, mode, platform, at_user, group, user_id, caption, expected in cases:
            with self.subTest(label=label):
                config = {'DailyWifeAtUser': at_user}
                if mode is not None:
                    config['DailyWifeQqBotMode'] = mode
                namespace, downloads, upload = _load_delivery(config)
                sent = AsyncMock()
                bot = SimpleNamespace(
                    ev=SimpleNamespace(
                        real_bot_id=platform, bot_id=platform, user_id='U1',
                        user_type='group' if group else 'direct', bot_self_id='BOT',
                        sender={'avatar': 'https://avatar.test/CALLER'},
                    ),
                    send=sent,
                )
                member = SimpleNamespace(
                    name='a*b_c[d]', user_id='U2', avatar='https://avatar.test/MEMBER',
                )
                await namespace['_send_marry_member_result_image'](
                    bot, member, caption, user_id, group,
                )
                self.assertEqual(sent.await_count, 1)
                self.assertEqual(_outgoing(sent.await_args.args[0]), expected)
                self.assertEqual(downloads, ['https://avatar.test/MEMBER'])
                upload.assert_not_awaited()

    async def test_legacy_numeric_avatar_path_uses_current_qq_avatar(self) -> None:
        namespace, downloads, _ = _load_delivery({})
        with tempfile.TemporaryDirectory() as directory:
            avatar = Path(directory) / 'old-avatar.png'
            avatar.write_bytes(b'OLD_CACHED_AVATAR')
            member = SimpleNamespace(user_id='123456789', avatar=str(avatar))
            result = await namespace['_fetch_member_avatar_bytes'](member)

        self.assertEqual(result, b'MEMBER_AVATAR')
        self.assertEqual(downloads, ['https://q1.qlogo.cn/g?b=qq&nk=123456789&s=640'])

    async def test_legacy_openid_avatar_path_remains_readable(self) -> None:
        namespace, downloads, _ = _load_delivery({})
        with tempfile.TemporaryDirectory() as directory:
            avatar = Path(directory) / 'openid-avatar.png'
            for contents in (b'OLD_OPENID_AVATAR', b'x' * (2 * 1024 * 1024)):
                with self.subTest(size=len(contents)):
                    avatar.write_bytes(contents)
                    member = SimpleNamespace(user_id='OPENID_ABC', avatar=str(avatar))
                    self.assertEqual(
                        await namespace['_fetch_member_avatar_bytes'](member), contents,
                    )
                    self.assertEqual(avatar.read_bytes(), contents)

        self.assertEqual(downloads, [])

    async def test_invalid_legacy_openid_avatar_fails_without_network_or_writes(self) -> None:
        namespace, downloads, _ = _load_delivery({})
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            empty = folder / 'empty.png'
            empty.touch()
            oversized = folder / 'oversized.png'
            oversized.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
            before = {path.name: path.stat().st_size for path in folder.iterdir()}
            for path in (folder / 'missing.png', empty, oversized):
                with self.subTest(path=path.name):
                    member = SimpleNamespace(user_id='OPENID_ABC', avatar=str(path))
                    self.assertIsNone(await namespace['_fetch_member_avatar_bytes'](member))
            self.assertEqual(
                {path.name: path.stat().st_size for path in folder.iterdir()}, before,
            )

        self.assertEqual(downloads, [])

    async def test_unavailable_avatar_gives_one_error_without_extra_network_requests(self) -> None:
        for avatar in ('', 'https://avatar.test/missing'):
            with self.subTest(avatar=avatar):
                namespace, downloads, upload = _load_delivery({
                    'DailyWifeQqBotMode': True, 'DailyWifeAtUser': True,
                })
                sent = AsyncMock()
                bot = SimpleNamespace(
                    ev=SimpleNamespace(
                        real_bot_id='qqgroup:1', bot_id='qqgroup:1', user_type='group',
                    ),
                    send=sent,
                )
                member = SimpleNamespace(name='群友', user_id='U2', avatar=avatar)
                await namespace['_send_marry_member_result_image'](
                    bot, member, '结果文案', 'U1', True,
                )
                sent.assert_awaited_once_with('本地群友头像文件不存在，请稍后重试。')
                self.assertEqual(downloads, [avatar] if avatar else [])
                upload.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
