
from __future__ import annotations

import asyncio
import copy
import binascii
import hashlib
import json
import os
import random
import re
import shutil
import time
from datetime import date
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from gsuid_core.bot import Bot
from gsuid_core.config import core_config
from gsuid_core.data_store import get_res_path
from gsuid_core.help.utils import register_help
from gsuid_core.logger import logger
from gsuid_core.models import Event, Message
from gsuid_core.segment import MessageSegment
from gsuid_core.server import on_core_start_before
from gsuid_core.sv import Plugins, SV
from gsuid_core.utils.database.models import CoreUser

from ..daily_wife_config import DailyWifeConfig
from .file_cache import (
    read_file_bytes_cached,
    read_file_text_cached,
    read_url_cache,
    write_url_cache,
    clear_expired_files,
)
from .folder_gallery import scan_named_role_directories
from .kind_metadata import DAILY_KIND_METADATA, DailyKindMetadata, daily_kind_metadata
from .models import DailyWifeRecord
from .storage import read_json_dict
from .messaging import (
    _adapt_mentions_for_platform, _is_at_message, _parse_send_options,
    _remove_private_mentions, _safe_send, _send_loli_text,
    _target_send_without_bot_hooks,
)
from .daily_state_helpers import (
    DAILY_WIFE_KINDS, ALL_DAILY_RECORD_KINDS,
    daily_bucket_name as _daily_bucket_name,
    daily_item_title as _daily_item_title,
    daily_kind_metadata_for as _daily_kind_metadata,
    wife_state as _wife_state,
    wife_origin as _wife_origin,
    is_secondhand_wife as _is_secondhand_wife,
    has_active_wife as _has_active_wife,
)
from .upload_access import can_upload_images, normalized_user_ids


Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

# 控制台 SV 服务拆分：不要把所有功能塞进同一个 SV，便于单独开关。
# priority 数字越小越先执行；短前缀命令放到低优先级，避免抢到长命令。
help_sv = SV('今日老婆-帮助', priority=0)
custom_role_sv = SV('今日老婆-自定义老婆', pm=1, priority=2)
assign_wife_sv = SV('今日老婆-主人分配', pm=1, priority=2)
loli_manage_sv = SV('今日老婆-萝莉图库管理', pm=1, priority=2)
image_upload_sv = SV('今日老婆-图片上传', priority=2)
specify_wife_sv = SV('今日老婆-指定老婆', priority=2)
wife_list_sv = SV('今日老婆-老婆列表', priority=3)
husband_list_sv = SV('今日老婆-老公列表', priority=3)
marry_member_sv = SV('今日老婆-娶群友', priority=3)
rob_sv = SV('今日老婆-抢老婆', priority=3)
gift_sv = SV('今日老婆-送老婆', priority=3)
divorce_sv = SV('今日老婆-离婚', priority=3)
loli_sv = SV('今日老婆-今日萝莉', priority=3)
daily_wife_sv = SV('今日老婆-每日抽取', priority=10)
daily_husband_sv = SV('今日老婆-今日老公', priority=10)
daily_nte_wife_sv = SV('今日老婆-异环老婆', priority=10)
pgr_wife_sv = SV('今日老婆-战双老婆', priority=10)
BASE_DIR = Path(__file__).parent.parent
WIFE_ROLE_MAP_PATH = BASE_DIR / 'wife_role_id_map.txt'
HUSBAND_ROLE_MAP_PATH = BASE_DIR / 'husband_role_id_map.txt'
NTE_ROLE_MAP_PATH = BASE_DIR / 'nte_role_id_map.txt'
LEGACY_ROLE_MAP_PATH = BASE_DIR / 'role_id_map.txt'
HELP_ICON_PATH = BASE_DIR / 'ICON.png'
DEFAULT_GALLERY_API_URL = 'https://img.mimokit.dpdns.org/api/xwuid/roles'
NTE_DETAIL_CDN_BASE = 'https://webstatic.tajiduo.com/bbs/yh-game-records-web-source/character/detail'
PGR_WIFE_DIR_NAME = 'pgr_wife'
CACHE_TTL_SECONDS = 300
MEMBER_AVATAR_CACHE_SECONDS = 7 * 24 * 60 * 60
CACHE_MAINTENANCE_INTERVAL_SECONDS = 60 * 60
CACHE_MAINTENANCE_FILE_LIMIT = 1000
_CACHE_MAINTENANCE_TASK: asyncio.Task[None] | None = None
LIST_FORWARD_THRESHOLD = 10
CUSTOM_ROLE_ID_START = 900001
UPLOAD_IMAGE_MAX_BYTES = 10 * 1024 * 1024
CUSTOM_ROLE_DELETE_CONFIRM_SECONDS = 120
LOLI_IMAGE_DIR_NAME = 'loli_images'
LOLICONAPP_API_URL = 'https://api.lolicon.app/setu/v2'
LOLICONAPP_TAGS = '萝莉|ロリ|loli|rori,-hololive'
LOLI_MOBILE_UA = (
    'Mozilla/5.0 (Linux; Android 13; Pixel 7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.6367.82 Mobile Safari/537.36'
)
# --- 日志前缀 ---
LOG_PREFIX = '[鸣潮今日老婆]'
LOLI_DOWNLOAD_LOG_PREFIX = '[今日萝莉下载]'

