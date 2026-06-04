from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from gsuid_core.bot import Bot
from gsuid_core.config import core_config
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.sv import Plugins, SV
from gsuid_core.utils.database.models import CoreUser

from .daily_wife_config import DailyWifeConfig


Plugins(
    name='gs_wuwa_daily_wife',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

sv = SV('鸣潮今日老婆')
BASE_DIR = Path(__file__).parent
ROLE_MAP_RE = re.compile(r'^\s*(\d+)\s*[:：]\s*(.+?)\s*$')
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}


@dataclass(frozen=True)
class RoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[Path, ...]


@dataclass(frozen=True)
class MemberCandidate:
    name: str
    user_id: str
    avatar: str


def _cfg(key: str) -> Any:
    return DailyWifeConfig.get_config(key).data


def _configured_path(key: str) -> Path | None:
    raw = str(_cfg(key) or '').strip().strip('"')
    if not raw:
        return None
    return Path(raw).expanduser()


def _resolve_role_map_path() -> Path | None:
    configured = _configured_path('DailyWifeRoleMapPath')
    candidates = [
        configured,
        BASE_DIR / 'role_id_map.txt',
        BASE_DIR.parent / '鸣潮面板id对照角色.txt',
        Path.cwd() / '鸣潮面板id对照角色.txt',
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    return None


def _resolve_role_pile_root() -> Path | None:
    configured = _configured_path('DailyWifeCustomRolePilePath')
    candidates = [configured] if configured else []
    candidates.extend(
        [
            Path.cwd() / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
            Path.cwd() / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
            BASE_DIR.parent / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
            BASE_DIR.parent / 'data' / 'XutheringWavesUID' / 'custom_role_pile',
        ]
    )

    try:
        import gsuid_core

        core_root = Path(gsuid_core.__file__).resolve().parents[1]
        candidates.append(core_root / 'data' / 'XutheringWavesUID' / 'custom_role_pile')
    except Exception:
        pass

    for path in candidates:
        if path and path.is_dir():
            return path
    return None


def _load_role_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        match = ROLE_MAP_RE.match(line)
        if not match:
            continue
        role_id, role_name = match.groups()
        role_name = role_name.strip()
        if role_name:
            result[role_id] = role_name
    return result


def _role_images(role_dir: Path) -> tuple[Path, ...]:
    images = [
        path
        for path in role_dir.rglob('*')
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return tuple(sorted(images, key=lambda path: str(path).lower()))


def _collect_role_candidates(role_map: dict[str, str], pile_root: Path) -> tuple[RoleCandidate, ...]:
    grouped: dict[str, dict[str, list[Any]]] = {}
    for role_id in sorted(role_map.keys(), key=lambda item: int(item) if item.isdigit() else item):
        role_name = role_map[role_id]
        role_dir = pile_root / role_id
        if not role_dir.is_dir():
            continue
        images = _role_images(role_dir)
        if not images:
            continue
        bucket = grouped.setdefault(role_name, {'role_ids': [], 'images': []})
        bucket['role_ids'].append(role_id)
        bucket['images'].extend(images)

    candidates: list[RoleCandidate] = []
    for role_name, bucket in grouped.items():
        candidates.append(
            RoleCandidate(
                name=role_name,
                role_ids=tuple(str(item) for item in bucket['role_ids']),
                images=tuple(bucket['images']),
            )
        )
    return tuple(sorted(candidates, key=lambda item: item.name))


def _daily_rng(ev: Event) -> random.Random:
    # 按日期、用户和当前会话固定结果；群聊会区分不同群，私聊单独固定。
    group_key = ev.group_id or 'direct'
    seed = f'{date.today().isoformat()}:{ev.user_id}:{group_key}'
    return random.Random(seed)


def _is_master(ev: Event) -> bool:
    try:
        masters = core_config.get_config('masters')
    except Exception:
        masters = []
    return str(ev.user_id) in {str(master) for master in masters}


def _event_rng(ev: Event) -> random.Random:
    if bool(_cfg('DailyWifeMasterUnlimited')) and _is_master(ev):
        return random.Random()
    return _daily_rng(ev)


def _group_member_probability() -> float:
    value = _cfg('DailyWifeGroupMemberProbability')
    try:
        probability = float(value)
    except (TypeError, ValueError):
        probability = 0.1
    return max(0.0, min(1.0, probability))


def _qq_avatar_url(user_id: str) -> str:
    return f'https://q1.qlogo.cn/g?b=qq&s=0&nk={user_id}'


def _valid_member_text(value: Any) -> str:
    text = str(value or '').strip()
    return '' if text in {'', '1', 'None', 'none', 'null'} else text


def _onebot_api_url() -> str:
    return str(_cfg('DailyWifeOneBotApiUrl') or '').strip().rstrip('/')


def _onebot_access_token() -> str:
    return str(_cfg('DailyWifeOneBotAccessToken') or '').strip()


def _member_from_onebot_data(data: dict[str, Any]) -> MemberCandidate | None:
    user_id = _valid_member_text(data.get('user_id'))
    if not user_id:
        return None

    name = (
        _valid_member_text(data.get('card'))
        or _valid_member_text(data.get('nickname'))
        or _valid_member_text(data.get('title'))
        or user_id
    )
    avatar = _valid_member_text(data.get('avatar')) or _qq_avatar_url(user_id)
    return MemberCandidate(name=name, user_id=user_id, avatar=avatar)


async def _load_onebot_group_member_candidates(ev: Event) -> tuple[MemberCandidate, ...]:
    base_url = _onebot_api_url()
    if not base_url or not ev.group_id:
        return ()

    try:
        import httpx
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] httpx 不可用，无法直抓群成员: {exc}')
        return ()

    headers: dict[str, str] = {}
    token = _onebot_access_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f'{base_url}/get_group_member_list',
                json={'group_id': int(ev.group_id)},
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] OneBot 直抓群成员失败，将使用缓存: {exc}')
        return ()

    if isinstance(payload, dict):
        status = str(payload.get('status') or '').lower()
        retcode = payload.get('retcode')
        if status and status not in {'ok', 'async'}:
            logger.warning(f'[gs_wuwa_daily_wife] OneBot 返回异常: {payload}')
            return ()
        if retcode not in {None, 0}:
            logger.warning(f'[gs_wuwa_daily_wife] OneBot 返回异常: {payload}')
            return ()
        members_data = payload.get('data') or []
    elif isinstance(payload, list):
        members_data = payload
    else:
        return ()

    candidates: dict[str, MemberCandidate] = {}
    for item in members_data:
        if not isinstance(item, dict):
            continue
        member = _member_from_onebot_data(item)
        if not member or member.user_id == str(ev.bot_self_id or ''):
            continue
        candidates[member.user_id] = member

    return tuple(sorted(candidates.values(), key=lambda item: item.user_id))


