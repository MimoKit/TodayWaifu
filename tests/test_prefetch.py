"""零点前预热的调度、状态与边界：窗口必须严格有界，且重启不得重跑整库。

零点高峰的根因是「日期翻转 + 全员同时抽签」：新一天每人抽到的角色与图片与前一天无关，
磁盘缓存整体失效，所有人的请求同时转为网络下载（0728809）。预热本身是一段额外负载，
因此它必须被限制在墙钟上限、单并发、可取消的范围内，否则会把零点的网络压力提前扩散到
整个晚间。

另一半是「重启友好」：预热结果必须落盘，否则每次重启都会重跑一遍整库下载（实测是
「重启就卡」的直接来源）。本文件同时锁定这两组不变量。
"""

import ast
import json
import unittest
from typing import Any
from pathlib import Path
from datetime import tzinfo, datetime, timezone, timedelta
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
PREFETCH = PLUGIN / 'prefetch.py'

# 纯函数族：调度与状态判定都不依赖 gsuid_core，可整段抽出来直接跑。
# `_now_shanghai` 定义在 constants，这里按固定时刻注入，使不传 now 的调用也可复现。
_TIMELINE_FUNCTIONS = (
    'in_prefetch_window',
    'seconds_until_prefetch',
    'seconds_until_midnight',
    'next_sleep_seconds',
)
_STATE_FUNCTIONS = ('_state_read', '_state_write', '_state_is_done')


def _extract(names: tuple[str, ...], injected: dict[str, Any]) -> dict[str, Any]:
    """把指定的顶层函数抽出来单独执行（prefetch.py 其余部分依赖 gsuid_core）。

    注入的 PREFETCH_HOUR / PREFETCH_MINUTE 来自 constants，此处写死字面量是刻意的：
    调度断言需要固定基准时刻，若从被测模块读取这些常量，「改了常量也照样通过」。
    """
    tree = ast.parse(PREFETCH.read_text(encoding='utf-8'))
    selected = [item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name in names]
    assert len(selected) == len(names), [item.name for item in selected]
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, *selected], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {
        'datetime': datetime,
        'timedelta': timedelta,
        'timezone': timezone,
        'tzinfo': tzinfo,
        'ZoneInfo': ZoneInfo,
        'Path': Path,
        'json': json,
        'PREFETCH_HOUR': 23,
        'PREFETCH_MINUTE': 20,
        '_SHANGHAI_TZ': None,
    }
    globals_dict.update(injected)
    exec(compile(module, str(PREFETCH), 'exec'), globals_dict)
    return globals_dict


_TIMELINE = _extract(
    _TIMELINE_FUNCTIONS,
    {'_now_shanghai': lambda: datetime(2026, 9, 24, 12, 0, 0)},
)
_STATE = _extract(_STATE_FUNCTIONS, {})

seconds_until_prefetch = _TIMELINE['seconds_until_prefetch']
seconds_until_midnight = _TIMELINE['seconds_until_midnight']
in_prefetch_window = _TIMELINE['in_prefetch_window']
next_sleep_seconds = _TIMELINE['next_sleep_seconds']


