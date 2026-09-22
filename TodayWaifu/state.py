"""TodayWaifu 的进程内共享可变状态（缓存、注册表、信号量、待确认表）。

所有需跨模块共享且会被就地修改的状态集中在此，避免模块间互相导入成环。"""
from __future__ import annotations

import asyncio

from .domain import RoleCandidate, MemberCandidate
from .payloads import DailyContext, GalleryPayload, PendingCustomRoleDelete
from .constants import CACHE_TTL_SECONDS
from .source_cache import AsyncSourceCache
from .daily_repository import ContextRegistry

CANDIDATE_CACHE: dict[str, tuple[float, tuple['RoleCandidate', ...]]] = {}


_CANDIDATE_INFLIGHT: dict[str, asyncio.Task[tuple[tuple['RoleCandidate', ...] | None, str | None]]] = {}


_SOURCE_CACHE = AsyncSourceCache[GalleryPayload](CACHE_TTL_SECONDS, max_entries=16)


_PGR_CANDIDATE_CACHE = AsyncSourceCache[tuple[RoleCandidate, ...]](CACHE_TTL_SECONDS, max_entries=4)


_CANDIDATE_CACHE_GENERATION = 0


_CANDIDATE_LOAD_SEMAPHORE = asyncio.Semaphore(4)


_IMAGE_INFLIGHT: dict[str, asyncio.Task[bytes]] = {}


_IMAGE_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(8)


CUSTOM_ROLE_DELETE_PENDING: dict[str, PendingCustomRoleDelete] = {}


_daily_data_lock = asyncio.Lock()


_CONTEXT_REGISTRY = ContextRegistry()


_DAILY_CONTEXT_LOCKS: dict[str, asyncio.Lock] = _CONTEXT_REGISTRY.locks  # compatibility view


_DAILY_CONTEXT_CACHE: dict[str, tuple[str, DailyContext]] = {}


_DAILY_CONTEXT_INFLIGHT: dict[str, asyncio.Task[DailyContext]] = _CONTEXT_REGISTRY.inflight  # compatibility view


_MEMBER_CACHE = AsyncSourceCache[tuple[MemberCandidate, ...]](60.0, max_entries=128)


_GROUP_DISPLAY_NAME_CACHE = AsyncSourceCache[dict[str, str]](60.0, max_entries=128)


_MEMBER_AVATAR_INFLIGHT: dict[str, asyncio.Task[str]] = {}
