import ast
import copy
import asyncio
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, AsyncMock

ROOT = Path(__file__).resolve().parents[1]


def _extract_function(filename, name, namespace):
    tree = ast.parse((ROOT / 'TodayWaifu' / filename).read_text(encoding='utf-8-sig'))
    function = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
            function,
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, filename, 'exec'), namespace)
    return namespace[name]


class SafeWifeDivorceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ev = SimpleNamespace(user_id='u1', group_id='g1')
        self.bot = object()
        self.context = {
            'wives': {'u1': {'name': '旧老婆', 'stolen_by': 'u2'}},
            'safe_wives': {'u1': {'name': '新老婆', 'safe': True}},
            'husbands': {'u1': {'name': '老公'}},
            'marry_members': {},
        }
        self.messages = AsyncMock()
        self.saved = AsyncMock(side_effect=self._save)
        self.namespace = {
            'LOG_PREFIX': '',
            'logger': Mock(),
            'time': SimpleNamespace(time=lambda: 123),
            '_user_key': lambda ev: ev.user_id,
            '_safe_send': self.messages,
            '_daily_bucket_name': {'wife': 'wives', 'husband': 'husbands'}.__getitem__,
            '_daily_context_lock': lambda ev: self.lock,
            '_load_daily_context': AsyncMock(return_value=self.context),
            '_save_daily_records': self.saved,
            '_divorce_result_name': lambda kind, name: name,
        }
        self.lock = asyncio.Lock()
        self.namespace['_wife_state'] = _extract_function('daily_store.py', '_wife_state', {})
        self.divorce = _extract_function('divorce.py', '_send_divorce', self.namespace)

    async def _save(self, ev, updates):
        for bucket, user, record in updates:
            self.context[bucket][user] = record

    async def test_divorce_targets_compensation_and_preserves_stolen_history(self):
        original = copy.deepcopy(self.context['wives']['u1'])
        await self.divorce(self.bot, self.ev, 'wife')
        self.assertEqual(self.context['wives']['u1'], original)
        self.assertTrue(self.context['safe_wives']['u1']['divorced'])
        self.assertEqual(self.context['safe_wives']['u1']['divorced_at'], 123)
        self.messages.assert_awaited_once_with(self.bot, '已经和今天的老婆离婚：新老婆。')

    async def test_repeated_and_concurrent_divorces_only_save_once(self):
        await asyncio.gather(
            self.divorce(self.bot, self.ev, 'wife'),
            self.divorce(self.bot, self.ev, 'wife'),
        )
        self.saved.assert_awaited_once()
        self.assertEqual(self.messages.await_args_list[-1].args[1], '你今天已经和老婆离婚了。')

    async def test_stolen_wife_without_compensation_cannot_be_divorced(self):
        self.context['safe_wives'].clear()
        await self.divorce(self.bot, self.ev, 'wife')
        self.saved.assert_not_awaited()
        self.messages.assert_awaited_once_with(self.bot, '你今天没有可以离婚的老婆。')

    async def test_legacy_divorced_original_still_targets_compensation(self):
        self.context['wives']['u1']['divorced'] = True
        await self.divorce(self.bot, self.ev, 'wife')
        self.assertTrue(self.context['safe_wives']['u1']['divorced'])
        self.messages.assert_awaited_once_with(self.bot, '已经和今天的老婆离婚：新老婆。')

    async def test_owned_wife_and_other_kinds_do_not_target_compensation(self):
        del self.context['wives']['u1']['stolen_by']
        await self.divorce(self.bot, self.ev, 'wife')
        await self.divorce(self.bot, self.ev, 'husband')
        self.assertTrue(self.context['wives']['u1']['divorced'])
        self.assertTrue(self.context['husbands']['u1']['divorced'])
        self.assertNotIn('divorced', self.context['safe_wives']['u1'])

    async def test_divorce_marry_member_by_specific_command(self):
        self.ev.command = '离婚群友'
        self.context['marry_members']['u1'] = {'name': '群友A'}
        await self.divorce(self.bot, self.ev, 'wife')
        self.assertTrue(self.context['marry_members']['u1']['divorced'])
        self.assertEqual(self.context['marry_members']['u1']['divorced_at'], 123)
        self.messages.assert_awaited_once_with(self.bot, '已经和今天的群友离婚：群友A。')
        self.assertNotIn('divorced', self.context['safe_wives']['u1'])

    async def test_divorce_marry_member_when_no_member_married(self):
        self.ev.command = '离婚群友'
        await self.divorce(self.bot, self.ev, 'wife')
        self.saved.assert_not_awaited()
        self.messages.assert_awaited_once_with(self.bot, '你今天没有可以离婚的群友。')
        self.assertNotIn('divorced', self.context['safe_wives']['u1'])

    async def test_divorce_marry_member_already_divorced(self):
        self.ev.command = '离婚群友'
        self.context['marry_members']['u1'] = {'name': '群友A', 'divorced': True}
        await self.divorce(self.bot, self.ev, 'wife')
        self.saved.assert_not_awaited()
        self.messages.assert_awaited_once_with(self.bot, '你今天已经和群友离婚了。')

    async def test_divorce_fallback_to_marry_member_when_no_wife(self):
        del self.context['wives']['u1']
        del self.context['safe_wives']['u1']
        self.context['marry_members']['u1'] = {'name': '群友B'}
        await self.divorce(self.bot, self.ev, 'wife')
        self.assertTrue(self.context['marry_members']['u1']['divorced'])
        self.messages.assert_awaited_once_with(self.bot, '已经和今天的群友离婚：群友B。')

    def _daily(self):
        self.namespace.update({
            '_daily_item_title': lambda mode: '老婆',
            '_is_master': lambda ev: False,
            '_cfg_bool': lambda key, default: False,
            '_can_specify_wife': lambda ev: False,
            '_normalize_role_name': lambda name: name,
            '_get_other_daily_wife_name': AsyncMock(return_value=None),
            '_record_from_dict': Mock(return_value=SimpleNamespace(name='新老婆')),
            '_send_record_image': AsyncMock(),
            '_load_candidates': AsyncMock(return_value=([object()], None)),
            '_daily_rng': Mock(),
            '_filter_by_mode': lambda candidates, mode: candidates,
            '_pick_role_record': Mock(return_value=SimpleNamespace(name='候选老婆')),
            '_record_to_dict': Mock(return_value={'name': '候选老婆', 'safe': True}),
        })
        return _extract_function('daily.py', '_send_daily_wife', self.namespace)

    async def test_divorced_compensation_is_not_displayed_or_redrawn(self):
        daily = self._daily()
        await self.divorce(self.bot, self.ev, 'wife')
        self.messages.reset_mock()
        await daily(self.bot, self.ev)
        self.messages.assert_awaited_once_with(self.bot, '你今天已经和新老婆离婚了，明天再来吧~')
        self.namespace['_send_record_image'].assert_not_awaited()
        self.namespace['_load_candidates'].assert_not_awaited()

    async def test_active_compensation_is_still_displayed(self):
        daily = self._daily()
        await daily(self.bot, self.ev)
        self.namespace['_send_record_image'].assert_awaited_once()
        self.namespace['_load_candidates'].assert_not_awaited()
        self.saved.assert_not_awaited()

    async def test_divorce_during_candidate_loading_cannot_resurrect_compensation(self):
        daily = self._daily()
        # 模拟旧图片失效触发候选加载，期间另一请求完成离婚。
        self.namespace['_record_from_dict'].return_value = None

        async def load_candidates(mode):
            await self.divorce(self.bot, self.ev, 'wife')
            return [object()], None

        self.namespace['_load_candidates'].side_effect = load_candidates
        await daily(self.bot, self.ev)
        self.saved.assert_awaited_once()
        self.assertTrue(self.context['safe_wives']['u1']['divorced'])
        self.assertEqual(
            self.messages.await_args_list[-1].args[1],
            '你今天已经和新老婆离婚了，明天再来吧~',
        )


if __name__ == '__main__':
    unittest.main()