class PrefetchScheduleTests(unittest.TestCase):
    # 用例名中的 2350 是预热时刻的历史值，ec94fc9 为给限速下载留出余量已提前到 23:20；
    # 断言以实际常量为准，重命名会牵动兼容性清单，故保留原名。
    def test_waits_until_2350_on_the_same_day(self) -> None:
        delay = seconds_until_prefetch(datetime(2026, 9, 24, 20, 0, 0))
        self.assertAlmostEqual(delay, 3 * 3600 + 20 * 60, delta=1.0)

    def test_runs_immediately_inside_the_prefetch_window(self) -> None:
        # 进程刚好在预热窗口内启动：必须立刻补跑，而不是等 24 小时
        for minute in (20, 35, 55, 59):
            with self.subTest(minute=minute):
                delay = seconds_until_prefetch(datetime(2026, 9, 24, 23, minute, 0))
                self.assertLessEqual(delay, 1.0)

    def test_schedules_next_day_after_midnight(self) -> None:
        # 零点之后不属于窗口，须排到次日：若沿用「已过当天时刻即立刻返回」的口径，
        # 零点后重启的进程会在整个白天反复触发预热。
        delay = seconds_until_prefetch(datetime(2026, 9, 25, 0, 5, 0))
        self.assertAlmostEqual(delay, 23 * 3600 + 15 * 60, delta=1.0)

    def test_just_before_the_window_still_targets_today(self) -> None:
        # 窗口前一分钟仍应等待当天的目标时刻，延迟为正的小值：此处若判定为已过时刻，
        # 预热会提前到 23:19 触发，挤占当晚剩余的准备时间。
        delay = seconds_until_prefetch(datetime(2026, 9, 24, 23, 19, 30))
        self.assertAlmostEqual(delay, 30.0, delta=1.0)

    def test_delay_is_never_negative(self) -> None:
        # 逐小时遍历一整天：负数延迟会让 asyncio.sleep 立刻返回，预热退化为忙循环。
        for hour in range(24):
            delay = seconds_until_prefetch(datetime(2026, 9, 24, hour, 0, 0))
            self.assertGreater(delay, 0.0, f'hour={hour}')

    def test_window_covers_only_the_minutes_before_midnight(self) -> None:
        # 窗口外不得判定为「可立刻预热」，否则启动补跑会退化成全天候下载
        self.assertFalse(in_prefetch_window(datetime(2026, 9, 24, 22, 0, 0)))
        self.assertFalse(in_prefetch_window(datetime(2026, 9, 24, 23, 19, 59)))
        self.assertTrue(in_prefetch_window(datetime(2026, 9, 24, 23, 20, 0)))
        self.assertTrue(in_prefetch_window(datetime(2026, 9, 24, 23, 59, 59)))
        self.assertFalse(in_prefetch_window(datetime(2026, 9, 25, 0, 0, 0)))


class PrefetchBudgetTests(unittest.TestCase):
    def test_midnight_countdown_is_exact(self) -> None:
        self.assertAlmostEqual(seconds_until_midnight(datetime(2026, 9, 24, 23, 20, 0)), 40 * 60, delta=0.1)
        self.assertAlmostEqual(seconds_until_midnight(datetime(2026, 9, 24, 23, 59, 59)), 1.0, delta=0.1)
        self.assertAlmostEqual(seconds_until_midnight(datetime(2026, 9, 24, 0, 0, 0)), 24 * 3600, delta=0.1)

    def test_budget_never_runs_past_midnight(self) -> None:
        # 预算取「墙钟上限」与「距零点秒数」的较小值：23:50 起跑只能跑 10 分钟，
        # 否则预热会跨过零点，与早高峰抢带宽且对新一天毫无意义。
        source = PREFETCH.read_text(encoding='utf-8')
        body = source[source.index('async def _prefetch_once(') : source.index('def _prefetch_modes(')]
        self.assertIn('seconds_until_midnight()', body)
        self.assertIn('min(float(PREFETCH_MAX_SECONDS), seconds_until_midnight())', body)


class PrefetchLoopSimulationTests(unittest.TestCase):
    """按调度算法的真实推进方式模拟一整天，验证「一天只预热一次」。"""

    def _simulate(self, start: datetime, rounds: int = 4) -> list[datetime]:
        now = start
        fires: list[datetime] = []
        # 启动补跑：仅在窗口内触发
        if seconds_until_prefetch(now) <= 1.0:
            fires.append(now)
        for _ in range(rounds):
            now = now + timedelta(seconds=next_sleep_seconds(now))
            fires.append(now)
        return fires

    def test_daytime_restart_waits_for_the_window(self) -> None:
        # 中午重启：不预热，等 23:20
        fires = self._simulate(datetime(2026, 9, 24, 12, 0, 0), rounds=1)
        self.assertEqual(fires, [datetime(2026, 9, 24, 23, 20, 0)])

    def test_window_restart_catches_up_then_waits_for_tomorrow(self) -> None:
        # 23:30 重启：立刻补跑一次，之后每天 23:20 一次，当晚不会出现第二轮
        fires = self._simulate(datetime(2026, 9, 24, 23, 30, 0), rounds=3)
        self.assertEqual(
            fires,
            [
                datetime(2026, 9, 24, 23, 30, 0),
                datetime(2026, 9, 25, 23, 20, 0),
                datetime(2026, 9, 26, 23, 20, 0),
                datetime(2026, 9, 27, 23, 20, 0),
            ],
        )

    def test_after_midnight_restart_skips_that_day(self) -> None:
        # 00:30 重启：当日窗口已过，直接排到今晚 23:20，白天不预热
        fires = self._simulate(datetime(2026, 9, 25, 0, 30, 0), rounds=2)
        self.assertEqual(
            fires,
            [datetime(2026, 9, 25, 23, 20, 0), datetime(2026, 9, 26, 23, 20, 0)],
        )

    def test_loop_never_sleeps_zero_or_negative(self) -> None:
        # 逐分钟遍历两天：任何时刻算出的睡眠时长都必须为正，否则循环退化成忙循环
        moment = datetime(2026, 9, 24, 0, 0, 0)
        for _ in range(2 * 24 * 60):
            self.assertGreater(next_sleep_seconds(moment), 0.0, moment.isoformat())
            moment += timedelta(minutes=1)


