"""TodayWaifu 公共层：SV 定义、启动/维护钩子与对外再导出。

本模块把拆分后的各子模块按原名重新导出（见 `__all__`），业务模块按需显式
`from .shared import (...)` 取用，不再使用星号导入。
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import random
import shutil
import asyncio
import hashlib
import binascii
from pathlib import Path
from datetime import date
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy.exc import SQLAlchemyError

from gsuid_core.sv import SV, Plugins
from gsuid_core.bot import Bot
from gsuid_core.config import core_config
from gsuid_core.logger import logger
from gsuid_core.models import Event, Message
from gsuid_core.server import on_core_shutdown, on_core_start_before
from gsuid_core.segment import MessageSegment
from gsuid_core.data_store import get_res_path
from gsuid_core.help.utils import register_help
from gsuid_core.utils.database.models import CoreUser

from .paths import (
    _user_key,
    _daily_rng,
    _event_rng,
    _today_key,
    _context_key,
    _pgr_wife_root,
    _wife_data_path,
    _configured_path,
    _loli_image_root,
    _resolve_role_map_path,
    _resolve_role_pile_root,
    _writable_role_map_path,
    _custom_upload_data_root,
    _writable_role_pile_root,
    _gallery_image_cache_root,
    _custom_upload_role_map_path,
    _custom_upload_role_pile_root,
    _resolve_nte_custom_panel_root,
    _resolve_default_role_pile_root,
    _resolve_nte_default_panel_root,
)
from .roles import (
    _MALE_ROLE_NAMES_NORM,
    _role_images,
    _is_male_role,
    _loli_enabled,
    _load_role_map,
    _shota_enabled,
    _filter_by_mode,
    _husband_enabled,
    _is_excluded_role,
    _pick_role_record,
    _husband_available,
    _normalize_role_name,
    _gallery_mode_enabled,
    _load_local_candidates,
    _collect_role_candidates,
    _load_pgr_local_candidates,
)
from .state import (
    _MEMBER_CACHE,
    _SOURCE_CACHE,
    _IMAGE_INFLIGHT,
    CANDIDATE_CACHE,
    _CONTEXT_REGISTRY,
    _CANDIDATE_INFLIGHT,
    _DAILY_CONTEXT_CACHE,
    _PGR_CANDIDATE_CACHE,
    _MEMBER_AVATAR_INFLIGHT,
    _GROUP_DISPLAY_NAME_CACHE,
    CUSTOM_ROLE_DELETE_PENDING,
    _daily_data_lock,
)
from .domain import WifeRecord, RoleCandidate, MemberCandidate
from .models import DailyWifeRecord
from .gallery import (
    _http_get,
    _download_image,
    _gallery_api_url,
    _load_candidates,
    _request_headers,
    _download_image_sync,
    _http_get_with_retry,
    _parse_role_candidates,
    _load_pgr_wife_candidates,
    _fetch_gallery_payload_sync,
    _fetch_gallery_payload_from_url_sync,
)
from .members import (
    _qq_avatar_url,
    _download_avatar,
    _pick_group_member,
    _user_display_name,
    _valid_member_text,
    _member_probability,
    _valid_display_name,
    _marry_member_enabled,
    _usable_cached_avatar,
    _resolve_member_avatar,
    _member_feature_enabled,
    _roll_group_member_wife,
    _load_group_display_names,
    _member_avatar_cache_path,
    _load_group_member_candidates,
    _resolve_member_candidate_avatar,
)
from .senders import (
    _send_role_image,
    _send_local_image,
    _is_valid_image_ref,
    _send_loli_result_image,
    _send_daily_result_image,
    _send_shota_result_image,
)
from .storage import read_json_dict
from .targets import _get_event_target_user_id
from .delivery import _safe_send, _send_loli_text, _send_shota_text
from .payloads import PendingGift, DailyContext, GalleryPayload, RoleRecordValue, PendingCustomRoleDelete
from .constants import (
    BASE_DIR,
    LOG_PREFIX,
    ROLE_MAP_RE,
    HELP_ICON_PATH,
    LOLI_MOBILE_UA,
    LOLICONAPP_TAGS,
    IMAGE_EXTENSIONS,
    CACHE_TTL_SECONDS,
    LOLICONAPP_API_URL,
    ROLE_MAP_JSON_PATH,
    EXCLUDED_ROLE_NAMES,
    LOLI_IMAGE_DIR_NAME,
    NTE_DETAIL_CDN_BASE,
    CUSTOM_ROLE_ID_START,
    EXCLUDED_ROLE_KEYWORDS,
    LIST_FORWARD_THRESHOLD,
    UPLOAD_IMAGE_MAX_BYTES,
    DEFAULT_GALLERY_API_URL,
    LOLI_DOWNLOAD_LOG_PREFIX,
    MAX_IMAGE_RESPONSE_BYTES,
    MAX_GALLERY_RESPONSE_BYTES,
    MEMBER_AVATAR_CACHE_SECONDS,
    CACHE_MAINTENANCE_FILE_LIMIT,
    CACHE_MAINTENANCE_INTERVAL_SECONDS,
    CUSTOM_ROLE_DELETE_CONFIRM_SECONDS,
    _cfg,
    _cfg_bool,
    _image_source,
    _cfg_probability,
    _daily_item_title,
    _daily_bucket_name,
    _daily_kind_metadata,
)
from .file_cache import clear_expired_files, read_file_bytes_cached
from .daily_store import (
    _wife_state,
    _wife_origin,
    _load_wife_data,
    _record_to_dict,
    _save_wife_data,
    _has_active_wife,
    _record_from_dict,
    _get_today_context,
    _save_daily_record,
    _daily_context_lock,
    _is_secondhand_wife,
    _load_daily_context,
    _save_daily_context,
    _save_daily_records,
    _delete_daily_record,
    _get_existing_daily_record,
    _get_other_daily_wife_name,
    _get_existing_daily_wife_record,
    _mark_all_daily_records_divorced,
)
from .invalidation import _invalidate_candidate_cache
from .source_cache import AsyncSourceCache
from .kind_metadata import DAILY_KIND_METADATA, DailyKindMetadata
from .upload_access import can_upload_images, normalized_user_ids
from .role_map_store import loads_role_map, write_role_map, migrate_legacy_text_map
from .daily_repository import ContextKey, ContextRegistry
from ..daily_wife_config import DailyWifeConfig

Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

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
shota_sv = SV('今日老婆-今日正太', priority=3)
daily_wife_sv = SV('今日老婆-每日抽取', priority=10)
daily_husband_sv = SV('今日老婆-今日老公', priority=10)
daily_nte_wife_sv = SV('今日老婆-异环老婆', priority=10)
pgr_wife_sv = SV('今日老婆-战双老婆', priority=10)
daily_normal_wife_sv = SV('今日老婆-普通老婆', priority=10)


__all__ = [
    'BASE_DIR', 'Bot', 'CACHE_TTL_SECONDS', 'CANDIDATE_CACHE',
    'ContextKey', 'ContextRegistry',
    'AsyncSourceCache',
    'CUSTOM_ROLE_DELETE_CONFIRM_SECONDS', 'CUSTOM_ROLE_DELETE_PENDING',
    'CUSTOM_ROLE_ID_START', 'CoreUser', 'DAILY_KIND_METADATA', 'DEFAULT_GALLERY_API_URL',
    'DailyKindMetadata', 'DailyWifeConfig', 'DailyContext',
    'EXCLUDED_ROLE_KEYWORDS', 'EXCLUDED_ROLE_NAMES', 'Event', 'HELP_ICON_PATH',
    'HTTPError', 'IMAGE_EXTENSIONS', 'LIST_FORWARD_THRESHOLD', 'LOG_PREFIX',
    'LOLI_DOWNLOAD_LOG_PREFIX', 'LOLI_IMAGE_DIR_NAME', 'LOLI_MOBILE_UA',
    'LOLICONAPP_API_URL', 'LOLICONAPP_TAGS',
    'MemberCandidate', 'Message', 'MessageSegment', 'Path', 'Plugins',
    'ROLE_MAP_RE', 'Request', 'RoleCandidate', 'RoleRecordValue', 'SV',
    'PendingCustomRoleDelete', 'PendingGift', 'GalleryPayload',
    'NTE_DETAIL_CDN_BASE', 'ROLE_MAP_JSON_PATH', 'UPLOAD_IMAGE_MAX_BYTES', 'URLError', 'WifeRecord',
    'loads_role_map', 'write_role_map', 'migrate_legacy_text_map',
    'MAX_GALLERY_RESPONSE_BYTES', 'MAX_IMAGE_RESPONSE_BYTES',
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
    '_image_source', '_invalidate_candidate_cache', '_loli_enabled', '_shota_enabled',
    '_can_specify_wife', '_can_upload_images', '_is_excluded_role', '_is_male_role',
    '_is_master', '_is_secondhand_wife',
    '_is_valid_image_ref', '_load_candidates', '_load_group_display_names',
    '_load_group_member_candidates', '_load_local_candidates', '_load_role_map',
    '_load_pgr_local_candidates', '_load_pgr_wife_candidates', '_pgr_wife_root',
    '_load_wife_data', '_loli_image_root', '_marry_member_enabled',
    '_daily_context_lock',
    '_load_daily_context', '_save_daily_context',
    '_save_daily_records',
    '_save_daily_record', '_delete_daily_record',
    '_mark_all_daily_records_divorced', '_member_avatar_cache_path',
    '_member_feature_enabled', '_member_probability',
    '_normalize_role_name', '_parse_role_candidates', '_pick_group_member',
    '_pick_role_record',
    '_qq_avatar_url', '_record_from_dict', '_record_to_dict',
    '_request_headers', '_resolve_default_role_pile_root',
    '_resolve_member_avatar', '_resolve_member_candidate_avatar',
    '_resolve_nte_custom_panel_root', '_resolve_nte_default_panel_root',
    '_resolve_role_map_path', '_resolve_role_pile_root', '_role_images',
    '_roll_group_member_wife', '_save_wife_data', '_send_local_image', '_send_loli_text', '_send_shota_text',
    '_safe_send', '_send_daily_result_image', '_send_loli_result_image', '_send_shota_result_image',
    '_send_role_image',
    '_today_key', '_usable_cached_avatar', '_user_display_name', '_user_key',
    '_valid_display_name', '_valid_member_text', '_wife_data_path', '_wife_origin',
    '_wife_state', '_writable_role_map_path', '_writable_role_pile_root',
    'DailyWifeRecord', '_daily_data_lock', '_migrate_legacy_wife_data',
    'read_file_bytes_cached',
    'asyncio', 'binascii', 'core_config', 'date', 'get_res_path',
    'assign_wife_sv', 'custom_role_sv', 'daily_husband_sv', 'daily_normal_wife_sv',
    'daily_nte_wife_sv', 'daily_wife_sv',
    'divorce_sv', 'gift_sv', 'help_sv', 'husband_list_sv', 'image_upload_sv', 'loli_manage_sv', 'loli_sv', 'shota_sv',
    'marry_member_sv', 'pgr_wife_sv', 'rob_sv', 'specify_wife_sv', 'wife_list_sv',
    'hashlib', 'json', 'logger', 'random', 're', 'register_help', 'shutil', 'time',
    'urlopen', 'urlparse',
]


_CACHE_MAINTENANCE_TASK: asyncio.Task[None] | None = None


def _is_master(ev: Event) -> bool:
    masters = core_config.get_config('masters')
    if not isinstance(masters, list):
        return False
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
    except (OSError, SQLAlchemyError) as exc:
        logger.exception(f'{LOG_PREFIX} 旧每日记录迁移失败: {exc}')


def _prune_daily_context_state() -> None:
    today = _today_key()
    _CONTEXT_REGISTRY.prune(today)
    for key in list(_DAILY_CONTEXT_CACHE):
        if not key.startswith(f'{today}:'):
            _DAILY_CONTEXT_CACHE.pop(key, None)


def _prune_pending_state() -> None:
    now = time.time()
    for key, value in tuple(CUSTOM_ROLE_DELETE_PENDING.items()):
        try:
            created_at = float(value.get('created_at') or 0)
        except (TypeError, ValueError):
            created_at = 0
        if now - created_at > CUSTOM_ROLE_DELETE_CONFIRM_SECONDS:
            CUSTOM_ROLE_DELETE_PENDING.pop(key, None)
    # gift / normal_wife 的待处理状态与缓存由各自模块持有，仅在已加载时清理。
    if f'{__package__}.gift' in sys.modules:
        from . import gift as gift_module

        gift_module.clear_expired_pending_gifts()
    if f'{__package__}.normal_wife' in sys.modules:
        from . import normal_wife

        normal_wife.prune_normal_gallery_cache()


async def _cache_maintenance_once() -> None:
    now = time.time()
    for key, (created, _) in list(CANDIDATE_CACHE.items()):
        if now - created >= CACHE_TTL_SECONDS:
            CANDIDATE_CACHE.pop(key, None)
    _prune_daily_context_state()
    _prune_pending_state()
    _SOURCE_CACHE.prune()
    _PGR_CANDIDATE_CACHE.prune()
    _MEMBER_CACHE.prune()
    _GROUP_DISPLAY_NAME_CACHE.prune()
    if f'{__package__}.normal_wife' in sys.modules:
        from . import normal_wife

        normal_wife.prune_normal_gallery_cache()
    for registry in (_CONTEXT_REGISTRY,):
        registry.prune(_today_key())
    for mapping in (_CANDIDATE_INFLIGHT, _IMAGE_INFLIGHT, _MEMBER_AVATAR_INFLIGHT):
        for key, task in tuple(mapping.items()):
            if task.done() or task.cancelled():
                mapping.pop(key, None)
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
        except (OSError, SQLAlchemyError) as exc:
            logger.warning(f'{LOG_PREFIX} 缓存维护失败: {exc}')
        await asyncio.sleep(CACHE_MAINTENANCE_INTERVAL_SECONDS)


@on_core_start_before(priority=-60)
async def _start_cache_maintenance_on_startup() -> None:
    global _CACHE_MAINTENANCE_TASK
    if _CACHE_MAINTENANCE_TASK is None or _CACHE_MAINTENANCE_TASK.done():
        _CACHE_MAINTENANCE_TASK = asyncio.create_task(_cache_maintenance_loop())


@on_core_shutdown
async def _stop_cache_maintenance_on_shutdown() -> None:
    global _CACHE_MAINTENANCE_TASK
    task = _CACHE_MAINTENANCE_TASK
    _CACHE_MAINTENANCE_TASK = None
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
