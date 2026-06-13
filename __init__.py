from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

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
CACHE_TTL_SECONDS = 300
MEMBER_AVATAR_CACHE_SECONDS = 7 * 24 * 60 * 60
# 本地图片读取相关常量
ROLE_MAP_RE = re.compile(r'^\s*(\d+)\s*[:：]\s*(.+?)\s*$')
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'}
EXCLUDED_ROLE_NAMES = {
    '仇远',
    '凌阳',
    '卡卡罗',
    '布兰特',
    '忌炎',
    '渊武',
    '相里要',
    '秋水',
    '莫特斐',
    '陆·赫斯',
}
EXCLUDED_ROLE_KEYWORDS = ('漂泊者',)
# 本地角色候选缓存
CANDIDATE_CACHE: tuple[float, tuple['RoleCandidate', ...]] | None = None


@dataclass(frozen=True)
class RoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


@dataclass(frozen=True)
class MemberCandidate:
    name: str
    user_id: str
    avatar: str


@dataclass(frozen=True)
class WifeRecord:
    name: str
    role_ids: tuple[str, ...]
    image: str
    record_type: str = 'role'
    target_user_id: str = ''

    @classmethod
    def from_role(cls, role: RoleCandidate, image: str) -> 'WifeRecord':
        return cls(role.name, role.role_ids, image)

    @classmethod
    def from_member(cls, member: MemberCandidate) -> 'WifeRecord':
        return cls(member.name, ('群友',), member.avatar, 'member', member.user_id)

    def to_role(self) -> RoleCandidate:
        return RoleCandidate(self.name, self.role_ids, (self.image,))

    def to_member(self) -> MemberCandidate:
        return MemberCandidate(self.name, self.target_user_id, self.image)


def _cfg(key: str) -> Any:
    return DailyWifeConfig.get_config(key).data


def _cfg_bool(key: str, default: bool = False) -> bool:
    value = _cfg(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'true', '1', 'yes', 'y', 'on', 'enable', 'enabled', '开启'}:
            return True
        if text in {'false', '0', 'no', 'n', 'off', 'disable', 'disabled', '关闭'}:
            return False
    return default


def _cfg_probability(key: str, default: float = 0.0) -> float:
    try:
        value = float(_cfg(key))
    except (TypeError, ValueError):
        value = default
    return max(0.0, min(1.0, value))


def _configured_path(key: str) -> Path | None:
    '''读取配置中的路径，留空返回 None。'''
    raw = str(_cfg(key) or '').strip().strip('"')
    if not raw:
        return None
    return Path(raw).expanduser()


def _resolve_role_map_path() -> Path | None:
    '''定位角色 ID 对照表：优先配置路径，其次插件内置 role_id_map.txt。'''
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
    '''定位本地角色立绘目录 custom_role_pile：优先配置路径，其次自动查找。'''
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
    '''解析「ID：角色名」格式的对照表。'''
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