class PrefetchBoundednessTests(unittest.TestCase):
    def _constants(self) -> dict[str, Any]:
        # 以受限求值读取常量而非导入 constants 模块：后者会连带拉起 GsCore 依赖；
        # 求值环境清空 __builtins__ 并只接受字面量与算式，避免执行任意代码。
        tree = ast.parse((PLUGIN / 'constants.py').read_text(encoding='utf-8'))
        values: dict[str, Any] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
                continue
            try:
                # 常量文件里允许 10 * 60 这类算式，直接求值即可
                code = compile(ast.Expression(node.value), '<constants>', 'eval')
                values[node.targets[0].id] = eval(code, {'__builtins__': {}}, {})
            except (NameError, TypeError, ValueError, SyntaxError):
                continue
        return values

    def test_prefetch_has_a_wall_clock_budget(self) -> None:
        # 预热必须有墙钟上限：无上限的下载会在图库限速时一直占用线程池与网络，
        # 反而加剧零点前后的拥塞；上限同时为「窗口时长是否够用」提供判据。
        values = self._constants()
        self.assertIn('PREFETCH_MAX_SECONDS', values)
        self.assertLessEqual(values['PREFETCH_MAX_SECONDS'], 30 * 60, '预热必须有时间上限，不能无限跑')
        self.assertGreater(values['PREFETCH_MAX_SECONDS'], 60)

    def test_prefetch_runs_before_midnight(self) -> None:
        # 预热时刻须早于零点并留出足够余量，使限速下载能在日期翻转前完成：
        # 若贴着零点执行，翻转过半的请求仍会落到未预热的 URL 上，预热失去意义。
        values = self._constants()
        self.assertEqual(values['PREFETCH_HOUR'], 23)
        self.assertEqual(values['PREFETCH_MINUTE'], 20, '预热应提前到 23:20，给限速下载留时间')
        self.assertLess(values['PREFETCH_MINUTE'], 60)

    def test_prefetch_budget_and_interval_match_gallery_limit(self) -> None:
        # 墙钟上限与下载间隔是一组必须同步调整的常量：间隔大于「上限 ÷ 候选张数」时，
        # 预热会在预算耗尽前只走完一部分图片，且失败得无声无息。
        values = self._constants()
        self.assertEqual(values['PREFETCH_MAX_SECONDS'], 30 * 60)
        self.assertEqual(values['PREFETCH_DOWNLOAD_INTERVAL_SECONDS'], 6.0)

    def test_prefetch_spaces_real_downloads_but_not_cache_hits(self) -> None:
        # 限速只作用于真实下载，命中磁盘缓存时不得等待：否则一次正常重启的补跑
        # 会按「张数 × 间隔」空转数分钟，这段时间恰好与 Core 启动争抢资源。
        source = PREFETCH.read_text(encoding='utf-8')
        body = source[source.index('async def _prefetch_once(') : source.index('def _prefetch_modes(')]
        self.assertIn('last_download_at', body)
        self.assertIn('PREFETCH_DOWNLOAD_INTERVAL_SECONDS', body)
        self.assertIn('has_url_cache', body)

    def test_prefetch_is_opt_outable_and_bounded_per_role(self) -> None:
        # 两个配置键缺一不可：开关用于在图库限速的部署上停用预热，每角色张数用于控制
        # 下载总量；默认开启是因为该功能的价值正来自「默认就生效」。
        config = (ROOT / 'config_default.py').read_text(encoding='utf-8')
        self.assertIn("'DailyWifePrefetchEnabled'", config)
        self.assertIn("'DailyWifePrefetchImagesPerRole'", config)
        # 默认开启（否则等于没修），但每角色张数要小
        self.assertIn('GsBoolConfig(', config)

    def test_prefetch_only_runs_in_gallery_mode_and_respects_the_switch(self) -> None:
        # 开关关闭即整体跳过，图片源非图库的模式不计入候选：本地图片源没有网络下载，
        # 对其预热只会空转磁盘 IO 并把耗时计入墙钟预算。
        source = PREFETCH.read_text(encoding='utf-8')
        body = source[source.index('async def _prefetch_once(') : source.index('def _prefetch_modes(')]
        self.assertIn("_cfg_bool('DailyWifePrefetchEnabled', True)", body)
        # 图片来源按功能拆分后，预热只针对跟随图库的那些功能
        self.assertIn("if _image_source(mode) == 'gallery'", body)
        self.assertIn('PREFETCH_MAX_SECONDS', body)

    def test_prefetch_skips_images_already_on_disk(self) -> None:
        # 已知在磁盘上的图片计入 cached 而非重新下载，且探测只做存在性检查：
        # 该跳过是「重启补跑代价极低」这一设计成立的前提，缺失会让每次重启都重新下载全量。
        source = PREFETCH.read_text(encoding='utf-8')
        body = source[source.index('async def _prefetch_once(') : source.index('def _prefetch_modes(')]
        self.assertIn('has_url_cache', body)
        self.assertIn("stats['cached'] += 1", body)
        # 命中后不得再读整张图重压一遍，否则重启补跑的读盘量按张数累积
        self.assertNotIn('_shrink_image_sync, cached', body)

    def test_prefetch_never_raises_out_of_the_loop(self) -> None:
        # 异常分流是明确取舍：CancelledError 必须上抛以响应停机，其余具体异常在此吞掉，
        # 否则一次网络抖动会终止预热循环，当晚不再重试。禁止裸 except Exception
        # 是为了不把编程错误一并吞掉，使其只留日志、无从定位。
        source = PREFETCH.read_text(encoding='utf-8')
        runner = source[source.index('async def _run_prefetch_once(') : source.index('async def _prefetch_loop(')]
        # 不允许 except Exception（skill 红线），但必须有具体异常兜底
        self.assertNotIn('except Exception', runner)
        self.assertIn('except asyncio.CancelledError', runner)
        self.assertIn('except (OSError, RuntimeError, TimeoutError, ValueError)', runner)

    def test_startup_delay_is_small_but_not_zero(self) -> None:
        # 延迟下界避免与 Core 启动争抢数据库与线程池，上界保证窗口内重启的实例
        # 不会错过当晚预热。
        values = self._constants()
        delay = values['PREFETCH_STARTUP_DELAY_SECONDS']
        self.assertGreater(delay, 0, '不能为 0，否则和 Core 启动抢资源')
        self.assertLessEqual(delay, 600, '也不能太久，否则重启后长时间没有预热')


