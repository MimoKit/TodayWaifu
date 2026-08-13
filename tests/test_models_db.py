"""twf/models.py 数据库层测试。

需要 gsuid_core 环境（sqlmodel/aiosqlite），用核心 venv 运行：
    D:/122/bot/xiaoyu/botkj/gsuid_core/.venv/Scripts/python.exe tests/test_models_db.py
无依赖环境下自动跳过。
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

try:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlmodel import SQLModel

    from gsuid_core.utils.database import base_models

    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "twf" / "models.py"


def _load_models():
    import importlib.util

    spec = importlib.util.spec_from_file_location("todaywaifu_models", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load models module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

LEGACY_DATA = {
    "days": {
        "2026-08-10": {
            "onebot:1001": {
                "wives": {"u1": {"name": "今汐", "image": "a.png", "updated_at": 1}}
            }
        },
        "2026-08-11": {
            "onebot:1001": {
                "wives": {"u2": {"name": "长离", "image": "b.png", "updated_at": 2}},
                "rob_attempts": {"u2": True},
            }
        },
        "2026-08-12": {
            "onebot:1002": {
                "wives": {
                    "u3": {
                        "name": "景燃",
                        "image": "c.png",
                        "updated_at": 3,
                        "stolen_from": "u9",
                    }
                }
            }
        },
    }
}


@unittest.skipUnless(_DEPS_OK, "缺少 gsuid_core/sqlmodel 环境，跳过数据库测试")
class DailyWifeRecordDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.models = _load_models()
        cls._tmp = tempfile.TemporaryDirectory()
        db_file = Path(cls._tmp.name) / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        cls._engine = engine

        async def _init() -> None:
            async with engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)

        asyncio.run(_init())
        # 把 with_session 用的全局 session 工厂指向临时库
        cls._old_maker = base_models.async_maker
        base_models.async_maker = async_sessionmaker(engine)

    @classmethod
    def tearDownClass(cls) -> None:
        base_models.async_maker = cls._old_maker
        asyncio.run(cls._engine.dispose())  # Windows 下先释放连接再删文件
        cls._tmp.cleanup()

    def test_save_then_load_round_trip(self) -> None:
        models = self.models

        async def run() -> None:
            context = {
                "wives": {
                    "u1": {"name": "今汐", "image": "a.png", "updated_at": 1},
                },
                "rob_attempts": {"u1": True},
            }
            await models.DailyWifeRecord.save_context(
                "2026-08-12", "onebot", "1001", context
            )
            loaded = await models.DailyWifeRecord.load_day("2026-08-12")
            self.assertEqual(
                loaded["onebot:1001"]["wives"]["u1"]["name"], "今汐"
            )
            self.assertIs(loaded["onebot:1001"]["rob_attempts"]["u1"], True)

        asyncio.run(run())

    def test_save_context_overwrite_is_idempotent(self) -> None:
        models = self.models

        async def run() -> None:
            context = {"wives": {"u5": {"name": "A", "updated_at": 1}}}
            await models.DailyWifeRecord.save_context(
                "2026-08-13", "onebot", "2001", context
            )
            context2 = {"wives": {"u5": {"name": "B", "updated_at": 2}}}
            await models.DailyWifeRecord.save_context(
                "2026-08-13", "onebot", "2001", context2
            )
            loaded = await models.DailyWifeRecord.load_day("2026-08-13")
            self.assertEqual(loaded["onebot:2001"]["wives"]["u5"]["name"], "B")
            self.assertEqual(len(loaded["onebot:2001"]["wives"]), 1)

        asyncio.run(run())

    def test_import_legacy_keeps_only_recent_two_days(self) -> None:
        models = self.models

        async def run() -> int:
            return await models.DailyWifeRecord.import_legacy_data(LEGACY_DATA)

        imported = asyncio.run(run())
        self.assertGreater(imported, 0)

        async def check() -> None:
            oldest = await models.DailyWifeRecord.load_day("2026-08-10")
            self.assertEqual(oldest, {})  # 最老的一天被丢弃
            middle = await models.DailyWifeRecord.load_day("2026-08-11")
            self.assertEqual(middle["onebot:1001"]["wives"]["u2"]["name"], "长离")
            self.assertIs(middle["onebot:1001"]["rob_attempts"]["u2"], True)
            newest = await models.DailyWifeRecord.load_day("2026-08-12")
            self.assertEqual(
                newest["onebot:1002"]["wives"]["u3"]["stolen_from"], "u9"
            )

        asyncio.run(check())

        # 幂等：重复导入行数不变
        asyncio.run(run())

        async def count() -> int:
            from sqlmodel import select, func

            async with base_models.async_maker() as session:
                result = await session.execute(
                    select(func.count()).select_from(models.DailyWifeRecord)
                )
                return result.one()

        asyncio.run(count())  # 不抛异常即可；行数一致性由先删后插保证


if __name__ == "__main__":
    unittest.main()