def _role_images(role_dir: Path) -> tuple[str, ...]:
    '''递归收集某角色目录下的所有图片，返回本地路径字符串。'''
    images = [
        path
        for path in role_dir.rglob('*')
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return tuple(str(path) for path in sorted(images, key=lambda path: str(path).lower()))


def _collect_role_candidates(role_map: dict[str, str], pile_root: Path) -> tuple[RoleCandidate, ...]:
    '''按对照表把本地目录里的图片归并成角色候选。'''
    grouped: dict[str, dict[str, list[Any]]] = {}
    for role_id in sorted(role_map.keys(), key=lambda item: int(item) if item.isdigit() else item):
        role_name = role_map[role_id]
        if _is_excluded_role(role_name):
            continue
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


def _load_local_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    '''从本地目录加载角色候选。'''
    role_map_path = _resolve_role_map_path()
    if role_map_path is None:
        return None, '没有找到鸣潮角色 ID 对照表。'

    pile_root = _resolve_role_pile_root()
    if pile_root is None:
        return None, '没有找到 custom_role_pile 图片目录。'

    try:
        role_map = _load_role_map(role_map_path)
        candidates = _collect_role_candidates(role_map, pile_root)
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 读取本地图片目录失败: {exc}')
        return None, '读取本地图片目录失败。'

    if not candidates:
        return None, 'custom_role_pile 里没有找到可用角色图片。'
    return candidates, None


def _normalize_role_name(name: str) -> str:
    '''归一化角色名：统一中点分隔符并去空白，避免因 ・/·/空格 差异导致性别误判。'''
    return name.replace('・', '·').replace('•', '·').strip()


# 预归一化的男角色名单，匹配时两边都归一化，杜绝分隔符差异
_MALE_ROLE_NAMES_NORM = {_normalize_role_name(n) for n in EXCLUDED_ROLE_NAMES}


def _is_male_role(name: str) -> bool:
    '''是否男角色（用于今日老婆/今日老公的性别过滤）。'''
    return _normalize_role_name(name) in _MALE_ROLE_NAMES_NORM


def _is_excluded_role(name: str) -> bool:
    '''是否始终排除的角色（如漂泊者，性别不固定）。'''
    return any(keyword in name for keyword in EXCLUDED_ROLE_KEYWORDS)


def _husband_enabled() -> bool:
    return _cfg_bool('DailyWifeHusbandEnabled', False)


def _filter_by_mode(candidates: tuple['RoleCandidate', ...], mode: str) -> tuple['RoleCandidate', ...]:
    '''按模式过滤候选：husband 只留男角色，wife 只留非男角色。'''
    if mode == 'husband':
        return tuple(role for role in candidates if _is_male_role(role.name))
    return tuple(role for role in candidates if not _is_male_role(role.name))


async def _load_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    global CANDIDATE_CACHE

    now = time.time()
    if CANDIDATE_CACHE and now - CANDIDATE_CACHE[0] < CACHE_TTL_SECONDS:
        return CANDIDATE_CACHE[1], None

    # 本地读取为同步文件操作，放到线程里避免阻塞
    candidates, error = await asyncio.to_thread(_load_local_candidates)
    if error or not candidates:
        return None, error

    CANDIDATE_CACHE = (now, candidates)
    return candidates, None


def _daily_rng(ev: Event, user_id: str | int | None = None, salt: str = '') -> random.Random:
    group_key = ev.group_id or 'direct'
    target_user_id = ev.user_id if user_id is None else user_id
    seed = f'{date.today().isoformat()}:{target_user_id}:{group_key}'
    if salt:
        seed = f'{seed}:{salt}'
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


def _wife_data_path() -> Path:
    return BASE_DIR / 'daily_wife_data.json'


def _today_key() -> str:
    return date.today().isoformat()


def _context_key(ev: Event) -> str:
    return f'{ev.bot_id}:{ev.group_id or "direct"}'


def _user_key(ev: Event, user_id: str | int | None = None) -> str:
    return str(ev.user_id if user_id is None else user_id)


def _valid_display_name(value: Any, user_id: str | int | None = None) -> str:
    text = str(value or '').strip()
    if text in {'', '1', 'None', 'none', 'NULL', 'null'}:
        return ''
    if user_id is not None and text == str(user_id):
        return ''
    return text


def _user_display_name(ev: Event, user_id: str | int | None = None) -> str:
    key = _user_key(ev, user_id)
    if user_id is None or key == str(ev.user_id):
        sender = getattr(ev, 'sender', {}) or {}
        if isinstance(sender, dict):
            for field in ('card', 'nickname', 'name', 'username', 'user_name'):
                value = _valid_display_name(sender.get(field), key)
                if value:
                    return value
    return key


async def _load_group_display_names(ev: Event) -> dict[str, str]:
    if not ev.group_id:
        return {}

    try:
        users = await CoreUser.get_group_all_user(str(ev.group_id))
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 读取 GsCore 群成员缓存失败: {exc}')
        return {}

    preferred_bot_id = str(getattr(ev, 'real_bot_id', '') or ev.bot_id or '').strip()
    exact: dict[str, str] = {}
    fallback: dict[str, str] = {}
    for user in users or []:
        user_id = str(getattr(user, 'user_id', '') or '').strip()
        if not user_id:
            continue
        name = _valid_display_name(getattr(user, 'user_name', ''), user_id)
        if name:
            fallback[user_id] = name
            if preferred_bot_id and str(getattr(user, 'bot_id', '') or '').strip() == preferred_bot_id:
                exact[user_id] = name
    return exact or fallback


def _member_feature_enabled() -> bool:
    return _cfg_bool('DailyWifeEnableGroupMember', False)


def _marry_member_enabled() -> bool:
    return _cfg_bool('DailyWifeMarryGroupMemberEnabled', False)


def _member_probability() -> float:
    return _cfg_probability('DailyWifeGroupMemberProbability', 0.1)


def _valid_member_text(value: Any) -> str:
    text = str(value or '').strip()
    if text in {'', '1', 'None', 'none', 'NULL', 'null'}:
        return ''
    return text


def _qq_avatar_url(user_id: str) -> str:
    return f'https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640'


def _member_avatar_cache_path(user_id: str) -> Path:
    safe_user_id = re.sub(r'[^0-9A-Za-z_-]+', '_', str(user_id)) or 'unknown'
    return BASE_DIR / 'group_member_avatar_cache' / f'{safe_user_id}.jpg'


def _usable_cached_avatar(path: Path, check_ttl: bool = True) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if check_ttl and time.time() - path.stat().st_mtime > MEMBER_AVATAR_CACHE_SECONDS:
            return False
        return True
    except Exception:
        return False


def _download_avatar(url: str, path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(request, timeout=8) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            return False
        tmp_path = path.with_suffix('.tmp')
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
        return True
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 下载群友头像失败: {url} -> {exc}')
        return False


def _resolve_member_avatar(user_id: str, avatar_source: str) -> str:
    cache_path = _member_avatar_cache_path(user_id)
    if _usable_cached_avatar(cache_path):
        return str(cache_path)

    source = _valid_member_text(avatar_source)
    if source and not source.startswith(('http://', 'https://')):
        try:
            local_path = Path(source)
            if local_path.is_file():
                return str(local_path)
        except Exception:
            pass

    if str(user_id).isdigit() and _download_avatar(_qq_avatar_url(str(user_id)), cache_path):
        return str(cache_path)

    if _usable_cached_avatar(cache_path, check_ttl=False):
        return str(cache_path)
    return ''


async def _load_group_member_candidates(ev: Event) -> tuple[MemberCandidate, ...]:
    if not ev.group_id:
        return ()

    try:
        users = await CoreUser.get_group_all_user(str(ev.group_id))
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 读取 GsCore 群成员缓存失败: {exc}')
        return ()

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
    excluded_user_ids = {str(ev.user_id), *bot_ids}
    preferred_bot_id = str(getattr(ev, 'real_bot_id', '') or ev.bot_id or '').strip()
    exact: dict[str, MemberCandidate] = {}
    fallback: dict[str, MemberCandidate] = {}

    for user in users or []:
        user_id = str(getattr(user, 'user_id', '') or '').strip()
        if not user_id or user_id in excluded_user_ids:
            continue
        name = _valid_display_name(getattr(user, 'user_name', ''), user_id) or user_id
        avatar = _valid_member_text(getattr(user, 'user_icon', ''))
        candidate = MemberCandidate(name=name, user_id=user_id, avatar=avatar)
        fallback[user_id] = candidate
        if preferred_bot_id and str(getattr(user, 'bot_id', '') or '').strip() == preferred_bot_id:
            exact[user_id] = candidate

    result = exact or fallback
    return tuple(sorted(result.values(), key=lambda item: (item.name, item.user_id)))


async def _resolve_member_candidate_avatar(member: MemberCandidate) -> MemberCandidate | None:
    avatar = await asyncio.to_thread(_resolve_member_avatar, member.user_id, member.avatar)
    if not avatar:
        return None
    return MemberCandidate(member.name, member.user_id, avatar)


async def _pick_group_member(ev: Event, rng: random.Random) -> MemberCandidate | None:
    candidates = list(await _load_group_member_candidates(ev))
    if not candidates:
        return None

    rng.shuffle(candidates)
    for member in candidates:
        resolved = await _resolve_member_candidate_avatar(member)
        if resolved is not None:
            return resolved
    return None


async def _roll_group_member_wife(ev: Event, user_id: str | int | None = None, rng: random.Random | None = None) -> WifeRecord | None:
    if not _member_feature_enabled() or not ev.group_id:
        return None

    probability = _member_probability()
    if probability <= 0:
        return None

    key = _user_key(ev, user_id)
    hit_rng = rng or _daily_rng(ev, key, 'group_member_probability')
    if hit_rng.random() >= probability:
        return None

    pick_rng = rng or _daily_rng(ev, key, 'group_member_pick')
    member = await _pick_group_member(ev, pick_rng)
    if member is None:
        return None
    return WifeRecord.from_member(member)


def _load_wife_data() -> dict[str, Any]:
    path = _wife_data_path()
    if not path.is_file():
        return {'days': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning(f'[gs_wuwa_daily_wife] 读取数据文件失败，将使用空数据: {exc}')
        return {'days': {}}
    if not isinstance(data, dict):
        return {'days': {}}
    data.setdefault('days', {})
    return data


def _save_wife_data(data: dict[str, Any]) -> None:
    _wife_data_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _get_today_context(data: dict[str, Any], ev: Event) -> dict[str, Any]:
    day = data.setdefault('days', {}).setdefault(_today_key(), {})
    context = day.setdefault(_context_key(ev), {})
    context.setdefault('wives', {})
    context.setdefault('husbands', {})
    context.setdefault('marry_members', {})
    context.setdefault('rob_attempts', {})
    return context


def _record_to_dict(record: WifeRecord, ev: Event | None = None, user_id: str | int | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        'name': record.name,
        'role_ids': list(record.role_ids),
        'image': record.image,
        'record_type': record.record_type,
    }
    if record.target_user_id:
        data['target_user_id'] = record.target_user_id
    if ev is not None:
        data.update(
            {
                'user_id': _user_key(ev, user_id),
                'display_name': _user_display_name(ev, user_id),
                'group_id': str(ev.group_id or 'direct'),
                'bot_id': str(ev.bot_id),
                'day': _today_key(),
                'updated_at': int(time.time()),
            }
        )
    return data


def _is_valid_image_ref(image: str) -> bool:
    '''图片引用是否有效：存在的本地文件。'''
    if not image:
        return False
    try:
        return Path(image).is_file()
    except Exception:
        return False


def _record_from_dict(data: dict[str, Any]) -> WifeRecord | None:
    try:
        record = WifeRecord(
            name=str(data['name']),
            role_ids=tuple(str(item) for item in data.get('role_ids', ())),
            image=str(data['image']),
            record_type=str(data.get('record_type') or 'role'),
            target_user_id=str(data.get('target_user_id') or ''),
        )
    except Exception:
        return None
    if not record.name:
        return None
    if record.record_type == 'member':
        if record.image and not _is_valid_image_ref(record.image):
            return None
        return record
    if not _is_valid_image_ref(record.image):
        return None
    return record


def _get_event_target_user_id(ev: Event) -> str | None:
    for attr in ('at_list', 'at', 'target_id', 'target_user_id'):
        value = getattr(ev, attr, None)
        if value is not None:
            if isinstance(value, (list, tuple, set)):
                value = next(iter(value), None)

            if isinstance(value, bool):
                continue

            v_str = str(value).strip()
            if v_str and v_str.lower() not in ('none', 'true', 'false', 'all'):
                match = re.search(r'(\d{5,20})', v_str)
                if match:
                    return match.group(1)
                if 'CQ:at' not in v_str and '<at' not in v_str:
                    return v_str

    for attr in ('text', 'raw_text', 'raw_message', 'message', 'original_message'):
        t = getattr(ev, attr, None)
        if t is not None:
            t_str = str(t).strip()
            if t_str:
                match = re.search(r'(?:@|qq=|qq:|QQ=|QQ:)?(\d{5,20})', t_str)
                if match:
                    return match.group(1)

    return None


def _rob_success_rate() -> float:
    try:
        value = float(_cfg('DailyWifeRobSuccessRate'))
    except (TypeError, ValueError):
        value = 0.5
    return max(0.0, min(1.0, value))


def _build_rob_success_text(role: RoleCandidate, target_user_id: str) -> str:
    return str(_cfg('DailyWifeRobSuccessTemplate') or '抢老婆成功！你把对方今天的老婆{name}抢过来了！').format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )


def _build_text(role: RoleCandidate, mode: str = 'wife') -> str:
    if mode == 'husband':
        template = str(_cfg('DailyHusbandTextTemplate') or '你今天的老公是{name}')
    else:
        template = str(_cfg('DailyWifeTextTemplate') or '你今天的老婆是{name}')
    lines = [
        template.format(
            name=role.name,
            role_id='/'.join(role.role_ids),
        )
    ]
    if bool(_cfg('DailyWifeShowRoleId')):
        lines.append(f'角色ID：{"/".join(role.role_ids)}')
    return '\n'.join(lines)


def _build_member_text(member: MemberCandidate, mode: str = 'daily') -> str:
    if mode == 'marry':
        template = str(_cfg('DailyWifeMarryGroupMemberTextTemplate') or '你娶到的群友是{name}')
    else:
        template = str(_cfg('DailyWifeGroupMemberTextTemplate') or '你今天的老婆是{name}')
    lines = [template.format(name=member.name, user_id=member.user_id)]
    lines.append(f'QQ：{member.user_id}')
    return '\n'.join(lines)


def _record_text(record: WifeRecord, mode: str = 'wife') -> str:
    if record.record_type == 'member':
        return _build_member_text(record.to_member())
    return _build_text(record.to_role(), mode)


async def _ensure_daily_wife_record(
    ev: Event, user_id: str | int | None = None, mode: str = 'wife'
) -> WifeRecord | None:
    bucket = 'husbands' if mode == 'husband' else 'wives'
    salt = 'husband' if mode == 'husband' else ''
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    key = _user_key(ev, user_id)
    current = context[bucket].get(key)
    if isinstance(current, dict):
        record = _record_from_dict(current)
        if record is not None:
            return record

    if mode == 'wife':
        member_record = await _roll_group_member_wife(ev, key)
        if member_record is not None:
            context[bucket][key] = _record_to_dict(member_record, ev, key)
            _save_wife_data(data)
            return member_record

    candidates, error = await _load_candidates()
    if error or not candidates:
        return None
    candidates = _filter_by_mode(candidates, mode)
    if not candidates:
        return None

    rng = _daily_rng(ev, key, salt)
    role = rng.choice(candidates)
    image = rng.choice(role.images)
    record = WifeRecord.from_role(role, image)
    context[bucket][key] = _record_to_dict(record, ev, key)
    _save_wife_data(data)
    return record


def _get_existing_daily_wife_record(ev: Event, user_id: str | int) -> WifeRecord | None:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = context['wives'].get(_user_key(ev, user_id))
    if isinstance(current, dict):
        return _record_from_dict(current)
    return None


def _save_daily_wife_record(
    ev: Event, record: WifeRecord, user_id: str | int | None = None, mode: str = 'wife'
) -> None:
    bucket = 'husbands' if mode == 'husband' else 'wives'
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    key = _user_key(ev, user_id)
    context[bucket][key] = _record_to_dict(record, ev, key)
    _save_wife_data(data)


async def _wife_list_text(ev: Event, mode: str = 'wife') -> str:
    bucket = 'husbands' if mode == 'husband' else 'wives'
    title = '老公' if mode == 'husband' else '老婆'
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    wives = context.get(bucket, {})
    if not isinstance(wives, dict) or not wives:
        return f'今天本群还没有人抽{title}。'

    group_display_names = await _load_group_display_names(ev)
    data_changed = False
    items: list[tuple[int, str, str]] = []
    for user_id, raw_record in wives.items():
        if not isinstance(raw_record, dict):
            continue
        record = _record_from_dict(raw_record)
        if record is None:
            continue
        display_name = _valid_display_name(raw_record.get('display_name'), user_id)
        if not display_name:
            display_name = group_display_names.get(str(user_id), '')
            if display_name:
                raw_record['display_name'] = display_name
                raw_record['display_name_source'] = 'coreuser'
                raw_record['display_name_updated_at'] = int(time.time())
                data_changed = True
        if not display_name:
            display_name = str(user_id)
        updated_at = raw_record.get('updated_at')
        try:
            order = int(updated_at)
        except (TypeError, ValueError):
            order = 0
        if raw_record.get('stolen_by'):
            wife_name = '被抢走了~'
        else:
            wife_name = record.name

        items.append((order, display_name, wife_name))

    if not items:
        return f'今天本群还没有可用的{title}记录。'

    if data_changed:
        _save_wife_data(data)

    items.sort(key=lambda item: (item[0], item[1]))
    lines = [f'今日{title}列表：']
    lines.extend(f'{index}. {display_name} → {wife_name}' for index, (_, display_name, wife_name) in enumerate(items, 1))
    return '\n'.join(lines)


async def _send_role_image(
    bot: Bot,
    role: RoleCandidate,
    image_url: str,
    text: str | None = None,
    user_id: str | int | None = None,
) -> None:
    # 只支持本地图片：校验文件存在后直接用路径发送
    if not Path(image_url).is_file():
        logger.warning(f'[gs_wuwa_daily_wife] 本地图片不存在: {image_url}')
        await bot.send('本地图片文件不存在，请检查 custom_role_pile 目录。')
        return
    image: Any = Path(image_url)

    messages: list[Any] = []
    if user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    messages.append(MessageSegment.image(image))
    await bot.send(messages if len(messages) > 1 else messages[0])


async def _send_local_image(
    bot: Bot,
    image_url: str,
    missing_hint: str,
    text: str | None = None,
    user_id: str | int | None = None,
) -> None:
    messages: list[Any] = []
    if user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    if image_url:
        if not Path(image_url).is_file():
            logger.warning(f'[gs_wuwa_daily_wife] 本地图片不存在: {image_url}')
            if not text:
                await bot.send(missing_hint)
                return
        else:
            messages.append(MessageSegment.image(Path(image_url)))

    if not messages:
        await bot.send(missing_hint)
        return
    await bot.send(messages if len(messages) > 1 else messages[0])


async def _send_record_image(
    bot: Bot,
    record: WifeRecord,
    mode: str = 'wife',
    user_id: str | int | None = None,
) -> None:
    text = _record_text(record, mode) if bool(_cfg('DailyWifeSendText')) else None
    hint = '本地群友头像文件不存在，请稍后重试。' if record.record_type == 'member' else '本地图片文件不存在，请检查 custom_role_pile 目录。'
    await _send_local_image(bot, record.image, hint, text, user_id)


async def _send_daily_wife(bot: Bot, ev: Event, mode: str = 'wife'):
    title = '老公' if mode == 'husband' else '老婆'
    salt = 'husband' if mode == 'husband' else ''
    # 老婆模式下，若今天的老婆已被抢走，则不再补抽（老公模式没有抢夺概念）
    if mode == 'wife' and not (bool(_cfg('DailyWifeMasterUnlimited')) and _is_master(ev)):
        data = _load_wife_data()
        context = _get_today_context(data, ev)
        user_key = _user_key(ev)
        current_record = context['wives'].get(user_key)

        if isinstance(current_record, dict) and current_record.get('stolen_by'):
            wife_name = current_record.get('name', '老婆')
            stolen_by_name = current_record.get('stolen_by_name') or current_record.get('stolen_by')
            return await bot.send(f'你的{wife_name}已经被{stolen_by_name}抢走了，今天就先忍忍吧~')

    if bool(_cfg('DailyWifeMasterUnlimited')) and _is_master(ev):
        rng = random.Random()
        record: WifeRecord | None = None
        if mode == 'wife':
            record = await _roll_group_member_wife(ev, rng=rng)

        if record is None:
            candidates, error = await _load_candidates()
            if error or not candidates:
                return await bot.send(error or '没有找到可用角色。')

            candidates = _filter_by_mode(candidates, mode)
            if not candidates:
                return await bot.send(f'没有找到可用的{title}角色。')

            role = rng.choice(candidates)
            image = rng.choice(role.images)
            record = WifeRecord.from_role(role, image)
    else:
        record = await _ensure_daily_wife_record(ev, mode=mode)
        if record is None:
            return await bot.send(f'没有找到可用的{title}角色。')

    _save_daily_wife_record(ev, record, mode=mode)

    if record.record_type == 'member':
        member = record.to_member()
        logger.info(
            f'[gs_wuwa_daily_wife] mode={mode} user={ev.user_id} group={ev.group_id or "direct"} '
            f'member={member.name} qq={member.user_id} avatar={record.image}'
        )
    else:
        role = record.to_role()
        logger.info(
            f'[gs_wuwa_daily_wife] mode={mode} user={ev.user_id} group={ev.group_id or "direct"} '
            f'role={role.name} ids={role.role_ids} image={record.image}'
        )

    await _send_record_image(bot, record, mode, ev.user_id)


async def _send_group_member_wife(bot: Bot, ev: Event):
    if not _marry_member_enabled():
        return await bot.send('娶群友功能当前已关闭。')
    if not ev.group_id:
        return await bot.send('这个命令只能在群聊里使用。')

    member = await _pick_group_member(ev, _event_rng(ev))
    if member is None:
        return await bot.send('没有获取到本群成员，暂时娶不到群友。')

    logger.info(
        f'[gs_wuwa_daily_wife] marry_member user={ev.user_id} group={ev.group_id} '
        f'member={member.name} qq={member.user_id} avatar={member.avatar}'
    )
    text = _build_member_text(member, 'marry') if bool(_cfg('DailyWifeSendText')) else None
    await _send_local_image(bot, member.avatar, '本地群友头像文件不存在，请稍后重试。', text, ev.user_id)


async def _send_rob_wife(bot: Bot, ev: Event):
    if not _cfg_bool('DailyWifeRobEnabled', True):
        return await bot.send('抢老婆功能当前已关闭。')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await bot.send('要抢谁的老婆？请艾特对方或在命令后面写对方 QQ。')

    robber_id = _user_key(ev)
    if target_user_id == robber_id:
        return await bot.send('自己抢自己的老婆也太奇怪了吧！')

    target_record = _get_existing_daily_wife_record(ev, target_user_id)
    if target_record is None:
        return await bot.send('对方今天还没有老婆呢~')
    if target_record.record_type == 'member':
        return await bot.send('对方今天娶到的是群友，不能被抢走哦~')

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    attempts = context.setdefault('rob_attempts', {})
    is_master = _is_master(ev)
    if not is_master and attempts.get(robber_id):
        return await bot.send('今天已经抢过老婆啦，明天再来吧！')

    if not is_master:
        attempts[robber_id] = True

    if random.random() >= _rob_success_rate():
        _save_wife_data(data)
        return await bot.send('抢老婆失败了，还被对方痛扁了一顿！🤣')

    context['wives'][robber_id] = _record_to_dict(target_record, ev, robber_id)
    context['wives'][robber_id]['stolen_from'] = target_user_id

    robber_name = _user_display_name(ev, robber_id)
    if isinstance(context['wives'].get(target_user_id), dict):
        context['wives'][target_user_id]['stolen_by'] = robber_id
        context['wives'][target_user_id]['stolen_by_name'] = robber_name

    _save_wife_data(data)

    role = target_record.to_role()
    text = _build_rob_success_text(role, target_user_id)
    await _send_role_image(bot, role, target_record.image, text, robber_id)


async def _send_wife_list(bot: Bot, ev: Event, mode: str = 'wife'):
    await bot.send(await _wife_list_text(ev, mode))


@sv.on_fullmatch('今日老婆', block=True)
async def daily_wife(bot: Bot, ev: Event):
    await _send_daily_wife(bot, ev)


@sv.on_fullmatch(('老婆列表', '今日老婆列表'), block=True)
async def daily_wife_list(bot: Bot, ev: Event):
    await _send_wife_list(bot, ev)


@sv.on_fullmatch('今日老公', block=True)
async def daily_husband(bot: Bot, ev: Event):
    if not _husband_enabled():
        return await bot.send('今日老公功能当前已关闭。')
    await _send_daily_wife(bot, ev, mode='husband')


@sv.on_fullmatch(('老公列表', '今日老公列表'), block=True)
async def daily_husband_list(bot: Bot, ev: Event):
    if not _husband_enabled():
        return await bot.send('今日老公功能当前已关闭。')
    await _send_wife_list(bot, ev, mode='husband')


@sv.on_fullmatch('娶群友', block=True)
async def group_member_wife(bot: Bot, ev: Event):
    await _send_group_member_wife(bot, ev)


@sv.on_prefix(('抢老婆', '抢今日老婆'), block=True)
async def rob_wife(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)


@sv.on_fullmatch(('抢老婆', '抢今日老婆'), block=True)
async def rob_wife_at(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)
