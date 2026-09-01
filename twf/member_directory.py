"""TodayWaifu 群成员目录与头像服务。"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from gsuid_core.utils.database.models import CoreUser

from .domain import MemberCandidate
from .source_cache import AsyncSourceCache

MEMBER_AVATAR_CACHE_SECONDS = 7 * 24 * 60 * 60
AVATAR_MAX_BYTES = 2 * 1024 * 1024


def valid_display_name(value: Any, user_id: str | int | None = None) -> str:
    text = str(value or '').strip()
    if text in {'', '1', 'None', 'none', 'NULL', 'null'}:
        return ''
    if user_id is not None and text == str(user_id):
        return ''
    return text


def valid_member_text(value: Any) -> str:
    text = str(value or '').strip()
    if text in {'', '1', 'None', 'none', 'NULL', 'null'}:
        return ''
    return text


def qq_avatar_url(user_id: str) -> str:
    return f'https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640'


def avatar_cache_path(root: Path, user_id: str) -> Path:
    safe_user_id = re.sub(r'[^0-9A-Za-z_-]+', '_', str(user_id)) or 'unknown'
    return root / 'group_member_avatar_cache' / f'{safe_user_id}.jpg'


def usable_cached_avatar(path: Path, check_ttl: bool = True) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    return not check_ttl or time.time() - path.stat().st_mtime <= MEMBER_AVATAR_CACHE_SECONDS


def download_avatar(url: str, path: Path) -> bool:
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(request, timeout=8) as response:
        data = response.read(AVATAR_MAX_BYTES + 1)
    if not data or len(data) > AVATAR_MAX_BYTES:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)
    return True


class MemberDirectory:
    def __init__(self, data_root: Path, ttl_seconds: float = 60.0) -> None:
        self.data_root = data_root
        self._members = AsyncSourceCache[tuple[MemberCandidate, ...]](ttl_seconds, max_entries=128)
        self._avatars = AsyncSourceCache[str](ttl_seconds, max_entries=512)

    async def load(self, ev: Any) -> tuple[MemberCandidate, ...]:
        if not ev.group_id:
            return ()
        key = f'{ev.bot_id}:{ev.group_id}'

        async def load_members() -> tuple[MemberCandidate, ...]:
            users = await CoreUser.get_group_all_user(str(ev.group_id))
            bot_ids = {
                str(item).strip()
                for item in (
                    ev.bot_id,
                    getattr(ev, 'real_bot_id', ''),
                    getattr(ev, 'bot_self_id', ''),
                    getattr(ev, 'self_id', ''),
                )
                if str(item or '').strip()
            }
            excluded = {str(ev.user_id), *bot_ids}
            preferred_bot_id = str(getattr(ev, 'real_bot_id', '') or ev.bot_id or '').strip()
            exact: dict[str, MemberCandidate] = {}
            fallback: dict[str, MemberCandidate] = {}
            for user in users or []:
                user_id = str(getattr(user, 'user_id', '') or '').strip()
                if not user_id or user_id in excluded:
                    continue
                name = next(
                    (
                        name
                        for field in ('user_name', 'nickname', 'name', 'username')
                        if (name := valid_display_name(getattr(user, field, ''), user_id))
                    ),
                    user_id,
                )
                candidate = MemberCandidate(
                    name=name,
                    user_id=user_id,
                    avatar=valid_member_text(getattr(user, 'user_icon', '')),
                )
                fallback[user_id] = candidate
                if preferred_bot_id and str(getattr(user, 'bot_id', '') or '').strip() == preferred_bot_id:
                    exact[user_id] = candidate
            return tuple(sorted((exact or fallback).values(), key=lambda item: (item.name, item.user_id)))

        return await self._members.get(key, load_members)

    async def resolve_avatar(self, member: MemberCandidate) -> str:
        key = member.user_id

        def resolve() -> str:
            path = avatar_cache_path(self.data_root, member.user_id)
            if usable_cached_avatar(path):
                return str(path)
            source = valid_member_text(member.avatar)
            if source.startswith(('http://', 'https://')) and download_avatar(source, path):
                return str(path)
            if source and Path(source).is_file():
                return source
            if member.user_id.isdigit() and download_avatar(qq_avatar_url(member.user_id), path):
                return str(path)
            return str(path) if usable_cached_avatar(path, check_ttl=False) else ''

        return await self._avatars.get(key, lambda: asyncio.to_thread(resolve))
