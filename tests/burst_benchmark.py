"""零点高峰突发压测：在真机 Core 上复现「全员同时抽老婆」。

**这个脚本不能在本地跑**，它需要真实的 gsuid_core 环境。用法：

    cd <gsuid_core 仓库根>
    .venv/bin/python plugins/TodayWaifu/tests/burst_benchmark.py <plugin_parent_dir> [并发数] [warm] [db]

`plugin_parent_dir` 是**包含** `TodayWaifu` 包的那一层目录（通常是 `plugins/`）。
可选参数：`warm` 先灌满磁盘缓存（模拟 23:50 预热之后的 00:00）；
`db` 把真实的一次 `upsert_record` 写库也算进命令路径。

它替代了原来的 `peak_benchmark.py` —— 那个只测内存里 `AsyncSourceCache` 的
并发合并（100 个协程打同一个 key，`loader_calls=1`），根本没有复现零点场景，
反而给出了「已经优化好了」的错觉。

测四件事（前两个才是「卡死」的直接原因）：

  1. `slot_occupancy_*`  命令协程占用 Core `CommandSemaphore` 额度的时长。
     框架在协程**结束**时才归还额度，所以这个值就是「一条命令会堵住别人多久」。
  2. `command_slot_wait_*` 一条**无关命令**要等多久才能拿到额度。
     额度被占满时框架 `_process` 会停止消费队列，该 bot 上所有插件一起卡。
  3. `competing_probe_*`  别的插件用 `asyncio.to_thread` 做阻塞 IO 要等多久。
     量化「插件借用了 Core 默认线程池」造成的跨插件饥饿。
  4. `all_images_delivered_seconds` 用户视角的完成时刻（吞吐），
     用来确认延迟优化没有以牺牲吞吐为代价。

注意：用 `db` 时必须让 `prepare_db()` 复刻框架真实的 SQLite 初始化
（WAL + `synchronous=NORMAL`）。漏掉会测成 `journal_mode=delete` +
`synchronous=FULL`，每次提交都 fsync，写入延迟被高估好几倍。

真机 4 核、200 并发、冷缓存、0.5s/张图库的实测：

    指标                   修复前        修复后       修复后+预热
    命令占用额度 max       2081.6 ms     0.1 ms       0.2 ms
    无关命令等待额度 max   11410.8 ms    0.0 ms       0.0 ms
    命令延迟 p99           13033.4 ms    0.0 ms       0.2 ms
    其他插件阻塞 IO 等待   493.0 ms      17.9 ms      13.1 ms
    全部 200 张图送达      13.10 s       12.87 s      0.43 s

带上真实写库（`db`）后，剩下的瓶颈是框架的 SQLite 单写者闸门：

    指标                   修复前        修复后
    命令占用额度 max       2123.9 ms     195.8 ms
    无关命令等待额度 max   11455.2 ms    825.8 ms
    全部 200 张图送达      13.10 s       12.93 s
"""
from __future__ import annotations

import sys
import json
import time
import asyncio
import tempfile
import threading
from types import SimpleNamespace
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor

PORT = 8899
IMAGE_DELAY = 0.5          # 每张图的模拟网络耗时
COMMAND_SEMAPHORE = 25     # 框架 CommandSemaphore 默认值
COMPETING_PROBE_INTERVAL = 0.05


class _Handler(BaseHTTPRequestHandler):
    payload = b'\x89PNG\r\n\x1a\n' + b'Z' * (256 * 1024)

    def do_GET(self) -> None:
        time.sleep(IMAGE_DELAY)
        body = type(self).payload
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def start_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(('127.0.0.1', PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def competing_probe(samples: list[float], stop: asyncio.Event) -> None:
    """模拟「别的插件」用默认 executor 做阻塞 IO，记录它要等多久。"""
    while not stop.is_set():
        started = time.perf_counter()
        await asyncio.to_thread(time.sleep, 0.01)
        samples.append(time.perf_counter() - started)
        await asyncio.sleep(COMPETING_PROBE_INTERVAL)


async def command_slot_probe(waits: list[float], stop: asyncio.Event, sem: asyncio.Semaphore) -> None:
    """模拟「一条无关命令」要等多久才能拿到 Core 的命令并发额度。"""
    while not stop.is_set():
        started = time.perf_counter()
        async with sem:
            waits.append(time.perf_counter() - started)
        await asyncio.sleep(COMPETING_PROBE_INTERVAL)


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * ratio))
    return ordered[index]


async def prepare_db() -> object:
    """把框架的数据库层指向一个临时 SQLite，建好 dailywiferecord 表。

    GsCore 默认 db_type 就是 SQLite，且所有写都要排一个**进程级单写者闸门**，
    所以命令路径里的那次 upsert 必须一起压。
    """
    from sqlmodel import SQLModel
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from TodayWaifu.TodayWaifu import models as M
    from gsuid_core.utils.database import base_models as BM

    db_path = Path(tempfile.mkdtemp()) / 'bench.db'
    # 必须复刻框架真实的 SQLite 初始化（base_models.py:165-226）：
    # WAL + synchronous=NORMAL。漏掉的话测到的是 journal_mode=delete +
    # synchronous=FULL（每次提交都 fsync），会把写入延迟高估好几倍。
    BM._enable_sqlite_wal(str(db_path))
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}')
    event.listens_for(engine.sync_engine, 'connect')(BM._set_sqlite_connect_pragmas)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    BM.engine = engine
    BM.async_maker = maker
    BM._db_type = 'sqlite'
    BM._db_initialized = True
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: SQLModel.metadata.create_all(c, tables=[M.DailyWifeRecord.__table__])
        )
    return M


