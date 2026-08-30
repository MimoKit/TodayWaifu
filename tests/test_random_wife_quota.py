"""「来点老婆」每日次数限制的行为测试。

用 AST 抽取 shared.py 里的额度函数单独执行，避免导入 gsuid_core 运行时。
"""
import ast
import asyncio
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'twf' / 'shared.py'
RANDOM_WIFE = ROOT / 'twf' / 'random_wife.py'

QUOTA_FUNCTIONS = (
    '_random_wife_daily_limit',
    '_random_wife_used_count',
    '_consume_random_wife_quota',
    '_refund_random_wife_quota',
)


class _FakeEvent:
    def __init__(self, user_id: str, group_id: str | None) -> None:
        self.user_id = user_id
        self.group_id = group_id
        self.bot_id = 'onebot'


class _FakeLock:
    async def __aenter__(self) -> '_FakeLock':
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False


def _load_quota(
    limit_value: Any = 3,
    masters: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回 (命名空间, 模拟上下文表)。命名空间里是可直接调用的额度函数。"""
    tree = ast.parse(SHARED.read_text(encoding='utf-8-sig'))
    wanted = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in QUOTA_FUNCTIONS
    }
    missing = set(QUOTA_FUNCTIONS) - set(wanted)
    if missing:
        raise AssertionError(f'shared.py 缺少额度函数: {sorted(missing)}')

    contexts: dict[str, Any] = {}
    masters = masters or set()

    def context_key(ev: Any) -> str:
        return f'{ev.bot_id}:{ev.group_id or "direct"}'

    async def load_daily_context(ev: Any) -> dict[str, Any]:
        context = contexts.setdefault(context_key(ev), {})
        context.setdefault('random_wife_quota', {})
        return context

    async def save_daily_record(
        ev: Any, bucket: str, user_key: str, value: Any
    ) -> None:
        context = await load_daily_context(ev)
        context.setdefault(bucket, {})[user_key] = value

    async def delete_daily_record(ev: Any, bucket: str, user_key: str) -> None:
        context = await load_daily_context(ev)
        bucket_data = context.get(bucket)
        if isinstance(bucket_data, dict):
            bucket_data.pop(user_key, None)

    namespace: dict[str, Any] = {
        'Any': Any,
        'Event': _FakeEvent,
        'RANDOM_WIFE_QUOTA_BUCKET': 'random_wife_quota',
        'RANDOM_WIFE_DEFAULT_DAILY_LIMIT': 3,
        '_cfg': lambda _key: limit_value,
        '_is_master': lambda ev: str(ev.user_id) in masters,
        '_user_key': lambda ev, user_id=None: str(
            ev.user_id if user_id is None else user_id
        ),
        '_daily_context_lock': lambda _ev: _FakeLock(),
        '_load_daily_context': load_daily_context,
        '_save_daily_record': save_daily_record,
        '_delete_daily_record': delete_daily_record,
    }
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module='__future__',
                names=[ast.alias(name='annotations')],
                level=0,
            ),
            *(wanted[name] for name in QUOTA_FUNCTIONS),
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, str(SHARED), 'exec'), namespace)
    return namespace, contexts


def _quota_bucket(contexts: dict[str, Any], context: str) -> dict[str, Any]:
    return contexts.get(context, {}).get('random_wife_quota', {})


class RandomWifeQuotaTests(unittest.TestCase):
    def test_third_use_passes_and_fourth_is_rejected(self) -> None:
        namespace, contexts = _load_quota()
        consume = namespace['_consume_random_wife_quota']
        ev = _FakeEvent('u1', '1001')

        for expected_used in (1, 2, 3):
            allowed, used, limit = asyncio.run(consume(ev))
            self.assertTrue(allowed)
            self.assertEqual((used, limit), (expected_used, 3))

        allowed, used, limit = asyncio.run(consume(ev))
        self.assertFalse(allowed)
        self.assertEqual((used, limit), (3, 3))
        self.assertEqual(_quota_bucket(contexts, 'onebot:1001'), {'u1': 3})

    def test_quota_is_isolated_per_group_and_per_user(self) -> None:
        namespace, contexts = _load_quota()
        consume = namespace['_consume_random_wife_quota']

        for _ in range(3):
            asyncio.run(consume(_FakeEvent('u1', '1001')))

        # 同一个人换群后额度重新计算
        allowed, used, _ = asyncio.run(consume(_FakeEvent('u1', '2002')))
        self.assertTrue(allowed)
        self.assertEqual(used, 1)

        # 私聊单独算一份
        allowed, used, _ = asyncio.run(consume(_FakeEvent('u1', None)))
        self.assertTrue(allowed)
        self.assertEqual(used, 1)

        # 同群里的其他人互不影响
        allowed, used, _ = asyncio.run(consume(_FakeEvent('u2', '1001')))
        self.assertTrue(allowed)
        self.assertEqual(used, 1)

        self.assertEqual(_quota_bucket(contexts, 'onebot:1001'), {'u1': 3, 'u2': 1})
        self.assertEqual(_quota_bucket(contexts, 'onebot:2002'), {'u1': 1})
        self.assertEqual(_quota_bucket(contexts, 'onebot:direct'), {'u1': 1})

    def test_master_and_zero_limit_are_unlimited(self) -> None:
        namespace, contexts = _load_quota(masters={'boss'})
        consume = namespace['_consume_random_wife_quota']
        ev = _FakeEvent('boss', '1001')
        for _ in range(5):
            allowed, _, _ = asyncio.run(consume(ev))
            self.assertTrue(allowed)
        self.assertEqual(_quota_bucket(contexts, 'onebot:1001'), {})

        namespace, contexts = _load_quota(limit_value=0)
        consume = namespace['_consume_random_wife_quota']
        ev = _FakeEvent('u1', '1001')
        for _ in range(5):
            allowed, _, limit = asyncio.run(consume(ev))
            self.assertTrue(allowed)
            self.assertEqual(limit, 0)
        self.assertEqual(_quota_bucket(contexts, 'onebot:1001'), {})

    def test_invalid_limit_config_falls_back_to_default(self) -> None:
        for bad in ('', None, 'abc', -5):
            with self.subTest(bad=bad):
                namespace, _ = _load_quota(limit_value=bad)
                expected = 0 if bad == -5 else 3
                self.assertEqual(namespace['_random_wife_daily_limit'](), expected)

    def test_refund_gives_the_attempt_back_and_never_goes_negative(self) -> None:
        namespace, contexts = _load_quota()
        consume = namespace['_consume_random_wife_quota']
        refund = namespace['_refund_random_wife_quota']
        ev = _FakeEvent('u1', '1001')

        asyncio.run(consume(ev))
        asyncio.run(consume(ev))
        asyncio.run(refund(ev))
        self.assertEqual(_quota_bucket(contexts, 'onebot:1001'), {'u1': 1})

        # 退到 0 时直接删除记录，不留 0 值残留
        asyncio.run(refund(ev))
        self.assertEqual(_quota_bucket(contexts, 'onebot:1001'), {})

        # 没有计数时退还是空操作，不会出现负数
        asyncio.run(refund(ev))
        self.assertEqual(_quota_bucket(contexts, 'onebot:1001'), {})

    def test_corrupted_counter_value_is_treated_as_zero(self) -> None:
        namespace, _ = _load_quota()
        used_count = namespace['_random_wife_used_count']
        for raw in ('abc', None, {}, [], True):
            with self.subTest(raw=raw):
                context = {'random_wife_quota': {'u1': raw}}
                expected = 1 if raw is True else 0
                self.assertEqual(used_count(context, 'u1'), expected)
        self.assertEqual(used_count({}, 'u1'), 0)


class RandomWifeQuotaWiringTests(unittest.TestCase):
    def test_command_consumes_quota_before_fetching_and_refunds_on_failure(self) -> None:
        source = RANDOM_WIFE.read_text(encoding='utf-8-sig')
        consume_at = source.index('_consume_random_wife_quota(ev)')
        fetch_at = source.index('_fetch_gallery_payload_from_url_sync')
        self.assertLess(consume_at, fetch_at, '额度必须在请求图库之前占用')
        self.assertEqual(source.count('_refund_random_wife_quota(ev)'), 2)

    def test_quota_bucket_is_registered_in_daily_context(self) -> None:
        shared = SHARED.read_text(encoding='utf-8-sig')
        self.assertIn('context.setdefault(RANDOM_WIFE_QUOTA_BUCKET, {})', shared)
        self.assertIn("RANDOM_WIFE_QUOTA_BUCKET = 'random_wife_quota'", shared)

    def test_quota_never_touches_marriage_buckets(self) -> None:
        tree = ast.parse(SHARED.read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in QUOTA_FUNCTIONS:
                continue
            body = ast.unparse(node)
            for bucket in ("'wives'", "'husbands'", "'lolis'", "'safe_wives'"):
                self.assertNotIn(bucket, body, f'{node.name} 不应触碰 {bucket}')


if __name__ == '__main__':
    unittest.main()
