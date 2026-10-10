"""TodayWaifu/models.py 数据库层的落盘契约测试。

该模块取代旧的 daily_wife_data.json 单文件读写：整份 JSON 的读-改-写随群数与天数增长
产生写放大，且并发写入会相互覆盖，零点高峰因此卡死（46fd929）。此处锁定两件事：写入后
读回的值必须等价、事务失败必须整体回滚。

需要 gsuid_core 环境（sqlmodel/aiosqlite），用核心 venv 运行：
    D:/122/bot/xiaoyu/botkj/gsuid_core/.venv/Scripts/python.exe tests/test_models_db.py
无依赖环境下自动跳过。
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

try:
    from sqlmodel import SQLModel
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from gsuid_core.utils.database import base_models

    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TodayWaifu" / "models.py"


def _load_models():
    import importlib.util

    # models.py 被设计为不依赖 TodayWaifu 内其它模块（禁运行时相对导入），因此可按文件路径
    # 独立加载；以模块名登记进 sys.modules 是 dataclass 装饰期查询 cls.__module__ 的前提，
    # 不登记时该查询返回 None 并直接抛 AttributeError。
    spec = importlib.util.spec_from_file_location("todaywaifu_models", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load models module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(_DEPS_OK, "缺少 gsuid_core/sqlmodel 环境，跳过数据库测试")
class DailyWifeRecordDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.models = _load_models()
        # 每个测试类使用独立临时库：用例验证的是事务与 upsert 语义，共用库会让前序用例
        # 残留的行参与后续断言。
        cls._tmp = tempfile.TemporaryDirectory()
        db_file = Path(cls._tmp.name) / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        cls._engine = engine

        async def _init() -> None:
            async with engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)

        asyncio.run(_init())
        # 把 with_session 用的全局 session 工厂指向临时库：被测方法由装饰器自行取会话，
        # 不接管该全局引用就只能测到真实数据库路径。
        cls._old_maker = base_models.async_maker
        base_models.async_maker = async_sessionmaker(engine)

    @classmethod
    def tearDownClass(cls) -> None:
        # 必须还原全局工厂：漏还原会把临时库泄漏给同进程内的后续测试类。
        base_models.async_maker = cls._old_maker
        asyncio.run(cls._engine.dispose())  # Windows 下先释放连接再删文件
        cls._tmp.cleanup()

    def test_save_then_load_round_trip(self) -> None:
        # 锁定写入-读回的等价性，尤其要覆盖非 dict 的布尔标记：rob_attempts 以 record_type
        # 标记行存储，序列化路径若把 True 退化为字典或字符串，抢老婆的去重语义随即失效。
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
        # 同一上下文重复保存只允许留下最新值且不产生重复行：upsert 冲突列漏配或误用插入，
        # 都会在同一主键上堆积旧值，使「今日老婆」在重试后读出过期记录。
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

    def test_apply_rows_rolls_back_delete_when_upsert_fails(self) -> None:
        # 删除与 upsert 必须同处一个事务：a982e77 之前两者分次提交，upsert 失败时删除已生效，
        # 用户的参与记录被清空而新记录未写入，等同数据丢失。此处注入 upsert 异常，
        # 断言既有行仍在且新行未落库。
        models = self.models

        async def run() -> None:
            seed_key = ("2026-08-14", "onebot", "3001", "wives", "u1")
            seed_value = {"name": "保留", "image": "keep.png", "updated_at": 1}
            await models.DailyWifeRecord.upsert_rows(
                [(*seed_key, seed_value)]
            )

            original_upsert = models.DailyWifeRecord.__dict__["_upsert_rows"]

            async def fail_upsert(cls, session, rows):
                raise RuntimeError("injected upsert failure")

            models.DailyWifeRecord._upsert_rows = classmethod(fail_upsert)
            try:
                with self.assertRaises(RuntimeError):
                    await models.DailyWifeRecord.apply_rows(
                        [
                            (
                                "2026-08-14",
                                "onebot",
                                "3001",
                                "wives",
                                "u2",
                                {"name": "新记录", "updated_at": 2},
                            )
                        ],
                        [seed_key],
                    )
            finally:
                # 无论断言是否通过都还原被替换的类方法，避免污染同类中的其它用例。
                models.DailyWifeRecord._upsert_rows = original_upsert

            self.assertEqual(
                await models.DailyWifeRecord.get_record(*seed_key),
                seed_value,
            )
            self.assertIsNone(
                await models.DailyWifeRecord.get_record(
                    "2026-08-14", "onebot", "3001", "wives", "u2"
                )
            )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
