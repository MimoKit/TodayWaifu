import ast
import asyncio
import random
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]


class MemberCandidate:
    def __init__(self, name: str, user_id: str, avatar: str):
        self.name = name
        self.user_id = user_id
        self.avatar = avatar


def _load_module_functions() -> dict:
    wanted = {'_pick_group_member', '_loli_enabled'}
    body: list[ast.stmt] = [
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
    ]
    for path in sorted((ROOT / 'twf').glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        body.extend(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
        )
    namespace = {
        'Event': object,
        'MemberCandidate': MemberCandidate,
        'random': random,
        'logger': SimpleNamespace(debug=lambda *a: None, warning=lambda *a: None),
        'LOG_PREFIX': '[TEST]',
        '_cfg_bool': lambda key, default=False: default,
    }
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, 'test_fns', 'exec'), namespace)
    return namespace


class MarryMemberSelfExclusionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.ns = _load_module_functions()
        self.pick_fn = self.ns['_pick_group_member']

    async def test_pick_group_member_excludes_caller_and_bot(self) -> None:
        ev = SimpleNamespace(
            user_id="123456",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Self", user_id="123456", avatar="avatar_self"),
            MemberCandidate(name="Bot", user_id="999999", avatar="avatar_bot"),
            MemberCandidate(name="Friend", user_id="654321", avatar="avatar_friend"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42))
        self.assertIsNotNone(picked)
        self.assertEqual(picked.user_id, "654321")
        self.assertEqual(picked.name, "Friend")

    async def test_pick_group_member_returns_none_when_only_caller_and_bot_present(self) -> None:
        ev = SimpleNamespace(
            user_id="123456",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Self", user_id="123456", avatar="avatar_self"),
            MemberCandidate(name="Bot", user_id="999999", avatar="avatar_bot"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42))
        self.assertIsNone(picked)

    async def test_pick_group_member_respects_custom_exclude_user_id(self) -> None:
        ev = SimpleNamespace(
            user_id="caller",
            bot_self_id="999999",
            group_id="888888",
            bot_id="onebot",
        )
        candidates = (
            MemberCandidate(name="Target", user_id="777777", avatar="avatar_target"),
            MemberCandidate(name="Friend", user_id="654321", avatar="avatar_friend"),
        )

        self.ns['_load_group_member_candidates'] = AsyncMock(return_value=candidates)
        self.ns['_resolve_member_candidate_avatar'] = lambda m: asyncio.sleep(0, m)

        picked = await self.pick_fn(ev, random.Random(42), exclude_user_id="777777")
        self.assertIsNotNone(picked)
        self.assertEqual(picked.user_id, "654321")


class DailyLoliConfigTests(unittest.TestCase):
    def test_loli_enabled_defaults_to_true(self) -> None:
        ns = _load_module_functions()
        self.assertTrue(ns['_loli_enabled']())


if __name__ == "__main__":
    unittest.main()