class PrefetchRestartTests(unittest.TestCase):
    """重启友好性：预热结果落盘，且启动补跑只在窗口内发生。"""

    def test_completed_day_is_persisted_and_survives_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'prefetch_state.json'
            payload = {'version': 1, 'day': '2026-09-24', 'status': 'done', 'roles': 12}
            self.assertTrue(_STATE['_state_write'](path, payload))

            # 模拟重启：重新读盘（内存里没有任何残留）
            state = _STATE['_state_read'](path)

            self.assertEqual(state['day'], '2026-09-24')
            self.assertTrue(_STATE['_state_is_done'](state, '2026-09-24'))

    def test_partial_run_does_not_count_as_done(self) -> None:
        # 撞到时间上限（partial）时下一次窗口必须能继续，不能被当成「今天已完成」
        state = {'day': '2026-09-24', 'status': 'partial'}
        self.assertFalse(_STATE['_state_is_done'](state, '2026-09-24'))

    def test_previous_day_state_does_not_skip_today(self) -> None:
        state = {'day': '2026-09-23', 'status': 'done'}
        self.assertFalse(_STATE['_state_is_done'](state, '2026-09-24'))

    def test_missing_or_corrupt_state_reads_as_empty(self) -> None:
        # 状态文件只是优化提示：缺失、半截或非对象都必须退化为「没预热过」，
        # 不能因为读不懂就抛错，否则整条定时链路会被一个坏文件打断。
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'prefetch_state.json'
            self.assertEqual(_STATE['_state_read'](path), {})
            path.write_text('{not json', encoding='utf-8')
            self.assertEqual(_STATE['_state_read'](path), {})
            path.write_text('[1, 2, 3]', encoding='utf-8')
            self.assertEqual(_STATE['_state_read'](path), {})
            path.write_text('{"day": 20260924, "status": null}', encoding='utf-8')
            self.assertEqual(_STATE['_state_read'](path), {})

    def test_state_write_is_atomic_and_leaves_no_temp_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'prefetch_state.json'
            self.assertTrue(_STATE['_state_write'](path, {'day': '2026-09-24', 'status': 'done'}))
            self.assertTrue(path.is_file())
            self.assertEqual([item.name for item in path.parent.iterdir()], ['prefetch_state.json'])

    def test_runner_skips_when_today_is_already_done(self) -> None:
        # 「重启不重新预热」的落点：执行前先查落盘状态，已完成就直接返回
        source = PREFETCH.read_text(encoding='utf-8')
        runner = source[source.index('async def _run_prefetch_once(') : source.index('async def _prefetch_loop(')]
        self.assertIn('if _prefetch_done_today():', runner)
        self.assertIn('_state_write(_state_path(), payload)', runner)

    def test_startup_catch_up_only_inside_the_window(self) -> None:
        # 窗口外重启不得触发任何预热：此前无条件补跑是「重启就重新预热」的直接原因
        source = PREFETCH.read_text(encoding='utf-8')
        loop = source[source.index('async def _prefetch_loop(') :]
        self.assertIn('await asyncio.sleep(PREFETCH_STARTUP_DELAY_SECONDS)', loop)
        self.assertIn('if seconds_until_prefetch() <= 1.0:', loop)
        self.assertIn('await _run_prefetch_once()', loop)
        # 补跑必须挂在窗口判断之下，不能无条件执行
        startup = loop[: loop.index('while True:')]
        self.assertEqual(startup.count('await _run_prefetch_once()'), 1)

    def test_prefetch_window_waits_for_next_day_after_startup_run(self) -> None:
        # 启动补跑已消费掉当前窗口的预热机会，此时必须显式排到次日窗口：
        # 沿用窗口内的小延迟会让循环立即再跑一次，形成连续预热。
        source = PREFETCH.read_text(encoding='utf-8')
        loop = source[source.index('async def _prefetch_loop(') :]
        self.assertIn('delay = next_sleep_seconds()', loop)
        scheduler = source[source.index('def next_sleep_seconds(') : source.index('def _state_path(')]
        self.assertIn('if delay > 1.0:', scheduler)
        # 延迟必须相对当前时刻计算，否则 asyncio.sleep 醒来会落在窗口之前
        self.assertIn('return (tomorrow_start - current).total_seconds()', scheduler)


