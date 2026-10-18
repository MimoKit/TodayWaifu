"""TodayWaifu 的进程内共享可变状态（缓存、注册表、信号量、待确认表）。

这些对象需由多个模块就地读写；若改由某个功能模块持有、其余模块反向导入，会形成
导入环并让依赖方向不可控。集中到本模块后依赖方向单向收敛，测试也能在用例之间整体
替换或清空这些对象，而不必逐个模块打补丁。

本模块只做定义，不承载业务逻辑；状态的具体失效策略由 invalidation 模块负责。"""
from __future__ import annotations

import asyncio

from .domain import RoleCandidate, MemberCandidate
from .payloads import DailyContext, GalleryPayload, PendingCustomRoleDelete
from .constants import CACHE_TTL_SECONDS
from .source_cache import AsyncSourceCache
from .daily_repository import ContextRegistry

# 候选缓存：值为 (写入时刻, 候选元组)。附带时间戳而非仅存候选，使读取方能自行
# 判断新鲜度；该缓存无容量上限，依赖失效钩子在数据变更时整体清空。
CANDIDATE_CACHE: dict[str, tuple[float, tuple['RoleCandidate', ...]]] = {}


# 在途候选加载：同一 key 的并发请求共享同一个 Task，避免同时重复扫描同一图库目录。
_CANDIDATE_INFLIGHT: dict[str, asyncio.Task[tuple[tuple['RoleCandidate', ...] | None, str | None]]] = {}


# 图库来源缓存：容量上限 16 并按 LRU 淘汰，防止来源种类增长时无界占用内存。
_SOURCE_CACHE = AsyncSourceCache[GalleryPayload](CACHE_TTL_SECONDS, max_entries=16)


# 战双候选缓存独立于通用来源缓存：其条目体积更大、复用率更低，故容量收紧到 4。
_PGR_CANDIDATE_CACHE = AsyncSourceCache[tuple[RoleCandidate, ...]](CACHE_TTL_SECONDS, max_entries=4)


# 候选缓存代次：每次失效递增，令失效之前发起的在途加载在写回时被识别并丢弃。
_CANDIDATE_CACHE_GENERATION = 0


# 候选加载并发上限：图库扫描为磁盘密集操作，不设上限会占满专用 IO 线程池而拖慢其它请求。
_CANDIDATE_LOAD_SEMAPHORE = asyncio.Semaphore(4)


# 图片下载在途合并表：键为图片标识，避免同一张图被并发重复下载。
_IMAGE_INFLIGHT: dict[str, asyncio.Task[bytes]] = {}


# 图片下载并发上限：与 executor 的线程池容量配套，超出部分排队而非挤占线程。
_IMAGE_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(8)


# 自定义角色删除的待确认表：确认动作与发起动作可能间隔数十秒，期间状态只驻留内存；
# 进程重启后待确认项丢失，用户需重新发起删除，这是可接受的代价。
CUSTOM_ROLE_DELETE_PENDING: dict[str, PendingCustomRoleDelete] = {}


# 每日上下文注册表：集中持有锁与在途任务，使上下文读写与跨日翻转共用同一临界区。
_CONTEXT_REGISTRY = ContextRegistry()


# 兼容视图：注册表已迁至 daily_repository，但旧调用方仍按模块级名字读取锁与在途任务。
# 这里保留同一对象的引用而非副本，使两处读到的始终是同一份数据，迁移期间不会出现双份状态。
_DAILY_CONTEXT_LOCKS: dict[str, asyncio.Lock] = _CONTEXT_REGISTRY.locks  # compatibility view


# 每日上下文缓存独立于注册表：值为 (日期键, 上下文)，日期不匹配即视为跨日失效。
_DAILY_CONTEXT_CACHE: dict[str, tuple[str, DailyContext]] = {}


# 与 _DAILY_CONTEXT_LOCKS 同理，共享注册表的在途任务表，避免并发加载同一上下文。
_DAILY_CONTEXT_INFLIGHT: dict[str, asyncio.Task[DailyContext]] = _CONTEXT_REGISTRY.inflight  # compatibility view


# 群成员与群显示名缓存 TTL 固定为 60 秒：成员变动无需即时可见，短 TTL 兼顾新鲜度与查询压力。
_MEMBER_CACHE = AsyncSourceCache[tuple[MemberCandidate, ...]](60.0, max_entries=128)


_GROUP_DISPLAY_NAME_CACHE = AsyncSourceCache[dict[str, str]](60.0, max_entries=128)