async def _load_cached_group_member_candidates(ev: Event) -> tuple[MemberCandidate, ...]:
    if not ev.group_id:
        return ()

    try:
        users = await CoreUser.get_group_all_user(ev.group_id)
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 获取群成员缓存失败: {exc}')
        return ()

    candidates: dict[str, MemberCandidate] = {}
    for user in users or []:
        user_id = _valid_member_text(getattr(user, 'user_id', ''))
        if not user_id or user_id == str(ev.bot_self_id or ''):
            continue
        name = _valid_member_text(getattr(user, 'user_name', '')) or user_id
        avatar = _valid_member_text(getattr(user, 'user_icon', '')) or _qq_avatar_url(user_id)
        candidates[user_id] = MemberCandidate(name=name, user_id=user_id, avatar=avatar)

    return tuple(sorted(candidates.values(), key=lambda item: item.user_id))


async def _load_group_member_candidates(ev: Event) -> tuple[MemberCandidate, ...]:
    direct_members = await _load_onebot_group_member_candidates(ev)
    if direct_members:
        return direct_members
    return await _load_cached_group_member_candidates(ev)


def _select_daily_member(
    rng: random.Random,
    candidates: tuple[MemberCandidate, ...],
) -> MemberCandidate:
    return rng.choice(candidates)


