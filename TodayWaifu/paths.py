"""TodayWaifu 的路径解析，以及日期/用户键与随机种子助手。"""
from __future__ import annotations

import random
from pathlib import Path
from datetime import date
from importlib.util import find_spec

import gsuid_core
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.data_store import get_res_path

from .constants import (
    BASE_DIR,
    LOG_PREFIX,
    NTE_ROLE_MAP_PATH,
    PGR_WIFE_DIR_NAME,
    WIFE_ROLE_MAP_PATH,
    LOLI_IMAGE_DIR_NAME,
    LEGACY_ROLE_MAP_PATH,
    HUSBAND_ROLE_MAP_PATH,
    _cfg,
    _daily_kind_metadata,
)
from .daily_repository import ContextKey


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

    core_root = Path(gsuid_core.__file__).resolve().parents[1]
    candidates.append(core_root / 'data' / 'XutheringWavesUID' / 'custom_role_pile')

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

    core_root = Path(gsuid_core.__file__).resolve().parents[1]
    candidates.append(core_root / 'data' / 'XutheringWavesUID' / 'resource' / 'role_pile')

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


def _pgr_wife_root() -> Path:
    configured = str(_cfg('DailyWifePgrGalleryPath') or '').strip().strip('"')
    if configured:
        return Path(configured).expanduser()
    return get_res_path('TodayWaifu') / PGR_WIFE_DIR_NAME


def _gallery_image_cache_root() -> Path:
    return _custom_upload_data_root() / 'gallery_image_cache'


def _daily_rng(ev: Event, user_id: str | int | None = None, salt: str = '') -> random.Random:
    group_key = ev.group_id or 'direct'
    target_user_id = ev.user_id if user_id is None else user_id
    seed = f'{date.today().isoformat()}:{target_user_id}:{group_key}'
    if salt:
        seed = f'{seed}:{salt}'
    logger.debug(f'{LOG_PREFIX} 生成随机数种子: {seed}')
    return random.Random(seed)


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


def _daily_context_key(ev: Event) -> ContextKey:
    bot_id, _, group_id = _context_key(ev).partition(':')
    return ContextKey(_today_key(), bot_id, group_id or 'direct')
