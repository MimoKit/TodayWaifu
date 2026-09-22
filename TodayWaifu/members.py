"""TodayWaifu 的群成员目录、头像与显示名。"""
from __future__ import annotations

import asyncio
import random
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy.exc import SQLAlchemyError

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.utils.database.models import CoreUser

from .constants import LOG_PREFIX, MEMBER_AVATAR_CACHE_SECONDS, _cfg_bool, _cfg_probability
from .domain import MemberCandidate, WifeRecord
from .paths import _custom_upload_data_root, _daily_rng, _user_key
from .state import _GROUP_DISPLAY_NAME_CACHE, _MEMBER_AVATAR_INFLIGHT, _MEMBER_CACHE

def _valid_display_name(value: object, user_id: str | int | None = None) -> str:
    text = str(value or '').strip()
    if text in {'', '1', 'None', 'none', 'NULL', 'null'}:
        return ''
    if user_id is not None and text == str(user_id):
        return ''
    return text


def _display_name_from_mapping(data: object, user_id: str | int | None = None) -> str:
    if not isinstance(data, dict):
        return ''
    for field in ('card', 'nickname', 'name', 'username', 'user_name'):
        value = _valid_display_name(data.get(field), user_id)
        if value:
            return value
    return ''


def _user_display_name(ev: Event, user_id: str | int | None = None) -> str:
    key = _user_key(ev, user_id)
    if user_id is None or key == str(ev.user_id):
        value = _display_name_from_mapping(ev.sender or {}, key)
        if value:
            return value
    return key


async def _load_group_display_names(ev: Event) -> dict[str, str]:
    if not ev.group_id:
        return {}

    cache_key = f'{ev.bot_id}:{ev.group_id}'

    async def load_names() -> dict[str, str]:
        try:
            users = await CoreUser.get_group_all_user(str(ev.group_id))
        except SQLAlchemyError as exc:
            logger.warning(f'{LOG_PREFIX} 读取 GsCore 群成员缓存失败: {exc}')
            return {}

        preferred_bot_id = str(ev.real_bot_id or ev.bot_id or '').strip()
        exact: dict[str, str] = {}
        fallback: dict[str, str] = {}
        for user in users or []:
            user_id = str(user.user_id or '').strip()
            if not user_id:
                continue
            name = _valid_display_name(user.user_name, user_id)
            if name:
                fallback[user_id] = name
                if preferred_bot_id and str(user.bot_id or '').strip() == preferred_bot_id:
                    exact[user_id] = name
        logger.debug(f'{LOG_PREFIX} 成功加载群 {ev.group_id} 的成员显示名称')
        return exact or fallback

    return await _GROUP_DISPLAY_NAME_CACHE.get(cache_key, load_names)


def _member_feature_enabled() -> bool:
    return _cfg_bool('DailyWifeEnableGroupMember', False)


def _marry_member_enabled() -> bool:
    return _cfg_bool('DailyWifeMarryGroupMemberEnabled', False)


def _member_probability() -> float:
    return _cfg_probability('DailyWifeGroupMemberProbability', 0.1)


def _valid_member_text(value: object) -> str:
    text = str(value or '').strip()
    if text in {'', '1', 'None', 'none', 'NULL', 'null'}:
        return ''
    return text


def _qq_avatar_url(user_id: str) -> str:
    return f'https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640'


def _member_avatar_cache_path(user_id: str) -> Path:
    safe_user_id = re.sub(r'[^0-9A-Za-z_-]+', '_', str(user_id)) or 'unknown'
    return _custom_upload_data_root() / 'group_member_avatar_cache' / f'{safe_user_id}.jpg'


def _usable_cached_avatar(path: Path, check_ttl: bool = True) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if check_ttl and time.time() - path.stat().st_mtime > MEMBER_AVATAR_CACHE_SECONDS:
            logger.debug(f'{LOG_PREFIX} 缓存的头像已过期: {path}')
            return False
        return True
    except OSError:
        return False


def _download_avatar(url: str, path: Path) -> bool:
    try:
        logger.debug(f'{LOG_PREFIX} 开始下载头像: {url} -> {path}')
        path.parent.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(request, timeout=8) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            logger.warning(f'{LOG_PREFIX} 下载头像数据无效或体积过大: {url}')
            return False
        tmp_path = path.with_suffix('.tmp')
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
        logger.debug(f'{LOG_PREFIX} 头像下载完成: {path}')
        return True
    except (OSError, HTTPError, URLError, TimeoutError) as exc:
        logger.warning(f'{LOG_PREFIX} 下载群友头像失败: {url} -> {exc}')
        return False


