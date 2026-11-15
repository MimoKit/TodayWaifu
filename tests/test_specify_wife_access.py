"""指定老婆、分配老婆/老公与图片上传的权限契约：统一由 GsCore `SV` 层默认 `pm=1` 托管。

插件不再维护独立的白名单配置项或处理器内二次鉴权门：
1. 四个管理/特权 `SV`（指定老婆、分配老婆、分配老公、图片上传）默认 `pm=1`（仅主人），
   普通随机抽取 `SV` 保持默认 `pm=6`（全员可用）。
2. 当管理员在 GsCore 网页控制台调低对应 `SV` 的 `pm` 或配置 `SV` 白名单放行非主人用户时，
   处理器内部不得再以主人身份或旧白名单拦截请求。
"""

from __future__ import annotations

import ast
import asyncio
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, AsyncMock

ROOT = Path(__file__).resolve().parents[1]
SHARED_PATH = ROOT / 'TodayWaifu' / 'shared.py'
DAILY_PATH = ROOT / 'TodayWaifu' / 'daily.py'


class _FakeSV:
    """复刻 GsCore `SV` 默认 `pm=6` 的构造语义，防止桩对象掩盖默认权限。"""

    def __init__(self, name: str = '', pm: int = 6, priority: int = 5, **kwargs: object) -> None:
        self.name = name
        self.pm = pm
        self.priority = priority
        self.kwargs = kwargs


def _load_shared_svs() -> dict[str, _FakeSV]:
    tree = ast.parse(SHARED_PATH.read_text(encoding='utf-8-sig'))
    sv_assigns = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'SV'
    ]
    module = ast.Module(body=sv_assigns, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {'SV': _FakeSV}
    exec(compile(module, str(SHARED_PATH), 'exec'), namespace)  # noqa: S102
    return {key: value for key, value in namespace.items() if isinstance(value, _FakeSV)}


def _extract_async_function(path: Path, name: str, namespace: dict[str, object]):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, function], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), 'exec'), namespace)  # noqa: S102
    return namespace[name]


class PrivilegedSvAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_privileged_services_default_to_pm1_while_daily_draws_stay_open(self) -> None:
        svs = _load_shared_svs()
        for sv_var in (
            'specify_wife_sv',
            'assign_wife_sv',
            'assign_husband_sv',
            'image_upload_sv',
            'custom_role_sv',
            'loli_manage_sv',
        ):
            with self.subTest(sv=sv_var):
                self.assertEqual(svs[sv_var].pm, 1)
                self.assertEqual(svs[sv_var].priority, 2)

        for open_sv in (
            'daily_wife_sv',
            'daily_husband_sv',
            'daily_nte_wife_sv',
            'pgr_wife_sv',
            'daily_normal_wife_sv',
        ):
            with self.subTest(sv=open_sv):
                self.assertEqual(svs[open_sv].pm, 6)

    async def test_specify_handler_allows_non_master_once_sv_permits_entry(self) -> None:
        """管理员把 `specify_wife_sv` 调低权限放行普通成员后，处理器必须直接执行指定角色。"""
        role = SimpleNamespace(name='今汐', role_ids=('1304',), images=('https://example.com/jinxi.png',))
        chosen = SimpleNamespace(
            name='今汐',
            record_type='role',
            image='https://example.com/jinxi.png',
            to_role=lambda: role,
        )
        ensure_record = AsyncMock(return_value=chosen)
        send_record = AsyncMock()
        safe_send = AsyncMock()
        namespace: dict[str, object] = {
            'LOG_PREFIX': '[TodayWaifu]',
            'logger': Mock(),
            '_daily_item_title': lambda mode: '老婆',
            '_is_master': lambda ev: False,
            '_cfg_bool': lambda key, default=False: False,
            '_normalize_role_name': lambda name: name.strip(),
            '_load_candidates': AsyncMock(return_value=((role,), None)),
            '_get_other_daily_wife_name': AsyncMock(return_value=None),
            '_load_daily_context': AsyncMock(return_value={'wives': {}, 'safe_wives': {}}),
            '_user_key': lambda ev: str(ev.user_id),
            '_daily_bucket_name': lambda mode: 'wives',
            '_wife_state': lambda raw: 'none',
            '_ensure_daily_wife_record': ensure_record,
            '_send_record_image': send_record,
            '_safe_send': safe_send,
        }
        handler = _extract_async_function(DAILY_PATH, '_send_daily_wife', namespace)
        ev = SimpleNamespace(user_id='30001', group_id='100', user_pm=6)

        await handler(object(), ev, mode='wife', specified_name='今汐')

        safe_send.assert_not_awaited()
        ensure_record.assert_awaited_once_with(ev, mode='wife', specified_role=role)
        send_record.assert_awaited_once()

    async def test_assign_wife_handler_allows_non_master_once_sv_permits_entry(self) -> None:
        """管理员把 `assign_wife_sv` 调低权限放行非主人后，分配处理器不得再在内部拦截。"""
        role = SimpleNamespace(name='今汐', role_ids=('1304',))
        chosen = SimpleNamespace(name='今汐', image='https://example.com/jinxi.png')
        save_records = AsyncMock()
        send_role = AsyncMock()
        safe_send = AsyncMock()
        lock = asyncio.Lock()
        namespace: dict[str, object] = {
            'LOG_PREFIX': '[TodayWaifu]',
            'logger': Mock(),
            'random': Mock(),
            '_get_event_target_user_id': lambda ev: '30002',
            '_assignment_role_name': lambda ev, target: '今汐',
            '_load_candidates': AsyncMock(return_value=((role,), None)),
            '_filter_by_mode': lambda candidates, mode: candidates,
            '_find_assignable_wife': lambda candidates, name: role,
            '_pick_role_record': lambda candidates, rng: chosen,
            '_record_to_dict': lambda record, ev, key: {'name': record.name, 'image': record.image},
            '_user_key': lambda ev: str(ev.user_id),
            'name_from_event': lambda ev: '群管理A',
            '_daily_context_lock': lambda ev: lock,
            '_save_daily_records': save_records,
            '_send_role_image': send_role,
            '_safe_send': safe_send,
        }
        handler = _extract_async_function(DAILY_PATH, '_send_assign_wife', namespace)
        ev = SimpleNamespace(user_id='20001', group_id='100', user_pm=3)

        await handler(object(), ev)

        safe_send.assert_not_awaited()
        save_records.assert_awaited_once()
        send_role.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