async def run(n_draws: int, plugin_parent: str, warm: bool = False, with_db: bool = False) -> dict[str, float]:
    sys.path.insert(0, plugin_parent)
    from TodayWaifu.TodayWaifu import gallery as G, senders as S

    cache_root = Path(tempfile.mkdtemp())
    G._gallery_image_cache_root = lambda: cache_root
    G._gallery_api_url = lambda: f'http://127.0.0.1:{PORT}/api/roles'
    S._download_image = G._download_image

    # 生产环境由 on_core_start_before 钩子启动投递 worker；基准里手动启动
    has_workers = hasattr(S, 'start_image_delivery_workers')
    if has_workers:
        S.start_image_delivery_workers()

    if with_db:
        from gsuid_core.models import Event
        from TodayWaifu.TodayWaifu import daily_store as store

        await prepare_db()
        events = [
            Event(bot_id='bot1', user_id=f'u{i}', group_id='g1', real_bot_id='bot1')
            for i in range(n_draws)
        ]
        record_value = {'name': '今汐', 'role_ids': ['1304'], 'image': 'x', 'record_type': 'role'}

    sent: list[int] = []

    class FakeBot:
        def __init__(self) -> None:
            self.ev = SimpleNamespace(user_type='group')

        async def send(self, message: object, *args: object, **kwargs: object) -> None:
            sent.append(len(message) if isinstance(message, list) else len(str(message)))

    bot = FakeBot()
    urls = [f'http://127.0.0.1:{PORT}/img/unique_{i}.png' for i in range(n_draws)]
    roles = [
        SimpleNamespace(name=f'角色{i}', role_ids=(str(1000 + i),), images=(urls[i],))
        for i in range(n_draws)
    ]

    if warm:
        # 模拟 23:50 的预热：先把图片灌进磁盘缓存，再看 00:00 的突发
        async def warm_one(url: str) -> None:
            try:
                await G._download_image(url)
            except Exception:
                pass

        await asyncio.gather(*(warm_one(u) for u in urls))
        print(f'# 预热完成，缓存文件 {len(list(cache_root.iterdir()))} 个', file=sys.stderr)

    # 默认 executor 压到真实机器的线程数（min(32, cpu+4)），模拟生产 Core
    loop = asyncio.get_running_loop()
    workers = min(32, (len(__import__('os').sched_getaffinity(0)) or 1) + 4)
    loop.set_default_executor(ThreadPoolExecutor(max_workers=workers))

    sem = asyncio.Semaphore(COMMAND_SEMAPHORE)
    latencies: list[float] = []
    occupancies: list[float] = []
    probe_samples: list[float] = []
    slot_waits: list[float] = []
    stop = asyncio.Event()

    async def draw(index: int) -> None:
        started = time.perf_counter()
        async with sem:                       # 命令协程占用 Core 并发额度
            # 进入临界区之后才是「命令真正占用额度」的时长 —— 这才是拖死 Core 的量
            entered = time.perf_counter()
            if with_db:
                await store._save_daily_record(events[index], 'wives', f'u{index}', record_value)
            await S._send_role_image(
                bot, roles[index], urls[index], '文字', index, True, 'wife'
            )
            occupancies.append(time.perf_counter() - entered)
        latencies.append(time.perf_counter() - started)

    probe_a = asyncio.create_task(competing_probe(probe_samples, stop))
    probe_b = asyncio.create_task(command_slot_probe(slot_waits, stop, sem))

    started = time.perf_counter()
    await asyncio.gather(*(draw(i) for i in range(n_draws)))
    drain = time.perf_counter() - started

    stop.set()
    await asyncio.gather(probe_a, probe_b, return_exceptions=True)

    # 等**所有图片真正发出去**（旧代码是内联发送，新代码由后台 worker 发送），
    # 这是用户视角的完成时刻，也是唯一公平的跨版本比较口径
    while len(sent) < n_draws and time.perf_counter() - started < 180:
        await asyncio.sleep(0.05)
    deliver_seconds = time.perf_counter() - started

    return {
        'draws': n_draws,
        'warm_cache': warm,
        'with_db_write': with_db,
        'default_executor_threads': workers,
        'drain_seconds': round(drain, 2),
        'all_images_delivered_seconds': round(deliver_seconds, 2),
        'slot_occupancy_p50_ms': round(percentile(occupancies, 0.50) * 1000, 1),
        'slot_occupancy_p99_ms': round(percentile(occupancies, 0.99) * 1000, 1),
        'slot_occupancy_max_ms': round(max(occupancies) * 1000, 1),
        'draw_p50_ms': round(percentile(latencies, 0.50) * 1000, 1),
        'draw_p99_ms': round(percentile(latencies, 0.99) * 1000, 1),
        'draw_max_ms': round(max(latencies) * 1000, 1),
        'competing_probe_max_ms': round(max(probe_samples or [0]) * 1000, 1),
        'competing_probe_p99_ms': round(percentile(probe_samples, 0.99) * 1000, 1),
        'command_slot_wait_max_ms': round(max(slot_waits or [0]) * 1000, 1),
        'messages_sent': len(sent),
        'cache_files_after_drain': len(list(cache_root.iterdir())),
    }


def main() -> int:
    plugin_parent = sys.argv[1]
    n_draws = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    flags = set(sys.argv[3:])
    server = start_server()
    try:
        result = asyncio.run(run(n_draws, plugin_parent, 'warm' in flags, 'db' in flags))
    finally:
        server.shutdown()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