def _resolve_member_avatar(user_id: str, avatar_source: str) -> str:
    cache_path = _member_avatar_cache_path(user_id)
    if _usable_cached_avatar(cache_path):
        return str(cache_path)

    source = _valid_member_text(avatar_source)
    if source.startswith(('http://', 'https://')):
        if _download_avatar(source, cache_path):
            return str(cache_path)
    elif source:
        try:
            local_path = Path(source)
            if local_path.is_file():
                return str(local_path)
        except (OSError, ValueError):
            logger.debug(f'{LOG_PREFIX} 头像本地路径无效: {source}')

    if str(user_id).isdigit() and _download_avatar(_qq_avatar_url(str(user_id)), cache_path):
        return str(cache_path)

    if _usable_cached_avatar(cache_path, check_ttl=False):
        return str(cache_path)
    return ''


async def _load_group_member_candidates(ev: Event) -> tuple[MemberCandidate, ...]:
    if not ev.group_id:
        return ()

    cache_key = f'{ev.bot_id}:{ev.group_id}'

    async def load_members() -> tuple[MemberCandidate, ...]:
        try:
            users = await CoreUser.get_group_all_user(str(ev.group_id))
        except SQLAlchemyError as exc:
            logger.warning(f'{LOG_PREFIX} 读取 GsCore 群成员缓存失败: {exc}')
            return ()

        bot_ids = {
            str(item).strip()
            for item in (
                ev.bot_id,
                ev.real_bot_id,
                ev.bot_self_id,
            )
            if str(item or '').strip()
        }
        excluded_user_ids = set(bot_ids)
        preferred_bot_id = str(ev.real_bot_id or ev.bot_id or '').strip()
        exact: dict[str, MemberCandidate] = {}
        fallback: dict[str, MemberCandidate] = {}

        for user in users or []:
            user_id = str(user.user_id or '').strip()
            if not user_id or user_id in excluded_user_ids:
                continue
            # CoreUser 的展示名列固定为 user_name（不存在 nickname/name/username 列）
            name = _valid_display_name(user.user_name, user_id) or user_id
            avatar = _valid_member_text(user.user_icon)
            candidate = MemberCandidate(name=name, user_id=user_id, avatar=avatar)
            fallback[user_id] = candidate
            if preferred_bot_id and str(user.bot_id or '').strip() == preferred_bot_id:
                exact[user_id] = candidate

        result = exact or fallback
        logger.debug(f'{LOG_PREFIX} 获取到 {len(result)} 个群友候选对象')
        return tuple(sorted(result.values(), key=lambda item: (item.name, item.user_id)))

    return await _MEMBER_CACHE.get(cache_key, load_members)


async def _resolve_member_candidate_avatar(member: MemberCandidate) -> MemberCandidate | None:
    task = _MEMBER_AVATAR_INFLIGHT.get(member.user_id)
    if task is None:
        task = asyncio.create_task(
            asyncio.to_thread(_resolve_member_avatar, member.user_id, member.avatar)
        )
        _MEMBER_AVATAR_INFLIGHT[member.user_id] = task
    try:
        avatar = await task
    finally:
        if task.done() and _MEMBER_AVATAR_INFLIGHT.get(member.user_id) is task:
            _MEMBER_AVATAR_INFLIGHT.pop(member.user_id, None)
    if not avatar:
        return None
    return MemberCandidate(member.name, member.user_id, avatar)


async def _pick_group_member(
    ev: Event,
    rng: random.Random,
    exclude_user_id: str | int | None = None,
) -> MemberCandidate | None:
    candidates = list(await _load_group_member_candidates(ev))
    if not candidates:
        return None

    target_user_id = str(exclude_user_id if exclude_user_id is not None else ev.user_id).strip()
    exclude_ids = {target_user_id}
    bot_self_id = str(ev.bot_self_id or '').strip()
    if bot_self_id:
        exclude_ids.add(bot_self_id)

    candidates = [c for c in candidates if str(c.user_id) not in exclude_ids]
    if not candidates:
        logger.warning(f'{LOG_PREFIX} 过滤自身及Bot后无可用群友候选')
        return None

    rng.shuffle(candidates)
    for member in candidates:
        resolved = await _resolve_member_candidate_avatar(member)
        if resolved is not None:
            logger.debug(f'{LOG_PREFIX} 成功挑选群友: {resolved.name} ({resolved.user_id})')
            return resolved
    logger.warning(f'{LOG_PREFIX} 未能成功获取任一群友的有效头像')
    return None


async def _roll_group_member_wife(ev: Event, user_id: str | int | None = None, rng: random.Random | None = None) -> WifeRecord | None:
    if not _member_feature_enabled() or not ev.group_id:
        return None

    probability = _member_probability()
    if probability <= 0:
        return None

    key = _user_key(ev, user_id)
    hit_rng = rng or _daily_rng(ev, key, 'group_member_probability')
    rolled_prob = hit_rng.random()
    if rolled_prob >= probability:
        logger.debug(f'{LOG_PREFIX} 抽群友检定未通过: {rolled_prob:.4f} >= {probability}')
        return None

    logger.debug(f'{LOG_PREFIX} 触发抽群友逻辑')
    pick_rng = rng or _daily_rng(ev, key, 'group_member_pick')
    member = await _pick_group_member(ev, pick_rng, exclude_user_id=user_id)
    if member is None:
        return None
    return WifeRecord.from_member(member)