def _build_member_text(member: MemberCandidate) -> str:
    return f'你今天的老婆是{member.name}\nQQ：{member.user_id}'


async def _send_group_member_wife(
    bot: Bot,
    ev: Event,
    rng: random.Random | None = None,
    *,
    force_text: bool = True,
):
    if not ev.group_id:
        return await bot.send('这个命令只能在群聊里使用。')

    members = await _load_group_member_candidates(ev)
    if not members:
        hint = '没有获取到本群成员，暂时娶不到群友。'
        if not _onebot_api_url():
            hint += '\n可在控制台配置 OneBot HTTP API 地址来直抓群成员；未配置时只能使用 GSCore 已记录成员缓存。'
        return await bot.send(hint)

    member = _select_daily_member(rng or _event_rng(ev), members)
    logger.info(
        f'[gs_wuwa_daily_wife] user={ev.user_id} group={ev.group_id} '
        f'member={member.name} qq={member.user_id}'
    )
    if force_text or bool(_cfg('DailyWifeSendText')):
        return await bot.send([
            _build_member_text(member),
            MessageSegment.image(member.avatar),
        ])
    return await bot.send(MessageSegment.image(member.avatar))


def _select_daily_wife(
    ev: Event,
    candidates: tuple[RoleCandidate, ...],
) -> tuple[RoleCandidate, Path]:
    rng = _daily_rng(ev)
    role = rng.choice(candidates)
    return role, rng.choice(role.images)


def _build_text(role: RoleCandidate) -> str:
    lines = [
        str(_cfg('DailyWifeTextTemplate') or '你今天的老婆是{name}').format(
            name=role.name,
            role_id='/'.join(role.role_ids),
        )
    ]
    if bool(_cfg('DailyWifeShowRoleId')):
        lines.append(f'角色ID：{"/".join(role.role_ids)}')
    return '\n'.join(lines)


def _load_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_map_path = _resolve_role_map_path()
    if role_map_path is None:
        return None, '没有找到鸣潮角色 ID 对照表。'

    pile_root = _resolve_role_pile_root()
    if pile_root is None:
        return None, '没有找到 custom_role_pile 图片目录。'

    role_map = _load_role_map(role_map_path)
    candidates = _collect_role_candidates(role_map, pile_root)
    if not candidates:
        return None, 'custom_role_pile 里没有找到可用角色图片。'
    return candidates, None


async def _send_daily_wife(bot: Bot, ev: Event):
    candidates, error = _load_candidates()
    if error or not candidates:
        return await bot.send(error or '没有找到可用角色。')

    rng = _event_rng(ev)
    if bool(_cfg('DailyWifeEnableGroupMember')) and ev.group_id:
        members = await _load_group_member_candidates(ev)
        if members and rng.random() < _group_member_probability():
            return await _send_group_member_wife(bot, ev, rng, force_text=False)

    role = rng.choice(candidates)
    image = rng.choice(role.images)
    logger.info(
        f'[gs_wuwa_daily_wife] user={ev.user_id} group={ev.group_id or "direct"} '
        f'role={role.name} ids={role.role_ids} image={image}'
    )

    if bool(_cfg('DailyWifeSendText')):
        await bot.send([
            _build_text(role),
            MessageSegment.image(image),
        ])
    else:
        await bot.send(MessageSegment.image(image))


@sv.on_fullmatch('今日老婆', block=True)
async def daily_wife(bot: Bot, ev: Event):
    await _send_daily_wife(bot, ev)


@sv.on_fullmatch('娶群友', block=True)
async def group_member_wife(bot: Bot, ev: Event):
    await _send_group_member_wife(bot, ev)