__all__ = [
    'Any', 'BASE_DIR', 'Bot', 'CACHE_TTL_SECONDS', 'CANDIDATE_CACHE',
    'CUSTOM_ROLE_DELETE_CONFIRM_SECONDS', 'CUSTOM_ROLE_DELETE_PENDING',
    'CUSTOM_ROLE_ID_START', 'CoreUser', 'DAILY_KIND_METADATA', 'DEFAULT_GALLERY_API_URL',
    'DailyKindMetadata', 'DailyWifeConfig', 'MemberCandidate', 'RoleCandidate', 'WifeRecord',
    'EXCLUDED_ROLE_KEYWORDS', 'EXCLUDED_ROLE_NAMES', 'Event', 'HELP_ICON_PATH',
    'HTTPError', 'IMAGE_EXTENSIONS', 'LIST_FORWARD_THRESHOLD', 'LOG_PREFIX',
    'LOLI_DOWNLOAD_LOG_PREFIX', 'LOLI_IMAGE_DIR_NAME', 'LOLI_MOBILE_UA',
    'LOLICONAPP_API_URL', 'LOLICONAPP_TAGS',
    'MemberCandidate', 'Message', 'MessageSegment', 'Path', 'Plugins',
    'ROLE_MAP_RE', 'Request', 'RoleCandidate', 'SV',
    'NTE_DETAIL_CDN_BASE', 'NTE_ROLE_MAP_PATH', 'UPLOAD_IMAGE_MAX_BYTES', 'URLError', 'WifeRecord',
    '_MALE_ROLE_NAMES_NORM', '_cfg', '_cfg_bool', '_cfg_probability',
    '_collect_role_candidates', '_configured_path', '_context_key',
    '_custom_upload_data_root', '_custom_upload_role_map_path',
    '_custom_upload_role_pile_root', '_daily_rng', '_download_avatar', '_download_image',
    '_download_image_sync', '_event_rng', '_fetch_gallery_payload_from_url_sync',
    '_fetch_gallery_payload_sync', '_filter_by_mode',
    '_gallery_api_url', '_gallery_mode_enabled',
    '_daily_bucket_name', '_daily_item_title', '_daily_kind_metadata', '_get_event_target_user_id',
    '_get_existing_daily_record', '_get_existing_daily_wife_record',
    '_get_other_daily_wife_name',
    '_get_today_context',
    '_has_active_wife', '_http_get', '_http_get_with_retry', '_husband_available', '_husband_enabled',
    '_image_source', '_invalidate_candidate_cache', '_loli_enabled',
    '_can_specify_wife', '_can_upload_images', '_is_excluded_role', '_is_male_role', '_is_master', '_is_secondhand_wife',
    '_is_valid_image_ref', '_load_candidates', '_load_group_display_names',
    '_load_group_member_candidates', '_load_local_candidates', '_load_role_map',
    '_load_pgr_local_candidates', '_load_pgr_wife_candidates', '_pgr_wife_root',
    '_load_wife_data', '_loli_image_root', '_marry_member_enabled',
    '_daily_context_lock',
    '_load_daily_context', '_save_daily_context',
    '_save_daily_record',
    '_mark_all_daily_records_divorced', '_member_avatar_cache_path',
    '_member_feature_enabled', '_member_probability',
    '_normalize_role_name', '_parse_role_candidates', '_pick_group_member',
    '_pick_role_record',
    '_qq_avatar_url', '_record_from_dict', '_record_to_dict',
    '_request_headers', '_resolve_default_role_pile_root',
    '_resolve_member_avatar', '_resolve_member_candidate_avatar',
    '_resolve_nte_custom_panel_root', '_resolve_nte_default_panel_root',
    '_resolve_role_map_path', '_resolve_role_pile_root', '_role_images',
    '_roll_group_member_wife', '_save_wife_data', '_send_local_image', '_send_loli_text',
    '_safe_send', '_send_daily_result_image', '_send_loli_result_image',
    '_send_role_image',
    '_today_key', '_usable_cached_avatar', '_user_display_name', '_user_key',
    '_valid_display_name', '_valid_member_text', '_wife_data_path', '_wife_origin',
    '_wife_state', '_writable_role_map_path', '_writable_role_pile_root',
    'DailyWifeRecord', '_daily_data_lock', '_migrate_legacy_wife_data',
    'read_file_bytes_cached',
    'asyncio', 'binascii', 'core_config', 'date', 'get_res_path',
    'assign_wife_sv', 'custom_role_sv', 'daily_husband_sv', 'daily_nte_wife_sv', 'daily_wife_sv',
    'divorce_sv', 'gift_sv', 'help_sv', 'husband_list_sv', 'image_upload_sv', 'loli_manage_sv', 'loli_sv',
    'marry_member_sv', 'pgr_wife_sv', 'rob_sv', 'specify_wife_sv', 'wife_list_sv',
    'hashlib', 'json', 'logger', 'random', 're', 'register_help', 'shutil', 'time',
    'urlopen', 'urlparse',
]


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
NTE_EXCLUDED_ROLE_NAMES = {
    '翳',
    '埃德嘉',
    '白藏',
    '阿德勒',
    '卡厄斯',
}
NTE_EXCLUDED_ROLE_KEYWORDS = (
    '异能者·零',
    '异能者零',
    '男主',
    '女主',
)
# 按数据源分别缓存候选，避免切换数据源后误用旧缓存
CANDIDATE_CACHE: dict[str, tuple[float, tuple['RoleCandidate', ...]]] = {}
_CANDIDATE_INFLIGHT: dict[str, asyncio.Task[tuple[tuple['RoleCandidate', ...] | None, str | None]]] = {}
_CANDIDATE_CACHE_GENERATION = 0
_CANDIDATE_LOAD_SEMAPHORE = asyncio.Semaphore(4)
_IMAGE_INFLIGHT: dict[str, asyncio.Task[bytes]] = {}
_IMAGE_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(8)
CUSTOM_ROLE_DELETE_PENDING: dict[str, dict[str, Any]] = {}




def _cfg(key: str) -> Any:
    return DailyWifeConfig.get_config(key).data