class PrefetchLifecycleTests(unittest.TestCase):
    def test_prefetch_loop_is_registered_and_cancelled(self) -> None:
        # 任务句柄必须被保存并在停机时取消：插件重载后遗留的循环会继续按旧句柄发下载，
        # 且因无人持有其引用而无从停止（dd71d75 修的是同类 worker 丢失问题）。
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('async def _start_gallery_prefetch_on_startup()', shared)
        self.assertIn('async def _stop_gallery_prefetch_on_shutdown()', shared)
        self.assertIn('asyncio.create_task(_prefetch_loop(), name=PREFETCH_TASK_NAME)', shared)
        self.assertIn('task.cancel()', shared)

    def test_duplicate_prefetch_loop_is_detected_by_task_name(self) -> None:
        # 热重载会重新导入模块并丢掉模块级句柄，只能靠任务名认出上一份循环，
        # 否则两份循环同时按 6 秒间隔下载，预热负载直接翻倍。
        prefetch = PREFETCH.read_text(encoding='utf-8')
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('def prefetch_task_running()', prefetch)
        self.assertIn('task.get_name() == PREFETCH_TASK_NAME', prefetch)
        self.assertIn('if prefetch_task_running():', shared)

    def test_cached_url_index_is_restored_on_startup(self) -> None:
        # 索引只驻留内存，重启后必须从磁盘重建；否则 prefer_cached_urls 退化为全量随机，
        # 前一晚预热的图片等于白热 —— 这正是此前「重启必须再预热一轮」的真正原因。
        prefetch = PREFETCH.read_text(encoding='utf-8')
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('async def restore_cached_url_index()', prefetch)
        self.assertIn('rebuild_cached_url_index', prefetch)
        self.assertIn('await restore_cached_url_index()', shared)


if __name__ == '__main__':
    unittest.main()
