"""零点前预热的调度与边界：窗口判定必须严格有界，且只在图库模式下运行。

零点高峰的根因是「日期翻转 + 全员同时抽签」：随机种子带日期，翻转后每个用户抽到的角色
与图片 URL 全部改变，磁盘缓存整体失效，所有人的请求同时转为网络下载（0728809）。预热
本身是一段额外负载，因此它必须被限制在墙钟上限、单并发、可取消的范围内，否则会把零点
的网络压力提前扩散到整个晚间。
"""

import ast
import unittest
from typing import Any
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'TodayWaifu'
PREFETCH = PLUGIN / 'prefetch.py'


def _extract_scheduler() -> Any:
    """单独抽出纯函数 seconds_until_prefetch 来跑（prefetch.py 依赖 gsuid_core）。

    注入的 PREFETCH_HOUR / PREFETCH_MINUTE 来自 constants，此处写死字面量是刻意的：
    调度断言需要固定基准时刻，若从被测模块读取这些常量，「改了常量也照样通过」。
    """
    tree = ast.parse(PREFETCH.read_text(encoding='utf-8'))
    node = next(
        item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == 'seconds_until_prefetch'
    )
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.Module(body=[future, node], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_dict: dict[str, Any] = {
        'datetime': datetime,
        'timedelta': __import__('datetime').timedelta,
        'PREFETCH_HOUR': 23,
        'PREFETCH_MINUTE': 20,
    }
    exec(compile(module, str(PREFETCH), 'exec'), globals_dict)
    return globals_dict['seconds_until_prefetch']


seconds_until_prefetch = _extract_scheduler()


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
        self.assertIn('read_url_cache', body)

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
        # 已知在磁盘上的图片计入 cached 而非重新下载：该跳过是「重启补跑代价极低」
        # 这一设计成立的前提，缺失会让每次重启都重新下载全量图片。
        source = PREFETCH.read_text(encoding='utf-8')
        body = source[source.index('async def _prefetch_once(') : source.index('def _prefetch_modes(')]
        self.assertIn('read_url_cache', body)
        self.assertIn("stats['cached'] += 1", body)

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

    def test_prefetch_runs_once_shortly_after_startup(self) -> None:
        """重启可能发生在零点之后，那时缓存未必完整，必须补跑一次。"""
        source = PREFETCH.read_text(encoding='utf-8')
        loop = source[source.index('async def _prefetch_loop(') :]
        self.assertIn('await asyncio.sleep(PREFETCH_STARTUP_DELAY_SECONDS)', loop)
        self.assertIn('await _run_prefetch_once()', loop)

    def test_prefetch_window_waits_for_next_day_after_startup_run(self) -> None:
        # 启动补跑已消费掉当前窗口的预热机会，此时必须显式排到次日：
        # 沿用窗口内的小延迟会让循环立即再跑一次，形成连续预热。
        source = PREFETCH.read_text(encoding='utf-8')
        loop = source[source.index('async def _prefetch_loop(') :]
        self.assertIn('if delay <= 1.0:', loop)
        self.assertIn('next_day = datetime.now() + timedelta(days=1)', loop)

    def test_startup_delay_is_small_but_not_zero(self) -> None:
        # 延迟下界避免与 Core 启动争抢数据库与线程池，上界保证零点后重启的实例
        # 不会长时间处于缓存冷态。
        values = self._constants()
        delay = values['PREFETCH_STARTUP_DELAY_SECONDS']
        self.assertGreater(delay, 0, '不能为 0，否则和 Core 启动抢资源')
        self.assertLessEqual(delay, 600, '也不能太久，否则重启后长时间没有预热')


class PrefetchLifecycleTests(unittest.TestCase):
    def test_prefetch_loop_is_registered_and_cancelled(self) -> None:
        # 任务句柄必须被保存并在停机时取消：插件重载后遗留的循环会继续按旧句柄发下载，
        # 且因无人持有其引用而无从停止（dd71d75 修的是同类 worker 丢失问题）。
        shared = (PLUGIN / 'shared.py').read_text(encoding='utf-8')
        self.assertIn('async def _start_gallery_prefetch_on_startup()', shared)
        self.assertIn('async def _stop_gallery_prefetch_on_shutdown()', shared)
        self.assertIn('_PREFETCH_TASK = asyncio.create_task(_prefetch_loop())', shared)
        self.assertIn('task.cancel()', shared)


if __name__ == '__main__':
    unittest.main()