def _cfg_bool(key: str, default: bool = False) -> bool:
    value = _cfg(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
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


def _pick_role_record(
    candidates: tuple['RoleCandidate', ...],
    rng: random.Random,
) -> 'WifeRecord | None':
    if not candidates:
        return None

    role = rng.choice(candidates)
    return WifeRecord.from_role(role, rng.choice(role.images))


def _image_source() -> str:
    value = str(_cfg('DailyWifeImageSource') or 'local').strip().lower()
    return 'gallery' if value == 'gallery' else 'local'


def _configured_path(key: str) -> Path | None:
    raw = str(_cfg(key) or '').strip().strip('"')
    if not raw:
        return None
    path = Path(raw).expanduser()
    logger.debug(f'{LOG_PREFIX} 读取配置路径 {key}: {path}')
    return path


def _role_mode(mode: str) -> str:
    return _daily_kind_metadata(mode).role_mode


def _role_map_title(mode: str) -> str:
    return _daily_kind_metadata(mode).title


def _resolve_role_map_path(mode: str = 'wife') -> Path | None:
    role_mode = _role_mode(mode)
    if role_mode == 'nte':
        configured = _configured_path('DailyWifeNteRoleMapPath')
        candidates = [configured, NTE_ROLE_MAP_PATH]
        for path in candidates:
            if path and path.is_file():
                logger.debug(f'{LOG_PREFIX} 成功定位异环角色对照表文件: {path}')
                return path
        logger.warning(f'{LOG_PREFIX} 未能找到异环角色对照表文件')
        return None

    configured = _configured_path(
        'DailyWifeHusbandRoleMapPath' if role_mode == 'husband' else 'DailyWifeWifeRoleMapPath'
    )
    legacy_configured = _configured_path('DailyWifeRoleMapPath') if role_mode == 'wife' else None
    primary_builtin = HUSBAND_ROLE_MAP_PATH if role_mode == 'husband' else WIFE_ROLE_MAP_PATH
    candidates = [
        configured,
        legacy_configured,
        primary_builtin,
        LEGACY_ROLE_MAP_PATH,
        BASE_DIR.parent / ('鸣潮老公面板id对照角色.txt' if role_mode == 'husband' else '鸣潮老婆面板id对照角色.txt'),
        Path.cwd() / ('鸣潮老公面板id对照角色.txt' if role_mode == 'husband' else '鸣潮老婆面板id对照角色.txt'),
    ]
    for path in candidates:
        if path and path.is_file():
            logger.debug(f'{LOG_PREFIX} 成功定位{_role_map_title(role_mode)}角色对照表文件: {path}')
            return path
    logger.warning(f'{LOG_PREFIX} 未能找到{_role_map_title(role_mode)}角色对照表文件')
    return None


def _custom_upload_data_root() -> Path:
    return get_res_path('TodayWaifu')


def _custom_upload_role_map_path() -> Path:
    return _custom_upload_data_root() / 'custom_role_map.txt'


def _custom_upload_role_pile_root() -> Path:
    return _custom_upload_data_root() / 'custom_role_pile'


def _loli_image_root() -> Path:
    return _custom_upload_data_root() / LOLI_IMAGE_DIR_NAME


def _writable_role_map_path() -> Path:
    path = _custom_upload_role_map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _writable_role_pile_root() -> Path:
    path = _custom_upload_role_pile_root()
    path.mkdir(parents=True, exist_ok=True)
    return path

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
            logger.debug(f'{LOG_PREFIX} 成功定位自定义角色图片目录: {path}')
            return path
    logger.info(f'{LOG_PREFIX} 未能找到自定义角色图片目录 custom_role_pile')
    return None

def _resolve_default_role_pile_root() -> Path | None:
    candidates = [
        Path.cwd() / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile',
        Path.cwd() / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile',
        BASE_DIR.parent / 'gsuid_core' / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile',
        BASE_DIR.parent / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile',
    ]

    try:
        import gsuid_core
        core_root = Path(gsuid_core.__file__).resolve().parents[1]
        candidates.append(core_root / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile')
    except Exception:
        pass

    for path in candidates:
        if path and path.is_dir():
            logger.debug(f'{LOG_PREFIX} 成功定位默认角色图片目录: {path}')
            return path
    logger.info(f'{LOG_PREFIX} 未能找到默认角色图片目录 role_pile')
    return None


def _resolve_nte_data_dir(config_key: str, relative: Path) -> Path | None:
    configured = _configured_path(config_key)
    candidates = [configured] if configured else []
    candidates.extend(
        [
            get_res_path('NTEUID') / relative,
            Path.cwd() / 'gsuid_core' / 'data' / 'NTEUID' / relative,
            Path.cwd() / 'data' / 'NTEUID' / relative,
            BASE_DIR.parent / 'gsuid_core' / 'data' / 'NTEUID' / relative,
            BASE_DIR.parent / 'data' / 'NTEUID' / relative,
        ]
    )
    for path in candidates:
        if path and path.is_dir():
            return path
    return None


def _resolve_nte_custom_panel_root() -> Path | None:
    path = _resolve_nte_data_dir('DailyWifeNteCustomPanelPath', Path('custom') / 'panel')
    if path is None:
        logger.info(f'{LOG_PREFIX} 未能找到 NTEUID 自定义面板图目录')
    else:
        logger.debug(f'{LOG_PREFIX} 成功定位 NTEUID 自定义面板图目录: {path}')
    return path


def _resolve_nte_default_panel_root() -> Path | None:
    path = _resolve_nte_data_dir('DailyWifeNteDefaultPanelPath', Path('role') / 'detail')
    if path is None:
        logger.info(f'{LOG_PREFIX} 未能找到 NTEUID 默认角色立绘目录，将使用官方资源地址')
    else:
        logger.debug(f'{LOG_PREFIX} 成功定位 NTEUID 默认角色立绘目录: {path}')
    return path


def _nte_static_resource_roots() -> tuple[Path, ...]:
    candidates: list[Path] = []
    for module_name in ('NTEUID', 'gsuid_core.plugins.NTEUID', 'gsuid_core.plugins.NTEUID.NTEUID'):
        try:
            spec = find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError):
            spec = None
        if spec is None:
            continue
        locations = list(spec.submodule_search_locations or ())
        if spec.origin:
            locations.append(str(Path(spec.origin).parent))
        for location in locations:
            module_root = Path(location)
            candidates.extend((module_root / 'resource', module_root / 'NTEUID' / 'resource'))

    candidates.extend(
        [
            BASE_DIR.parent / 'NTEUID' / 'NTEUID' / 'resource',
            BASE_DIR.parent / 'NTEUID' / 'resource',
        ]
    )
    existing: list[Path] = []
    for path in candidates:
        if path.is_dir() and path not in existing:
            existing.append(path)
    return tuple(existing)

def _load_role_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    # 角色对照表按 mtime 缓存文件内容，文件变更后自动失效，避免每次抽签都读盘
    try:
        text = read_file_text_cached(path)
    except OSError:
        return result
    for line in text.splitlines():
        match = ROLE_MAP_RE.match(line)
        if not match:
            continue
        role_id, role_name = match.groups()
        role_name = role_name.strip()
        if role_name:
            result[role_id] = role_name
    logger.debug(f'{LOG_PREFIX} 加载了 {len(result)} 个角色 ID 映射关系')
    return result


def _load_custom_upload_role_map() -> dict[str, str]:
    map_path = _custom_upload_role_map_path()
    return _load_role_map(map_path) if map_path.is_file() else {}


def _role_images(role_dir: Path) -> tuple[str, ...]:
    images = [
        path
        for path in role_dir.rglob('*')
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return tuple(str(path) for path in sorted(images, key=lambda path: str(path).lower()))


def _invalidate_candidate_cache() -> None:
    global _CANDIDATE_CACHE_GENERATION
    _CANDIDATE_CACHE_GENERATION += 1
    CANDIDATE_CACHE.clear()



def _collect_role_candidates(
    role_map: dict[str, str],
    pile_root: Path,
    default_pile_root: Path | None,
    upload_pile_root: Path | None = None,
    default_name_patterns: tuple[str, ...] = ('role_pile_{role_id}{ext}',),
) -> tuple[RoleCandidate, ...]:
    grouped: dict[str, dict[str, list[Any]]] = {}
    for role_id in sorted(role_map.keys(), key=lambda item: int(item) if item.isdigit() else item):
        role_name = role_map[role_id]
        if _is_excluded_role(role_name):
            continue

        images: list[str] = []

        # 1. 优先读取 GSCore data 下本插件的自定义老婆图片，避免和 XWUID 自定义面板图目录混在一起
        if upload_pile_root and upload_pile_root.is_dir():
            upload_role_dir = upload_pile_root / role_id
            if upload_role_dir.is_dir():
                images.extend(_role_images(upload_role_dir))

        # 2. 尝试从 XWUID 自定义目录获取
        role_dir = pile_root / role_id
        if role_dir.is_dir():
            images.extend(_role_images(role_dir))

        # 3. 如果没有自定义图片，且存在默认面板目录，尝试获取默认图片
        if not images and default_pile_root and default_pile_root.is_dir():
            for pattern in default_name_patterns:
                for ext in IMAGE_EXTENSIONS:
                    fallback_img = default_pile_root / pattern.format(role_id=role_id, ext=ext)
                    if fallback_img.is_file():
                        images.append(str(fallback_img))
                        break
                if images:
                    break

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
    logger.debug(f'{LOG_PREFIX} 成功归并候选角色 {len(candidates)} 名')
    return tuple(sorted(candidates, key=lambda item: item.name))


def _load_custom_upload_candidates() -> tuple[RoleCandidate, ...]:
    role_map = _load_custom_upload_role_map()
    if not role_map:
        return ()

    upload_pile_root = _custom_upload_role_pile_root()
    if not upload_pile_root.is_dir():
        return ()

    return _collect_role_candidates(
        role_map,
        Path('dummy_non_existent_path'),
        None,
        upload_pile_root,
    )


def _merge_role_candidates(
    base: tuple[RoleCandidate, ...],
    extra: tuple[RoleCandidate, ...],
) -> tuple[RoleCandidate, ...]:
    if not extra:
        return base
    if not base:
        return tuple(sorted(extra, key=lambda item: item.name))

    grouped: dict[str, dict[str, Any]] = {}
    for candidate in (*base, *extra):
        key = _normalize_role_name(candidate.name)
        bucket = grouped.setdefault(
            key,
            {'name': candidate.name, 'role_ids': [], 'images': []},
        )
        for role_id in candidate.role_ids:
            if role_id not in bucket['role_ids']:
                bucket['role_ids'].append(role_id)
        for image in candidate.images:
            if image not in bucket['images']:
                bucket['images'].append(image)

    return tuple(
        sorted(
            (
                RoleCandidate(
                    name=str(bucket['name']),
                    role_ids=tuple(str(item) for item in bucket['role_ids']),
                    images=tuple(str(item) for item in bucket['images']),
                )
                for bucket in grouped.values()
                if bucket['role_ids'] and bucket['images']
            ),
            key=lambda item: item.name,
        )
    )


def _load_mode_role_map(mode: str = 'wife') -> dict[str, str]:
    role_map_path = _resolve_role_map_path(mode)
    return _load_role_map(role_map_path) if role_map_path else {}


def _load_local_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
    title = _role_map_title(role_mode)
    logger.debug(f'{LOG_PREFIX} 开始从本地加载{title}角色候选列表...')
    role_map_path = _resolve_role_map_path(role_mode)
    if role_map_path is None:
        return None, f'没有找到鸣潮{title}角色 ID 对照表。'

    pile_root = _resolve_role_pile_root()
    default_pile_root = _resolve_default_role_pile_root()
    upload_pile_root = _custom_upload_role_pile_root() if role_mode == 'wife' else None
    if (
        pile_root is None
        and default_pile_root is None
        and (upload_pile_root is None or not upload_pile_root.is_dir())
    ):
        return None, '没有找到 custom_role_pile 或默认 role_pile 图片目录。'

    if pile_root is None:
        pile_root = Path("dummy_non_existent_path")

    try:
        role_map = _load_role_map(role_map_path)
        if role_mode == 'wife':
            role_map.update(_load_custom_upload_role_map())
        candidates = _collect_role_candidates(role_map, pile_root, default_pile_root, upload_pile_root)
    except Exception as exc:
        logger.exception(f'{LOG_PREFIX} 读取本地图片目录失败: {exc}')
        return None, '读取本地图片目录失败。'

    if not candidates:
        logger.warning(f'{LOG_PREFIX} 扫描图片目录完成，但未找到可用角色图片')
        return None, '图片目录里没有找到可用角色图片。'
    return candidates, None


def _load_nte_local_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_map_path = _resolve_role_map_path('nte')
    if role_map_path is None:
        return None, '没有找到异环角色 ID 对照表。'

    try:
        role_map = _load_role_map(role_map_path)
    except Exception as exc:
        logger.exception(f'{LOG_PREFIX} 读取异环角色对照表失败: {exc}')
        return None, '读取异环角色 ID 对照表失败。'
    role_map = {
        role_id: role_name
        for role_id, role_name in role_map.items()
        if not _is_excluded_nte_role(role_name)
    }
    if not role_map:
        return None, '异环角色 ID 对照表为空。'

    custom_root = _resolve_nte_custom_panel_root()
    default_root = _resolve_nte_default_panel_root()
    static_roots = _nte_static_resource_roots()
    custom_candidates = _collect_role_candidates(
        role_map,
        custom_root or Path('dummy_non_existent_nte_custom_path'),
        None,
    )
    custom_by_id = {
        role_id: candidate
        for candidate in custom_candidates
        for role_id in candidate.role_ids
    }

    candidates: list[RoleCandidate] = []
    for role_id in sorted(role_map, key=lambda item: int(item) if item.isdigit() else item):
        role_name = role_map[role_id]
        custom = custom_by_id.get(role_id)
        if custom is not None:
            candidates.append(custom)
            continue

        images: tuple[str, ...] = ()
        for resource_root in static_roots:
            for subdir in (Path('char') / 'fashion', Path('char') / 'lihui'):
                role_dir = resource_root / subdir / role_id
                if role_dir.is_dir():
                    images = _role_images(role_dir)
                if images:
                    break
            if images:
                break

        if not images and default_root is not None:
            for ext in IMAGE_EXTENSIONS:
                default_image = default_root / f'{role_id}{ext}'
                if default_image.is_file():
                    images = (str(default_image),)
                    break

        if not images:
            images = (f'{NTE_DETAIL_CDN_BASE}/{role_id}.png',)
        candidates.append(RoleCandidate(role_name, (role_id,), images))

    logger.debug(f'{LOG_PREFIX} 成功加载异环老婆候选 {len(candidates)} 名')
    return tuple(candidates), None


def _pgr_wife_root() -> Path:
    configured = str(_cfg('DailyWifePgrGalleryPath') or '').strip().strip('"')
    if configured:
        return Path(configured).expanduser()
    return get_res_path('TodayWaifu') / PGR_WIFE_DIR_NAME


def _pgr_gallery_api_url() -> str:
    return str(_cfg('DailyWifePgrGalleryApiUrl') or '').strip()


def _parse_pgr_gallery_candidates(payload: dict[str, Any]) -> tuple[RoleCandidate, ...]:
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list):
        return ()
    candidates: list[RoleCandidate] = []
    for item in roles_data:
        if not isinstance(item, dict):
            continue
        role_ids = tuple(str(value).strip() for value in item.get('role_ids') or [] if str(value).strip())
        if not role_ids:
            continue
        name = str(item.get('name') or role_ids[0]).strip()
        images = tuple(
            str(image.get('url') or '').strip()
            for image in item.get('images') or []
            if isinstance(image, dict) and str(image.get('url') or '').strip().startswith(('http://', 'https://'))
        )
        if name and images:
            candidates.append(RoleCandidate(name=name, role_ids=role_ids, images=images))
    return tuple(sorted(candidates, key=lambda role: role.name))


def _load_pgr_local_candidates() -> tuple[RoleCandidate, ...]:
    # 战双图库 rglob 全量扫描是昂贵操作，接入候选缓存（TTL 见 CACHE_TTL_SECONDS），
    # 上传图片时 _invalidate_candidate_cache 会主动失效
    cache_key = f'pgr_local:{_pgr_wife_root()}'
    now = time.time()
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    rows = scan_named_role_directories(_pgr_wife_root(), IMAGE_EXTENSIONS)
    candidates = tuple(
        RoleCandidate(name=name, role_ids=(name,), images=images)
        for name, images in rows
    )
    CANDIDATE_CACHE[cache_key] = (now, candidates)
    return candidates


async def _load_pgr_wife_candidates() -> tuple[RoleCandidate, ...]:
    api_url = _pgr_gallery_api_url()
    if api_url:
        try:
            payload = await asyncio.to_thread(_fetch_gallery_payload_from_url_sync, api_url)
            candidates = _parse_pgr_gallery_candidates(payload)
            if candidates:
                return candidates
            logger.warning(f'{LOG_PREFIX} 战双远程图库没有可用角色，回退本地图库。')
        except Exception as exc:
            logger.warning(f'{LOG_PREFIX} 读取战双远程图库失败，回退本地图库: {exc}')
    return _load_pgr_local_candidates()


def _normalize_role_name(name: str) -> str:
    return name.replace('・', '·').replace('•', '·').strip()

_MALE_ROLE_NAMES_NORM = {_normalize_role_name(n) for n in EXCLUDED_ROLE_NAMES}

def _is_male_role(name: str) -> bool:
    husband_names = {
        _normalize_role_name(role_name)
        for role_name in _load_mode_role_map('husband').values()
    }
    return _normalize_role_name(name) in (husband_names or _MALE_ROLE_NAMES_NORM)

def _is_excluded_role(name: str) -> bool:
    return any(keyword in name for keyword in EXCLUDED_ROLE_KEYWORDS)


def _is_excluded_nte_role(name: str) -> bool:
    normalized = _normalize_role_name(name)
    if normalized in NTE_EXCLUDED_ROLE_NAMES:
        return True
    return any(keyword in normalized for keyword in NTE_EXCLUDED_ROLE_KEYWORDS)

def _husband_enabled() -> bool:
    return _cfg_bool('DailyWifeHusbandEnabled', False)


def _loli_enabled() -> bool:
    return _cfg_bool('DailyLoliEnabled', False)


def _gallery_mode_enabled() -> bool:
    return _image_source() == 'gallery'


def _husband_available() -> bool:
    return _husband_enabled()


def _filter_by_mode(
    candidates: tuple['RoleCandidate', ...],
    mode: str,
) -> tuple['RoleCandidate', ...]:
    role_map = _load_mode_role_map(mode)
    role_mode = _role_mode(mode)
    if role_mode == 'wife':
        role_map.update(_load_custom_upload_role_map())
    allowed_ids = set(role_map)
    allowed_names = {_normalize_role_name(name) for name in role_map.values()}
    return tuple(
        role
        for role in candidates
        if any(role_id in allowed_ids for role_id in role.role_ids)
        or _normalize_role_name(role.name) in allowed_names
    )


def _gallery_api_url() -> str:
    return str(_cfg('DailyWifeGalleryApiUrl') or DEFAULT_GALLERY_API_URL).strip()


def _request_headers() -> dict[str, str]:
    headers = {'User-Agent': 'TodayWaifu/1.0'}
    token = str(_cfg('DailyWifeGalleryToken') or '').strip()
    if token:
        headers['X-Gallery-Token'] = token
    return headers


def _http_get(url: str, *, timeout: int = 15) -> bytes:
    request = Request(url, headers=_request_headers())
    with urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _http_get_with_retry(
    url: str,
    *,
    timeout: int = 15,
    retries: int = 3,
    delay: float = 5,
) -> bytes:
    """请求远程图库接口，失败或超时时重试 `retries` 次，每次间隔 `delay` 秒。

    401 属于认证错误，重试无意义，直接抛出。
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _http_get(url, timeout=timeout)
        except HTTPError as exc:
            if exc.code == 401:
                raise
            last_exc = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_exc = exc
        if attempt < retries:
            logger.warning(
                f'{LOG_PREFIX} 请求远程图库失败(第{attempt + 1}/{retries}次重试): {url}，'
                f'{int(delay)} 秒后重试: {last_exc}'
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def _fetch_gallery_payload_sync() -> dict[str, Any]:
    api_url = _gallery_api_url()
    if not api_url:
        raise RuntimeError('未配置图库接口地址。')
    try:
        body = _http_get_with_retry(api_url, timeout=15)
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError('图库账号或密码不正确，接口返回 401。') from exc
        raise RuntimeError(f'请求图库接口失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'请求图库接口失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('请求图库接口超时。') from exc

    try:
        payload = json.loads(body.decode('utf-8'))
    except Exception as exc:
        raise RuntimeError('图库接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('图库接口返回格式不正确。')
    return payload


def _fetch_gallery_payload_from_url_sync(url: str) -> dict[str, Any]:
    body = _http_get_with_retry(url, timeout=15)
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('图库接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('图库接口返回格式不正确。')
    return payload


def _parse_role_candidates(
    payload: dict[str, Any],
    mode: str = 'wife',
    role_map: dict[str, str] | None = None,
) -> tuple[RoleCandidate, ...]:
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list):
        return ()

    role_map = role_map or _load_mode_role_map(mode)
    candidates: list[RoleCandidate] = []
    for item in roles_data:
        if not isinstance(item, dict):
            continue

        role_ids_data = item.get('role_ids') or []
        role_ids = tuple(str(role_id).strip() for role_id in role_ids_data if str(role_id).strip())
        allowed_role_ids = tuple(role_id for role_id in role_ids if role_id in role_map)
        if not allowed_role_ids:
            continue

        name = role_map[allowed_role_ids[0]]
        if not name or _is_excluded_role(name):
            continue

        images: list[str] = []
        for image_item in item.get('images') or []:
            if isinstance(image_item, dict):
                url = str(image_item.get('url') or '').strip()
            else:
                url = str(image_item or '').strip()
            if url.startswith(('http://', 'https://')):
                images.append(url)
        if images:
            candidates.append(RoleCandidate(name=name, role_ids=allowed_role_ids, images=tuple(images)))

    logger.debug(f'{LOG_PREFIX} 成功从图库解析候选角色 {len(candidates)} 名')
    return tuple(sorted(candidates, key=lambda role: role.name))


def _download_image_sync(url: str) -> bytes:
    try:
        return _http_get_with_retry(url, timeout=20)
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError('图库账号或密码不正确，图片返回 401。') from exc
        raise RuntimeError(f'下载图片失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'下载图片失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('下载图片超时。') from exc


def _gallery_image_cache_root() -> Path:
    return _custom_upload_data_root() / 'gallery_image_cache'


async def _download_image(url: str) -> bytes:
    """下载图库图片；按 URL 哈希落盘缓存，并合并相同 URL 的并发下载。"""
    cache_root = _gallery_image_cache_root()
    cached = await asyncio.to_thread(read_url_cache, cache_root, url)
    if cached is not None:
        logger.debug(f'{LOG_PREFIX} 命中图库图片磁盘缓存: {url}')
        return cached

    task = _IMAGE_INFLIGHT.get(url)
    if task is None:
        async def download() -> bytes:
            async with _IMAGE_DOWNLOAD_SEMAPHORE:
                second_cached = await asyncio.to_thread(read_url_cache, cache_root, url)
                if second_cached is not None:
                    return second_cached
                data = await asyncio.to_thread(_download_image_sync, url)
                await asyncio.to_thread(write_url_cache, cache_root, url, data)
                return data
        task = asyncio.create_task(download())
        _IMAGE_INFLIGHT[url] = task
    try:
        return await task
    finally:
        if task.done() and _IMAGE_INFLIGHT.get(url) is task:
            _IMAGE_INFLIGHT.pop(url, None)



async def _fallback_to_local_candidates(
    role_mode: str,
    custom_candidates: tuple[RoleCandidate, ...],
    fallback_error: str,
) -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    """图库接口失败后的兜底：优先回退本地图片目录，其次使用本地上传候选。"""
    local_candidates, local_error = await asyncio.to_thread(_load_local_candidates, role_mode)
    if local_candidates:
        logger.warning(f'{LOG_PREFIX} 图库接口不可用，已回退本地图片目录。')
        return local_candidates, None
    if custom_candidates:
        logger.warning(f'{LOG_PREFIX} 图库接口不可用，已回退本地上传候选。')
        return custom_candidates, None
    return None, local_error or fallback_error


async def _load_wuwa_candidates_uncached(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    source = _image_source()
    role_mode = _role_mode(mode)
    now = time.time()
    cache_key = f'{source}:{role_mode}'
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        logger.debug(f'{LOG_PREFIX} 使用缓存的候选角色列表: {cache_key}')
        return cached[1], None

    if source == 'local':
        candidates, error = await asyncio.to_thread(_load_local_candidates, role_mode)
        if error or not candidates:
            return None, error
        CANDIDATE_CACHE[cache_key] = (now, candidates)
        return candidates, None

    custom_candidates = await asyncio.to_thread(_load_custom_upload_candidates) if role_mode == 'wife' else ()
    try:
        role_map = _load_mode_role_map(role_mode)
        if not role_map and not custom_candidates:
            return None, f'没有找到鸣潮{_role_map_title(role_mode)}角色 ID 对照表。'
        candidates = ()
        if role_map:
            payload = await asyncio.to_thread(_fetch_gallery_payload_sync)
            candidates = _parse_role_candidates(payload, role_mode, role_map)
        candidates = _merge_role_candidates(candidates, custom_candidates)
    except RuntimeError as exc:
        logger.warning(f'{LOG_PREFIX} 读取图库接口失败: {exc}')
        candidates, error = await _fallback_to_local_candidates(role_mode, custom_candidates, str(exc))
        if error or not candidates:
            return None, error
        CANDIDATE_CACHE[cache_key] = (now, candidates)
        return candidates, None
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 读取图库接口异常: {exc}')
        candidates, error = await _fallback_to_local_candidates(role_mode, custom_candidates, '读取图库接口失败。')
        if error or not candidates:
            return None, error
        CANDIDATE_CACHE[cache_key] = (now, candidates)
        return candidates, None

    if not candidates:
        return None, '图库接口里没有找到可用的角色立绘。'

    CANDIDATE_CACHE[cache_key] = (now, candidates)
    return candidates, None


async def _load_wuwa_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
    cache_key = f'{_image_source()}:{role_mode}'
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1], None
    task = _CANDIDATE_INFLIGHT.get(cache_key)
    if task is None:
        generation = _CANDIDATE_CACHE_GENERATION
        async def load() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
            async with _CANDIDATE_LOAD_SEMAPHORE:
                result = await _load_wuwa_candidates_uncached(role_mode)
                if generation != _CANDIDATE_CACHE_GENERATION:
                    CANDIDATE_CACHE.pop(cache_key, None)
                return result
        task = asyncio.create_task(load())
        _CANDIDATE_INFLIGHT[cache_key] = task
    try:
        return await task
    finally:
        if task.done() and _CANDIDATE_INFLIGHT.get(cache_key) is task:
            _CANDIDATE_INFLIGHT.pop(cache_key, None)


async def _load_nte_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    cache_key = 'local:nte'
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1], None
    task = _CANDIDATE_INFLIGHT.get(cache_key)
    if task is None:
        async def load() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
            async with _CANDIDATE_LOAD_SEMAPHORE:
                candidates, error = await asyncio.to_thread(_load_nte_local_candidates)
                if error or not candidates:
                    return None, error
                CANDIDATE_CACHE[cache_key] = (time.time(), candidates)
                return candidates, None
        task = asyncio.create_task(load())
        _CANDIDATE_INFLIGHT[cache_key] = task
    try:
        return await task
    finally:
        if task.done() and _CANDIDATE_INFLIGHT.get(cache_key) is task:
            _CANDIDATE_INFLIGHT.pop(cache_key, None)


async def _load_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
    if role_mode == 'nte':
        return await _load_nte_candidates()

    candidates, error = await _load_wuwa_candidates(role_mode)
    if error or not candidates:
        return None, error
    return candidates, None


def _daily_rng(ev: Event, user_id: str | int | None = None, salt: str = '') -> random.Random:
    group_key = ev.group_id or 'direct'
    target_user_id = ev.user_id if user_id is None else user_id
    seed = f'{date.today().isoformat()}:{target_user_id}:{group_key}'
    if salt:
        seed = f'{seed}:{salt}'
    logger.debug(f'{LOG_PREFIX} 生成随机数种子: {seed}')
    return random.Random(seed)


def _is_master(ev: Event) -> bool:
    try:
        masters = core_config.get_config('masters')
    except Exception:
        masters = []
    return str(ev.user_id) in {str(master) for master in masters}


def _can_upload_images(ev: Event) -> bool:
    return _is_master(ev) or can_upload_images(
        ev.user_id,
        (),
        _cfg('DailyWifeImageUploadWhitelist'),
    )


def _can_specify_wife(ev: Event) -> bool:
    return _is_master(ev) or str(ev.user_id) in normalized_user_ids(
        _cfg('DailyWifeSpecifyWhitelist')
    )


def _event_rng(ev: Event) -> random.Random:
    return _daily_rng(ev)


def _wife_data_path() -> Path:
    return _custom_upload_data_root() / 'daily_wife_data.json'


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


def _display_name_from_mapping(data: Any, user_id: str | int | None = None) -> str:
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
        value = _display_name_from_mapping(getattr(ev, 'sender', {}) or {}, key)
        if value:
            return value
    return key


async def _load_group_display_names(ev: Event) -> dict[str, str]:
    if not ev.group_id:
        return {}

    try:
        users = await CoreUser.get_group_all_user(str(ev.group_id))
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 读取 GsCore 群成员缓存失败: {exc}')
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
    logger.debug(f'{LOG_PREFIX} 成功加载群 {ev.group_id} 的成员显示名称')
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
    return _custom_upload_data_root() / 'group_member_avatar_cache' / f'{safe_user_id}.jpg'


def _usable_cached_avatar(path: Path, check_ttl: bool = True) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if check_ttl and time.time() - path.stat().st_mtime > MEMBER_AVATAR_CACHE_SECONDS:
            logger.debug(f'{LOG_PREFIX} 缓存的头像已过期: {path}')
            return False
        return True
    except Exception:
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
    except Exception as exc:
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
        logger.warning(f'{LOG_PREFIX} 读取 GsCore 群成员缓存失败: {exc}')
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
        name = ''
        for field in ('user_name', 'nickname', 'name', 'username'):
            name = _valid_display_name(getattr(user, field, ''), user_id)
            if name:
                break
        name = name or user_id
        avatar = _valid_member_text(getattr(user, 'user_icon', ''))
        candidate = MemberCandidate(name=name, user_id=user_id, avatar=avatar)
        fallback[user_id] = candidate
        if preferred_bot_id and str(getattr(user, 'bot_id', '') or '').strip() == preferred_bot_id:
            exact[user_id] = candidate

    result = exact or fallback
    logger.debug(f'{LOG_PREFIX} 获取到 {len(result)} 个群友候选对象')
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
    member = await _pick_group_member(ev, pick_rng)
    if member is None:
        return None
    return WifeRecord.from_member(member)


# 每日记录读-改-写串行锁：所有「加载→校验→写入」的临界区都必须持有它，
# 替代旧 JSON 时代「写入段无 await」的单事件循环原子性，防止 0 点并发抽签互相覆盖。
_daily_data_lock = asyncio.Lock()
_DAILY_CONTEXT_LOCKS: dict[str, asyncio.Lock] = {}
_DAILY_CONTEXT_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_DAILY_CONTEXT_INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}


def _daily_context_lock(ev: Event) -> asyncio.Lock:
    """返回按 bot/group 分片的每日记录锁，避免不同群互相阻塞。"""
    key = f'{_today_key()}:{_context_key(ev)}'
    lock = _DAILY_CONTEXT_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DAILY_CONTEXT_LOCKS[key] = lock
    return lock


async def _load_daily_context(ev: Event) -> dict[str, Any]:
    """按上下文 hydrate 一次每日快照；数据库失败直接向调用方传播。"""
    day = _today_key()
    cache_key = f'{day}:{_context_key(ev)}'
    cached = _DAILY_CONTEXT_CACHE.get(cache_key)
    if cached is not None and cached[0] == day:
        return cached[1]
    task = _DAILY_CONTEXT_INFLIGHT.get(cache_key)
    if task is None:
        async def hydrate() -> dict[str, Any]:
            bot_id, _, group_id = _context_key(ev).partition(':')
            context = await DailyWifeRecord.get_context(day, bot_id, group_id or 'direct')
            data = {'days': {day: {_context_key(ev): context}}}
            result = _get_today_context(data, ev)
            _DAILY_CONTEXT_CACHE[cache_key] = (day, result)
            return result
        task = asyncio.create_task(hydrate())
        _DAILY_CONTEXT_INFLIGHT[cache_key] = task
    try:
        return await task
    finally:
        if task.done() and _DAILY_CONTEXT_INFLIGHT.get(cache_key) is task:
            _DAILY_CONTEXT_INFLIGHT.pop(cache_key, None)


async def _save_daily_record(
    ev: Event, bucket: str, user_key: str, value: Any
) -> None:
    """提交单条记录，成功后才更新内存快照。"""
    day = _today_key()
    bot_id, _, group_id = _context_key(ev).partition(':')
    await DailyWifeRecord.upsert_record(
        day, bot_id, group_id or 'direct', bucket, user_key, value
    )
    context = await _load_daily_context(ev)
    context.setdefault(bucket, {})[user_key] = value


async def _save_daily_context(ev: Event, context: dict[str, Any]) -> None:
    """定向提交一个上下文的快照，成功后更新内存快照。"""
    day = _today_key()
    bot_id, _, group_id = _context_key(ev).partition(':')
    cache_key = f'{day}:{_context_key(ev)}'
    snapshot = copy.deepcopy(context)
    # 先摘除旧缓存：写入失败时不会继续复用调用方已经修改的脏快照。
    _DAILY_CONTEXT_CACHE.pop(cache_key, None)
    await DailyWifeRecord.upsert_context(
        day, bot_id, group_id or 'direct', snapshot
    )
    _DAILY_CONTEXT_CACHE[cache_key] = (day, snapshot)

async def _load_wife_data() -> dict[str, Any]:
    """从数据库加载今天的全部记录，返回与旧 JSON 相同的 {'days': {today: {context: ...}}} 结构。"""
    today = _today_key()
    contexts = await DailyWifeRecord.load_day(today)
    return {'days': {today: contexts}}


async def _save_wife_data(data: dict[str, Any]) -> None:
    """把 {'days': {day: {context_key: context}}} 结构整体写回数据库（按 context 先删后插）。"""
    days = data.get('days') if isinstance(data, dict) else None
    if not isinstance(days, dict):
        return
    for day, contexts in days.items():
        if not isinstance(contexts, dict):
            continue
        for context_key, context in contexts.items():
            if not isinstance(context, dict):
                continue
            bot_id, _, group_id = str(context_key).partition(':')
            try:
                await DailyWifeRecord.save_context(day, bot_id, group_id or 'direct', context)
            except Exception as exc:
                logger.error(f'{LOG_PREFIX} 保存每日记录到数据库失败: {exc}')


def _get_today_context(data: dict[str, Any], ev: Event) -> dict[str, Any]:
    day = data.setdefault('days', {}).setdefault(_today_key(), {})
    context = day.setdefault(_context_key(ev), {})
    context.setdefault('wives', {})
    context.setdefault('husbands', {})
    context.setdefault('nte_wives', {})
    context.setdefault('pgr_wives', {})
    context.setdefault('lolis', {})
    context.setdefault('marry_members', {})
    context.setdefault('rob_attempts', {})
    context.setdefault('safe_wives', {})
    return context


async def _migrate_legacy_wife_data() -> int:
    """把旧版 daily_wife_data.json 导入数据库，导入成功后改名为 .migrated.bak 备份。

    幂等：旧文件改名后即消失，重复启动不会重复导入；
    即使备份失败残留旧文件，导入本身按 (day, context) 先删后插也不会产生重复行。
    只迁移最近几天的数据，更老的直接丢弃（见 models.LEGACY_MIGRATION_KEEP_DAYS）。
    """
    path = _wife_data_path()
    if not path.is_file():
        # 兼容更旧版本：插件目录下的数据文件先搬到 data 目录再迁移
        legacy = BASE_DIR / 'daily_wife_data.json'
        if legacy.is_file():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(legacy.read_bytes())
                logger.info(f'{LOG_PREFIX} 已迁移旧数据文件到 data 目录: {path}')
            except OSError as exc:
                logger.warning(f'{LOG_PREFIX} 迁移旧数据文件失败: {exc}')
                return 0
        else:
            return 0

    data = read_json_dict(path)
    if not data:
        logger.warning(f'{LOG_PREFIX} 旧数据文件为空或已损坏，跳过导入: {path}')
        imported = 0
    else:
        imported = await DailyWifeRecord.import_legacy_data(data)
    backup = path.with_name(f'{path.name}.migrated.bak')
    try:
        os.replace(path, backup)
        logger.info(f'{LOG_PREFIX} 旧数据文件已备份为 {backup.name}（导入 {imported} 条记录）')
    except OSError as exc:
        logger.warning(f'{LOG_PREFIX} 旧数据文件备份失败: {exc}')
    return imported


@on_core_start_before(priority=-70)
async def _migrate_daily_wife_data_on_startup() -> None:
    """插件启动钩子：在核心建表（priority=-90）之后执行旧 JSON 迁移。"""
    try:
        await _migrate_legacy_wife_data()
    except Exception as exc:
        logger.exception(f'{LOG_PREFIX} 旧每日记录迁移失败: {exc}')


async def _cache_maintenance_once() -> None:
    now = time.time()
    for key, (created, _) in list(CANDIDATE_CACHE.items()):
        if now - created >= CACHE_TTL_SECONDS:
            CANDIDATE_CACHE.pop(key, None)
    today = _today_key()
    for key in list(_DAILY_CONTEXT_CACHE):
        if not key.startswith(f'{today}:'):
            _DAILY_CONTEXT_CACHE.pop(key, None)
    await asyncio.to_thread(
        clear_expired_files,
        _gallery_image_cache_root(),
        30 * 24 * 60 * 60,
        CACHE_MAINTENANCE_FILE_LIMIT,
    )
    await asyncio.to_thread(
        clear_expired_files,
        _custom_upload_data_root() / 'group_member_avatar_cache',
        MEMBER_AVATAR_CACHE_SECONDS,
        CACHE_MAINTENANCE_FILE_LIMIT,
    )


async def _cache_maintenance_loop() -> None:
    await asyncio.sleep(17)
    while True:
        try:
            await _cache_maintenance_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f'{LOG_PREFIX} 缓存维护失败: {exc}')
        await asyncio.sleep(CACHE_MAINTENANCE_INTERVAL_SECONDS)


@on_core_start_before(priority=-60)
async def _start_cache_maintenance_on_startup() -> None:
    global _CACHE_MAINTENANCE_TASK
    if _CACHE_MAINTENANCE_TASK is None or _CACHE_MAINTENANCE_TASK.done():
        _CACHE_MAINTENANCE_TASK = asyncio.create_task(_cache_maintenance_loop())


def _daily_bucket_name(kind: str) -> str:
    return _daily_kind_metadata(kind).bucket


def _daily_item_title(kind: str) -> str:
    return _daily_kind_metadata(kind).title


def _daily_kind_metadata(kind: str) -> DailyKindMetadata:
    return daily_kind_metadata(kind)


DAILY_WIFE_KINDS = ('wife', 'nte', 'pgr')
ALL_DAILY_RECORD_KINDS = ('wife', 'nte', 'pgr', 'husband', 'loli')


async def _get_other_daily_wife_name(ev: Event, requested_kind: str) -> str | None:
    """返回用户今天在其他老婆池已有的角色名。"""
    if requested_kind not in DAILY_WIFE_KINDS:
        return None
    context = await _load_daily_context(ev)
    user_key = _user_key(ev)
    for kind in DAILY_WIFE_KINDS:
        if kind == requested_kind:
            continue
        raw = context[_daily_bucket_name(kind)].get(user_key)
        if not _has_active_wife(raw):
            continue
        name = str(raw.get('name') or '').strip()
        if name:
            return name
    return None


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
    if not image:
        return False
    # 图库模式下 image 是 http(s) URL，不是本地文件，发送时再下载校验
    if image.startswith(('http://', 'https://')):
        return True
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
    except Exception as exc:
        logger.error(f'{LOG_PREFIX} 解析 Record 字典异常: {exc}')
        return None
    if not record.name:
        return None
    if record.record_type == 'member':
        if record.image and not _is_valid_image_ref(record.image):
            logger.debug(f'{LOG_PREFIX} 群友头像路径已失效: {record.image}')
            return None
        return record
    if not _is_valid_image_ref(record.image):
        logger.debug(f'{LOG_PREFIX} 角色图片路径已失效: {record.image}')
        return None
    return record



# —— 老婆状态单一判定（四个流程统一调用，避免各处口径不一致）——
# 沿用现有标记位，不新增持久化字段、不迁移历史数据：
#   stolen_by / gifted_to / divorced ：记录已离手（被抢走 / 送出去 / 主动离婚），原主变“空”
#   stolen_from / gifted_from ：记录来源（抢来的 / 别人送的），即“二手”
def _wife_state(raw: Any) -> str:
    """返回记录持有状态：owned 正常持有 / lost_stolen 被抢走 / lost_gifted 送出去 / divorced 主动离婚。"""
    if not isinstance(raw, dict):
        return 'owned'
    if raw.get('divorced'):
        return 'divorced'
    if raw.get('stolen_by'):
        return 'lost_stolen'
    if raw.get('gifted_to'):
        return 'lost_gifted'
    return 'owned'


def _wife_origin(raw: Any) -> str:
    """返回老婆记录的来源：self 自己抽到 / robbed 抢来的 / gifted 别人送的。"""
    if not isinstance(raw, dict):
        return 'self'
    if raw.get('stolen_from'):
        return 'robbed'
    if raw.get('gifted_from'):
        return 'gifted'
    if raw.get('safe'):
        return 'safe'
    return 'self'


def _is_secondhand_wife(raw: Any) -> bool:
    """二手老婆 = 抢来的/别人送的/补偿抽的（到手即终结，不能再流转）。"""
    return _wife_origin(raw) in ('robbed', 'gifted', 'safe')


def _has_active_wife(raw: Any) -> bool:
    """是否仍持有一个有效（未离手）的老婆。"""
    return isinstance(raw, dict) and bool(raw.get('name')) and _wife_state(raw) == 'owned'


def _mark_all_daily_records_divorced(
    context: dict[str, Any],
    user_key: str,
    divorced_at: int,
) -> list[tuple[str, str]]:
    """一次性终止用户今天在全部模式中的婚姻记录。"""
    divorced: list[tuple[str, str]] = []
    for kind in ALL_DAILY_RECORD_KINDS:
        bucket = context[_daily_bucket_name(kind)]
        raw = bucket.get(user_key)
        if not isinstance(raw, dict) or not str(raw.get('name') or '').strip():
            continue
        if raw.get('divorced'):
            continue
        raw['divorced'] = True
        raw['divorced_at'] = divorced_at
        divorced.append((kind, str(raw['name'])))

    safe_record = context['safe_wives'].get(user_key)
    if (
        isinstance(safe_record, dict)
        and str(safe_record.get('name') or '').strip()
        and not safe_record.get('divorced')
    ):
        safe_record['divorced'] = True
        safe_record['divorced_at'] = divorced_at
        divorced.append(('safe_wife', str(safe_record['name'])))
    return divorced


def _normalise_target_user_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ''
    if isinstance(value, Message):
        value = value.data
    if isinstance(value, dict):
        for field in ('user_id', 'qq', 'openid', 'open_id', 'id', 'data'):
            user_id = _normalise_target_user_id(value.get(field))
            if user_id:
                return user_id
        return ''
    text = str(value).strip()
    if not text or text.lower() in {'none', 'true', 'false', 'all'}:
        return ''
    return text


def _target_user_id_from_text(text: str) -> str | None:
    text = str(text or '').strip()
    if not text:
        return None

    patterns = (
        r'\[CQ:at,[^\]]*qq=([0-9A-Za-z_-]{5,})',
        r'<at[^>]*(?:id|qq|user_id)=["\']?([0-9A-Za-z_-]{5,})',
        r'(?:qq=|qq:|QQ=|QQ:|@)\s*([0-9A-Za-z_-]{5,})',
        r'\b(\d{5,20})\b',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _iter_event_messages(ev: Event):
    for attr in ('content', 'message', 'original_message'):
        value = getattr(ev, attr, None)
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            for item in value:
                yield item
        else:
            yield value


def _get_event_target_user_id(ev: Event) -> str | None:
    for attr in ('at_list', 'at', 'target_id', 'target_user_id'):
        value = getattr(ev, attr, None)
        if value is not None:
            if isinstance(value, (list, tuple, set)):
                value = next(iter(value), None)

            user_id = _normalise_target_user_id(value)
            if user_id:
                if 'CQ:at' in user_id or '<at' in user_id:
                    parsed = _target_user_id_from_text(user_id)
                    if parsed:
                        return parsed
                    continue
                return user_id

    for item in _iter_event_messages(ev):
        item_type = getattr(item, 'type', None)
        if item_type in {'at', 'mention_user', 'mention'}:
            user_id = _normalise_target_user_id(getattr(item, 'data', None))
            if user_id:
                return user_id
        if isinstance(item, dict) and item.get('type') in {'at', 'mention_user', 'mention'}:
            user_id = _normalise_target_user_id(item.get('data'))
            if user_id:
                return user_id

    for attr in ('text', 'raw_text', 'raw_message', 'message', 'original_message'):
        t = getattr(ev, attr, None)
        if t is not None:
            user_id = _target_user_id_from_text(str(t))
            if user_id:
                return user_id

    return None



async def _get_existing_daily_record(ev: Event, user_id: str | int, kind: str = 'wife') -> WifeRecord | None:
    context = await _load_daily_context(ev)
    current = context[_daily_bucket_name(kind)].get(_user_key(ev, user_id))
    if isinstance(current, dict):
        return _record_from_dict(current)
    return None


async def _get_existing_daily_wife_record(ev: Event, user_id: str | int) -> WifeRecord | None:
    return await _get_existing_daily_record(ev, user_id, 'wife')



async def _find_local_role_image(role: RoleCandidate, kind: str) -> str | None:
    """图库图片下载失败时，尝试从本地图片目录为该角色找一张图。"""
    try:
        candidates, error = await asyncio.to_thread(_load_local_candidates, kind)
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 回退本地图片失败: {exc}')
        return None
    if error or not candidates:
        logger.warning(f'{LOG_PREFIX} 回退本地图片失败: {error}')
        return None
    role_ids = set(role.role_ids)
    for candidate in candidates:
        if candidate.name == role.name or (role_ids & set(candidate.role_ids)):
            if candidate.images:
                return candidate.images[0]
    return None


async def _send_role_image(
    bot: Bot,
    role: RoleCandidate,
    image_url: str,
    text: str | None = None,
    user_id: str | int | None = None,
    is_group: bool = True,
    kind: str = 'wife',
) -> None:
    is_gallery_image = image_url.startswith(('http://', 'https://'))
    if is_gallery_image:
        try:
            image: Any = await _download_image(image_url)
        except RuntimeError as exc:
            logger.warning(f'{LOG_PREFIX} 下载图库图片失败: {exc}')
            local_image = await _find_local_role_image(role, kind)
            if local_image is not None:
                logger.warning(f'{LOG_PREFIX} 已回退本地图片: {local_image}')
                image = await asyncio.to_thread(read_file_bytes_cached, Path(local_image))
            else:
                await _safe_send(bot, str(exc))
                return
    else:
        if not Path(image_url).is_file():
            logger.warning(f'{LOG_PREFIX} 本地图片不存在: {image_url}')
            await _safe_send(bot, '本地图片文件不存在，请检查 custom_role_pile 目录。')
            return
        # 本地图片按 (路径, mtime) 缓存字节，避免高峰期核心反复读盘转 base64
        image = await asyncio.to_thread(read_file_bytes_cached, Path(image_url))

    messages: list[Any] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    messages.append(MessageSegment.image(image))
    await _safe_send(bot, messages if len(messages) > 1 else messages[0])


async def _send_daily_result_image(
    bot: Bot,
    role: RoleCandidate,
    image: str,
    text: str,
    user_id: str,
    is_group: bool,
    kind: str,
) -> None:
    if kind != 'loli':
        await _send_role_image(bot, role, image, text, user_id, is_group, kind)
        return

    await _send_loli_result_image(bot, image, text, user_id, is_group)


async def _send_loli_result_image(
    bot: Bot,
    image: Any,
    text: str,
    user_id: str | int | None,
    is_group: bool,
) -> None:

    messages: list[Any] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    messages.append(text)
    if isinstance(image, str):
        if image.startswith(('http://', 'https://')):
            try:
                image_ref = await _download_image(image)
            except RuntimeError as exc:
                logger.warning(f'{LOG_PREFIX} 下载萝莉图片失败: {exc}')
                await _send_loli_text(bot, str(exc))
                return
        else:
            # 本地图片走 mtime 字节缓存，避免重复读盘
            image_ref = await asyncio.to_thread(read_file_bytes_cached, Path(image))
    else:
        image_ref = image
    messages.append(MessageSegment.image(image_ref))
    await _safe_send(bot, messages)


async def _send_local_image(
    bot: Bot,
    image_url: str,
    missing_hint: str,
    text: str | None = None,
    user_id: str | int | None = None,
    is_group: bool = True,
    kind: str = 'wife',
) -> None:
    messages: list[Any] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    if image_url:
        if not Path(image_url).is_file():
            logger.warning(f'{LOG_PREFIX} 本地图片不存在: {image_url}')
            if not text:
                await _safe_send(bot, missing_hint)
                return
        else:
            image_bytes = await asyncio.to_thread(read_file_bytes_cached, Path(image_url))
            messages.append(MessageSegment.image(image_bytes))

    if not messages:
        await _safe_send(bot, missing_hint)
        return
    await _safe_send(bot, messages if len(messages) > 1 else messages[0])
