"""targets.py 的行为契约：@ / 富文本 / 纯文本三种上报形态的解析优先级。

这个模块没有任何测试覆盖，但它决定「抢老婆 / 送老婆」作用于谁——解析错了会让命令
静默作用在自己身上或错误的目标身上，属于会伤到用户的缺陷。
"""
from __future__ import annotations

import sys
import unittest
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'


def _load(name: str, filename: str):
    """按文件路径单独加载，绕过插件包的 __init__（它要导入 gsuid_core）。

    targets.py 只依赖 gsuid_core.models 的两个类型，测试里用替身即可。
    """
    spec = importlib.util.spec_from_file_location(name, PLUGIN / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {filename}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# gsuid_core 在测试环境不存在时塞入替身，targets 只用到 Event / Message 做类型判断
if 'gsuid_core.models' not in sys.modules:
    import types

    fake = types.ModuleType('gsuid_core.models')

    class Event:  # noqa: D101
        pass

    class Message:  # noqa: D101
        def __init__(self, type='', data=None):  # noqa: A002
            self.type = type
            self.data = data or {}

    fake.Event = Event
    fake.Message = Message
    sys.modules['gsuid_core'] = types.ModuleType('gsuid_core')
    sys.modules['gsuid_core.models'] = fake

targets = _load('test_targets_mod', 'targets.py')

_self_user_ids = targets._self_user_ids
_normalise_target_user_id = targets._normalise_target_user_id
_get_event_target_user_id = targets._get_event_target_user_id
_target_user_id_from_text = targets._target_user_id_from_text


class NormaliseTargetUserIdTests(unittest.TestCase):
    """把异构上报字段归一成可用标识。"""

    def test_plain_string_passes_through(self) -> None:
        self.assertEqual(_normalise_target_user_id('123456'), '123456')

    def test_strips_whitespace(self) -> None:
        self.assertEqual(_normalise_target_user_id('  123456  '), '123456')

    def test_none_and_bool_are_rejected(self) -> None:
        self.assertEqual(_normalise_target_user_id(None), '')
        self.assertEqual(_normalise_target_user_id(True), '')
        self.assertEqual(_normalise_target_user_id(False), '')

    def test_placeholder_strings_are_rejected(self) -> None:
        """适配器把缺失值序列化成这些占位串。"""
        for text in ('', 'none', 'None', 'true', 'false', 'ALL'):
            with self.subTest(text=text):
                self.assertEqual(_normalise_target_user_id(text), '')

    def test_all_is_rejected_because_it_means_at_everyone(self) -> None:
        """'all' 表示 @全体成员 —— 当成目标会误伤整群。"""
        self.assertEqual(_normalise_target_user_id('all'), '')

    def test_dict_tries_common_field_names_in_order(self) -> None:
        self.assertEqual(_normalise_target_user_id({'user_id': '111'}), '111')
        self.assertEqual(_normalise_target_user_id({'qq': '222'}), '222')
        self.assertEqual(_normalise_target_user_id({'openid': 'abc123'}), 'abc123')
        self.assertEqual(_normalise_target_user_id({'id': '333'}), '333')

    def test_dict_prefers_earlier_field(self) -> None:
        self.assertEqual(
            _normalise_target_user_id({'user_id': 'first', 'qq': 'second'}), 'first'
        )

    def test_dict_skips_empty_values(self) -> None:
        self.assertEqual(_normalise_target_user_id({'user_id': '', 'qq': '222'}), '222')

    def test_empty_dict_returns_empty_string(self) -> None:
        self.assertEqual(_normalise_target_user_id({}), '')


class TargetUserIdFromTextTests(unittest.TestCase):
    """从纯文本里抠出目标标识。"""

    def test_cq_at_code(self) -> None:
        self.assertEqual(_target_user_id_from_text('[CQ:at,qq=123456]'), '123456')

    def test_cq_at_with_extra_params(self) -> None:
        self.assertEqual(
            _target_user_id_from_text('[CQ:at,type=at,qq=987654,name=x]'), '987654'
        )

    def test_html_at_tag(self) -> None:
        # 标识至少 5 位，正则里的 {5,} 是有意的长度约束
        self.assertEqual(_target_user_id_from_text('<at id="55555">'), '55555')
        self.assertEqual(_target_user_id_from_text("<at user_id='66666'>"), '66666')

    def test_qq_equals_form(self) -> None:
        self.assertEqual(_target_user_id_from_text('抢老婆 qq=777888'), '777888')
        self.assertEqual(_target_user_id_from_text('抢老婆 QQ:999000'), '999000')

    def test_bare_at_prefix(self) -> None:
        self.assertEqual(_target_user_id_from_text('@123456789'), '123456789')

    def test_plain_digits(self) -> None:
        self.assertEqual(_target_user_id_from_text('抢 12345678 的老婆'), '12345678')

    def test_openid_hex_form(self) -> None:
        """QQ 官方机器人没有数字号，用 16 位以上 hex openid。"""
        openid = 'ABCDEF0123456789ABCD'
        self.assertEqual(_target_user_id_from_text(f'@{openid}'), openid)

    def test_cq_at_wins_over_bare_digits(self) -> None:
        """顺序即优先级：CQ 码比裸数字更可信。"""
        text = '[CQ:at,qq=111111] 另外 999999'
        self.assertEqual(_target_user_id_from_text(text), '111111')

    def test_returns_none_for_text_without_identifier(self) -> None:
        self.assertIsNone(_target_user_id_from_text('抢老婆'))
        self.assertIsNone(_target_user_id_from_text(''))

    def test_short_digits_are_rejected(self) -> None:
        """少于 5 位不认为是用户标识。"""
        self.assertIsNone(_target_user_id_from_text('1234'))


class SelfUserIdsTests(unittest.TestCase):
    """收集机器人自身标识，用于 @机器人 时排除。"""

    def test_collects_all_three_fields(self) -> None:
        class FakeEvent:
            bot_self_id = 'bot1'
            self_id = 'bot2'
            real_bot_id = 'bot3'

        self.assertEqual(_self_user_ids(FakeEvent()), {'bot1', 'bot2', 'bot3'})

    def test_skips_missing_and_empty(self) -> None:
        class FakeEvent:
            bot_self_id = 'only'
            self_id = ''

            @property
            def real_bot_id(self):
                raise AttributeError('missing')

        self.assertEqual(_self_user_ids(FakeEvent()), {'only'})

    def test_returns_empty_set_when_nothing_set(self) -> None:
        class FakeEvent:
            pass

        self.assertEqual(_self_user_ids(FakeEvent()), set())


class GetEventTargetUserIdTests(unittest.TestCase):
    """完整解析链：结构化字段 → 消息段 → 原始文本，且排除自身。"""

    def _event(self, **kwargs):
        defaults = {
            'at_list': None, 'at': None, 'content': None,
            'text': '', 'raw_text': '',
            'bot_self_id': '', 'self_id': '', 'real_bot_id': '',
        }
        defaults.update(kwargs)
        return type('FakeEvent', (), defaults)()

    def test_at_list_wins(self) -> None:
        ev = self._event(at_list=['123456'], text='@999999')
        self.assertEqual(_get_event_target_user_id(ev), '123456')

    def test_at_list_takes_any_element(self) -> None:
        ev = self._event(at_list={'aaa', 'bbb'})
        self.assertIn(_get_event_target_user_id(ev), {'aaa', 'bbb'})

    def test_at_falls_back_to_text_field(self) -> None:
        ev = self._event(text='抢老婆 @777888')
        self.assertEqual(_get_event_target_user_id(ev), '777888')

    def test_self_id_is_excluded(self) -> None:
        """@机器人不算目标，否则命令会作用在机器人自己身上。"""
        ev = self._event(at_list=['bot_id'], bot_self_id='bot_id', text='@123456')
        self.assertEqual(_get_event_target_user_id(ev), '123456')

    def test_self_id_only_returns_none(self) -> None:
        ev = self._event(at_list=['bot_id'], bot_self_id='bot_id')
        self.assertIsNone(_get_event_target_user_id(ev))

    def test_rich_text_in_at_field_falls_back_to_text_parsing(self) -> None:
        """结构化字段偶尔直接承载整段富文本。"""
        ev = self._event(at='[CQ:at,qq=123456]')
        self.assertEqual(_get_event_target_user_id(ev), '123456')

    def test_rich_text_at_self_is_excluded(self) -> None:
        ev = self._event(at='[CQ:at,qq=bot_id]', bot_self_id='bot_id')
        self.assertIsNone(_get_event_target_user_id(ev))

    def test_no_target_returns_none(self) -> None:
        """解析失败必须返回 None —— 不能回退到发送者本人。"""
        ev = self._event(text='抢老婆')
        self.assertIsNone(_get_event_target_user_id(ev))

    def test_empty_event_returns_none(self) -> None:
        self.assertIsNone(_get_event_target_user_id(self._event()))


if __name__ == '__main__':
    unittest.main()
