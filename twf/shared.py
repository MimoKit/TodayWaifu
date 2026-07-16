
from __future__ import annotations

import asyncio
import binascii
import hashlib
import json
import random
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from importlib.util import find_spec
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from PIL import Image

from gsuid_core.bot import Bot
from gsuid_core.config import core_config
from gsuid_core.data_store import get_res_path
from gsuid_core.help.utils import register_help
from gsuid_core.logger import logger
from gsuid_core.models import Event, Message
from gsuid_core.segment import MessageSegment
from gsuid_core.sv import Plugins, SV
from gsuid_core.utils.database.models import CoreUser

from ..daily_wife_config import DailyWifeConfig
from .folder_gallery import scan_named_role_directories
from .kind_metadata import DAILY_KIND_METADATA, DailyKindMetadata, daily_kind_metadata
from .storage import atomic_write_json, read_json_dict

Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

# 控制台 SV 服务拆分：不要把所有功能塞进同一个 SV，便于单独开关。
# priority 数字越小越先执行；短前缀命令放到低优先级，避免抢到长命令。
help_sv = SV('今日老婆-帮助', priority=1)
custom_role_sv = SV('今日老婆-自定义老婆', pm=1, priority=2)
assign_wife_sv = SV('今日老婆-主人分配', pm=1, priority=2)
loli_manage_sv = SV('今日老婆-萝莉图库管理', pm=1, priority=2)
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
DEFAULT_GALLERY_API_URL = 'https://img.xlinxc.cn/api/xwuid/roles'
NTE_DETAIL_CDN_BASE = 'https://webstatic.tajiduo.com/bbs/yh-game-records-web-source/character/detail'
PGR_WIFE_DIR_NAME = 'pgr_wife'
CACHE_TTL_SECONDS = 300
MEMBER_AVATAR_CACHE_SECONDS = 7 * 24 * 60 * 60
LIST_FORWARD_THRESHOLD = 10
CUSTOM_ROLE_ID_START = 900001
UPLOAD_IMAGE_MAX_BYTES = 10 * 1024 * 1024
CUSTOM_ROLE_DELETE_CONFIRM_SECONDS = 120
LOLI_IMAGE_DIR_NAME = 'loli_images'
LOLICONAPP_API_URL = 'https://api.lolicon.app/setu/v2'
LOLICONAPP_TAGS = '萝莉|ロリ|loli|rori,-hololive'
NSFW_CHECK_MAX_ATTEMPTS = 30
LOLI_MOBILE_UA = (
    'Mozilla/5.0 (Linux; Android 13; Pixel 7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0.6367.82 Mobile Safari/537.36'
)
# --- 日志前缀 ---
LOG_PREFIX = '[鸣潮今日老婆]'
LOLI_DOWNLOAD_LOG_PREFIX = '[今日萝莉下载]'
REPLY_PREFIX = '[今日老婆]'
HUSBAND_REPLY_PREFIX = '[今日老公]'
LOLI_REPLY_PREFIX = '[今日萝莉]'
PGR_REPLY_PREFIX = '[今日战双老婆]'

__all__ = [
    'Any', 'BASE_DIR', 'Bot', 'CACHE_TTL_SECONDS', 'CANDIDATE_CACHE',
    'CUSTOM_ROLE_DELETE_CONFIRM_SECONDS', 'CUSTOM_ROLE_DELETE_PENDING',
    'CUSTOM_ROLE_ID_START', 'CoreUser', 'DAILY_KIND_METADATA', 'DEFAULT_GALLERY_API_URL',
    'DailyKindMetadata', 'DailyWifeConfig',
    'EXCLUDED_ROLE_KEYWORDS', 'EXCLUDED_ROLE_NAMES', 'Event', 'HELP_ICON_PATH',
    'HTTPError', 'IMAGE_EXTENSIONS', 'Image', 'LIST_FORWARD_THRESHOLD', 'LOG_PREFIX',
    'LOLI_DOWNLOAD_LOG_PREFIX', 'LOLI_IMAGE_DIR_NAME', 'LOLI_MOBILE_UA',
    'LOLICONAPP_API_URL', 'LOLICONAPP_TAGS', 'NSFW_CHECK_MAX_ATTEMPTS',
    'HUSBAND_REPLY_PREFIX', 'LOLI_REPLY_PREFIX', 'PGR_REPLY_PREFIX', 'MEMBER_AVATAR_CACHE_SECONDS',
    'MemberCandidate', 'Message', 'MessageSegment', 'Path', 'Plugins', 'REPLY_PREFIX',
    'ROLE_MAP_RE', 'Request', 'RoleCandidate', 'SV',
    'NTE_DETAIL_CDN_BASE', 'NTE_ROLE_MAP_PATH', 'UPLOAD_IMAGE_MAX_BYTES', 'URLError', 'WifeRecord',
    '_MALE_ROLE_NAMES_NORM', '_cfg', '_cfg_bool', '_cfg_probability',
    '_collect_role_candidates', '_configured_path', '_context_key',
    '_custom_upload_data_root', '_custom_upload_role_map_path',
    '_custom_upload_role_pile_root', '_daily_rng', '_download_avatar', '_download_image',
    '_download_image_sync', '_event_rng', '_fetch_gallery_payload_sync', '_filter_by_mode',
    '_gallery_api_url', '_gallery_mode_enabled',
    '_daily_bucket_name', '_daily_item_title', '_daily_kind_metadata', '_get_event_target_user_id',
    '_get_existing_daily_record', '_get_existing_daily_wife_record',
    '_get_other_daily_wife_name',
    '_get_today_context',
    '_has_active_wife', '_http_get', '_husband_available', '_husband_enabled',
    '_husband_unavailable_message', '_image_source', '_invalidate_candidate_cache',
    '_is_excluded_role', '_is_male_role', '_is_master', '_is_secondhand_wife',
    '_is_valid_image_ref', '_load_candidates', '_load_group_display_names',
    '_load_group_member_candidates', '_load_local_candidates', '_load_role_map',
    '_load_pgr_local_candidates', '_pgr_wife_root',
    '_load_wife_data', '_loli_image_root', '_marry_member_enabled',
    '_member_avatar_cache_path', '_member_feature_enabled', '_member_probability',
    '_nsfw_check_enabled', '_nsfw_check_image_ref', '_nsfw_record_passes',
    '_normalize_role_name', '_parse_role_candidates', '_pick_group_member',
    '_pick_nsfw_checked_role_record',
    '_get_reply_prefix', '_prefix_outgoing_message', '_qq_avatar_url', '_record_from_dict', '_record_to_dict',
    '_reply_text', '_request_headers', '_resolve_default_role_pile_root',
    '_resolve_member_avatar', '_resolve_member_candidate_avatar',
    '_resolve_nte_custom_panel_root', '_resolve_nte_default_panel_root',
    '_resolve_role_map_path', '_resolve_role_pile_root', '_role_images',
    '_roll_group_member_wife', '_save_wife_data', '_send_local_image', '_send_loli_text',
    '_safe_send', '_send_daily_result_image', '_send_loli_result_image',
    '_send_prefixed', '_send_role_image',
    '_today_key', '_usable_cached_avatar', '_user_display_name', '_user_key',
    '_valid_display_name', '_valid_member_text', '_wife_data_path', '_wife_origin',
    '_wife_state', '_with_loli_reply_prefix', '_writable_role_map_path', '_writable_role_pile_root',
    'asyncio', 'binascii', 'core_config', 'date', 'get_res_path',
    'assign_wife_sv', 'custom_role_sv', 'daily_husband_sv', 'daily_nte_wife_sv', 'daily_wife_sv',
    'divorce_sv', 'gift_sv', 'help_sv', 'husband_list_sv', 'loli_manage_sv', 'loli_sv',
    'marry_member_sv', 'pgr_wife_sv', 'rob_sv', 'wife_list_sv',
    'hashlib', 'json', 'logger', 'random', 're', 'register_help', 'shutil', 'time',
    'parse_qsl', 'urlencode', 'urlopen', 'urlparse', 'urlunparse',
]


def _with_loli_reply_prefix(text: str) -> str:
    return _reply_text(text, 'loli')


def _is_xwuid_group_activity_hook_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        isinstance(exc, AttributeError)
        and 'PluginHookManager' in message
        and 'group_activity_hooks' in message
    )


def _parse_send_options(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[bool, Any, bool]:
    options = dict(kwargs)
    at_sender = options.pop('at_sender', False)
    extra_metadata = options.pop('extra_metadata', None)
    wait_recall = options.pop('wait_recall', False)

    if len(args) > 3:
        raise TypeError(f'Bot.send expected at most 3 positional options, got {len(args)}')
    if len(args) >= 1:
        at_sender = args[0]
    if len(args) >= 2:
        extra_metadata = args[1]
    if len(args) >= 3:
        wait_recall = args[2]
    if options:
        unexpected = ', '.join(options)
        raise TypeError(f'Bot.send got unexpected keyword argument(s): {unexpected}')
    return bool(at_sender), extra_metadata, bool(wait_recall)


async def _target_send_without_bot_hooks(
    bot: Bot,
    message: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    at_sender, extra_metadata, wait_recall = _parse_send_options(args, kwargs)
    ev = bot.ev
    target_type = ev.user_type
    target_id = ev.user_id if ev.user_type == 'direct' else ev.group_id
    return await bot.bot.target_send(
        message,
        target_type,
        target_id,
        ev.real_bot_id,
        bot.bot_self_id,
        ev.msg_id,
        at_sender,
        ev.user_id,
        ev.group_id,
        ev.task_id,
        ev.task_event,
        extra_metadata=extra_metadata,
        wait_recall=wait_recall,
    )


QQ_OFFICIAL_BOT_IDS = {'qqgroup'}


def _official_image_gallery_url() -> str:
    return str(_cfg('DailyWifeOfficialImageGalleryUrl') or '').strip().rstrip('/')


def _official_image_gallery_token() -> str:
    return str(_cfg('DailyWifeOfficialImageGalleryToken') or '').strip()


def _is_official_qq_bot(bot: Bot) -> bool:
    platform = str(bot.ev.real_bot_id or bot.ev.bot_id or '')
    return platform.split(':', 1)[0].strip().lower() in QQ_OFFICIAL_BOT_IDS


def _is_at_message(item: Any) -> bool:
    return isinstance(item, Message) and item.type == 'at'


def _remove_private_mentions(message: Any) -> Any:
    items = message if isinstance(message, list) else [message]
    result: list[Any] = []
    skip_linebreak = False
    for item in items:
        if _is_at_message(item):
            skip_linebreak = True
            continue
        if skip_linebreak and isinstance(item, str) and item in ('\n', '\r\n'):
            skip_linebreak = False
            continue
        skip_linebreak = False
        result.append(item)

    if isinstance(message, list):
        return result
    return result[0] if result else ''


def _adapt_mentions_for_platform(bot: Bot, message: Any) -> Any:
    if bot.ev.user_type == 'direct':
        return _remove_private_mentions(message)
    return message


def _official_gallery_image_info(image: bytes) -> tuple[str, tuple[int, int]]:
    format_extensions = {
        'JPEG': 'jpg',
        'PNG': 'png',
        'GIF': 'gif',
        'WEBP': 'webp',
    }
    with Image.open(BytesIO(image)) as opened:
        image_format = str(opened.format or '').upper()
        size = opened.size
    extension = format_extensions.get(image_format)
    if extension is None:
        raise RuntimeError(f'官方机器人图库不支持 {image_format or "未知"} 图片格式')
    return extension, size


async def _official_gallery_image_bytes(image: Any) -> bytes:
    if isinstance(image, bytes):
        return image
    if isinstance(image, (bytearray, memoryview)):
        return bytes(image)
    if isinstance(image, Image.Image):
        output = BytesIO()
        image.save(output, format='PNG')
        return output.getvalue()
    if isinstance(image, Path):
        return await asyncio.to_thread(image.read_bytes)
    if isinstance(image, str):
        if image.startswith(('http://', 'https://')):
            return await _download_image(image)
        path = Path(image)
        if path.is_file():
            return await asyncio.to_thread(path.read_bytes)
    raise RuntimeError('无法读取需要上传到官方机器人图库的图片')


def _upload_official_gallery_image_sync(image: bytes) -> tuple[str, tuple[int, int]]:
    gallery_url = _official_image_gallery_url()
    if not gallery_url:
        raise RuntimeError('未配置官方机器人图库地址')

    extension, size = _official_gallery_image_info(image)
    filename = f'todaywaifu-{hashlib.sha256(image).hexdigest()[:32]}.{extension}'
    headers = {
        'User-Agent': 'TodayWaifu/1.0',
        'Content-Type': 'application/octet-stream',
        'X-Filename': filename,
    }
    token = _official_image_gallery_token()
    if token:
        headers['Authorization'] = f'Bearer {token}'

    request = Request(f'{gallery_url}/upload', data=image, headers=headers, method='POST')
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(f'官方机器人图库上传失败，HTTP {exc.code}') from exc
    except URLError as exc:
        raise RuntimeError(f'官方机器人图库上传失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('官方机器人图库上传超时') from exc

    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('官方机器人图库返回内容不是有效 JSON') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('官方机器人图库返回格式不正确')

    public_url = str(payload.get('url') or '').strip()
    parsed = urlparse(public_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise RuntimeError('官方机器人图库没有返回有效图片地址')
    return public_url, size


def _official_markdown_image_size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    if width <= 0 or height <= 0:
        return 260, 360
    scale = min(260 / width, 360 / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _build_official_qq_image_markdown(
    image_url: str,
    size: tuple[int, int],
    text: str | None,
    user_id: str | int | None,
    is_group: bool,
    kind: str,
) -> str:
    header_parts: list[str] = []
    if is_group and user_id is not None and _cfg_bool('DailyWifeAtUser', True):
        header_parts.append(f'<@{user_id}>')
    if text:
        if _cfg_bool('DailyWifeReplyPrefixEnabled', True):
            text = _reply_text(text, kind)
        header_parts.append(text)
    width, height = _official_markdown_image_size(size)
    parts = [' '.join(header_parts)] if header_parts else []
    parts.append(f'![image #{width}px #{height}px]({image_url})')
    return '\n\n'.join(parts)


async def _try_send_official_qq_image_markdown(
    bot: Bot,
    image: Any,
    text: str | None,
    user_id: str | int | None,
    is_group: bool,
    kind: str,
) -> bool:
    if not _is_official_qq_bot(bot) or not _official_image_gallery_url():
        return False
    try:
        image_bytes = await _official_gallery_image_bytes(image)
        public_url, size = await asyncio.to_thread(_upload_official_gallery_image_sync, image_bytes)
        markdown = _build_official_qq_image_markdown(
            public_url,
            size,
            text,
            user_id,
            is_group,
            kind,
        )
        await _safe_send(bot, MessageSegment.markdown(markdown))
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 官方机器人图库 Markdown 发送失败，降级为普通图片: {exc}')
        return False
    return True


async def _safe_send(bot: Bot, message: Any, *args: Any, **kwargs: Any) -> Any:
    message = _adapt_mentions_for_platform(bot, message)
    try:
        return await bot.send(message, *args, **kwargs)
    except AttributeError as exc:
        if not _is_xwuid_group_activity_hook_error(exc):
            raise
        logger.warning(f'{LOG_PREFIX} 检测到 XWUID BotHook 兼容问题，改用底层发送: {exc}')
        return await _target_send_without_bot_hooks(bot, message, *args, **kwargs)


async def _send_loli_text(bot: Bot, text: str, *args: Any, **kwargs: Any) -> Any:
    return await _send_prefixed(bot, text, *args, kind='loli', **kwargs)



def _get_reply_prefix(kind: str = 'wife') -> str:
    if kind == 'husband':
        return HUSBAND_REPLY_PREFIX
    if kind == 'loli':
        return LOLI_REPLY_PREFIX
    if kind == 'pgr':
        return PGR_REPLY_PREFIX
    return REPLY_PREFIX


def _reply_text(text: str, kind: str = 'wife') -> str:
    if not text.strip():
        return text
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    prefix = _get_reply_prefix(kind)
    if stripped.startswith(prefix):
        return text
    return f'{leading}{prefix}{stripped}'


def _prefix_outgoing_message(message: Any, kind: str = 'wife') -> Any:
    prefixed = False

    def prefix_node_item(item: Any) -> Any:
        if isinstance(item, str):
            return _reply_text(item, kind) if item.strip() else item
        if isinstance(item, Message):
            if item.type == 'text' and isinstance(item.data, str):
                return Message(type=item.type, data=_reply_text(item.data, kind)) if item.data.strip() else item
            if item.type == 'node' and isinstance(item.data, list):
                return Message(type=item.type, data=[prefix_node_item(part) for part in item.data])
        return item

    def prefix_item(item: Any) -> Any:
        nonlocal prefixed
        if isinstance(item, str):
            if not prefixed and item.strip():
                prefixed = True
                return _reply_text(item, kind)
            return item
        if isinstance(item, Message):
            if item.type == 'text' and isinstance(item.data, str):
                if not prefixed and item.data.strip():
                    prefixed = True
                    return Message(type=item.type, data=_reply_text(item.data, kind))
                return item
            if item.type == 'node' and isinstance(item.data, list):
                return Message(type=item.type, data=[prefix_node_item(part) for part in item.data])
        return item

    if isinstance(message, list):
        return [prefix_item(item) for item in message]
    return prefix_item(message)


async def _send_prefixed(
    bot: Bot,
    message: Any,
    *args: Any,
    kind: str = 'wife',
    **kwargs: Any,
) -> Any:
    if not _cfg_bool('DailyWifeReplyPrefixEnabled', True):
        return await _safe_send(bot, message, *args, **kwargs)
    return await _safe_send(bot, _prefix_outgoing_message(message, kind), *args, **kwargs)

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
# 按数据源分别缓存候选，避免切换数据源后误用旧缓存
CANDIDATE_CACHE: dict[str, tuple[float, tuple['RoleCandidate', ...]]] = {}
CUSTOM_ROLE_DELETE_PENDING: dict[str, dict[str, Any]] = {}


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


def _nsfw_check_enabled() -> bool:
    return _cfg_bool('DailyWifeNsfwCheckEnabled', False)


def _nsfw_check_raw_url() -> str:
    return str(_cfg('DailyWifeNsfwCheckUrl') or '').strip()


def _nsfw_redact_url(raw_url: str) -> str:
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return '<invalid-url>'

    query_pairs = [
        (key, '***' if key.lower() in {'token', 'x-token', 'x_token'} else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(query_pairs), fragment=''))


def _nsfw_check_endpoint() -> tuple[str, str]:
    raw_url = _nsfw_check_raw_url()
    if not raw_url:
        return '', ''

    parsed = urlparse(raw_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        logger.warning(f'{LOG_PREFIX} NSFW 检测地址无效: {_nsfw_redact_url(raw_url)}')
        return '', ''

    token = ''
    query_pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in {'token', 'x-token', 'x_token'} and not token:
            token = value
        else:
            query_pairs.append((key, value))

    for key, value in parse_qsl(parsed.fragment, keep_blank_values=True):
        if key.lower() in {'token', 'x-token', 'x_token'} and not token:
            token = value

    path = parsed.path.rstrip('/')
    if not path.endswith('/check/upload'):
        path = f'{path}/check/upload' if path else '/check/upload'

    cleaned = parsed._replace(
        path=path,
        query=urlencode(query_pairs),
        fragment='',
    )
    return urlunparse(cleaned), token


def _nsfw_multipart_body(image: bytes, filename: str = 'image.jpg') -> tuple[bytes, str]:
    boundary = f'----TodayWaifuNsfw{hashlib.md5(image).hexdigest()}'
    header = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        'Content-Type: image/jpeg\r\n\r\n'
    ).encode('utf-8')
    footer = f'\r\n--{boundary}--\r\n'.encode('utf-8')
    return header + image + footer, boundary


def _nsfw_response_passed(payload: dict[str, Any]) -> bool:
    action = str(payload.get('action') or '').strip().lower()
    if action:
        return action == 'allow'

    is_nsfw = payload.get('is_nsfw')
    if isinstance(is_nsfw, bool):
        return not is_nsfw

    result = str(payload.get('result') or payload.get('label') or '').strip().lower()
    if result in {'sfw', 'safe', 'normal', 'allow'}:
        return True
    if result in {'nsfw', 'porn', 'hentai', 'sexy', 'block', 'review'}:
        return False

    return True


def _nsfw_check_image_bytes_sync(image: bytes, image_ref: str) -> tuple[bool, dict[str, Any]]:
    endpoint, token = _nsfw_check_endpoint()
    if not endpoint:
        return True, {'reason': 'no_endpoint'}

    body, boundary = _nsfw_multipart_body(image)
    headers = {
        'User-Agent': 'TodayWaifu/1.0',
        'Content-Type': f'multipart/form-data; boundary={boundary}',
    }
    if token:
        headers['X-Token'] = token

    request = Request(endpoint, data=body, headers=headers, method='POST')
    try:
        with urlopen(request, timeout=20) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raise RuntimeError(f'NSFW 检测失败，HTTP {exc.code}') from exc
    except URLError as exc:
        raise RuntimeError(f'NSFW 检测失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('NSFW 检测超时') from exc

    try:
        payload = json.loads(raw.decode('utf-8'))
    except Exception as exc:
        raise RuntimeError('NSFW 检测服务返回内容不是有效 JSON') from exc

    if not isinstance(payload, dict):
        raise RuntimeError('NSFW 检测服务返回格式不正确')

    passed = _nsfw_response_passed(payload)
    logger.debug(
        f'{LOG_PREFIX} NSFW 检测完成: passed={passed} image={image_ref} '
        f'action={payload.get("action")} result={payload.get("result") or payload.get("label")}'
    )
    return passed, payload


async def _read_image_ref_bytes(image_ref: str) -> bytes | None:
    if image_ref.startswith(('http://', 'https://')):
        return await _download_image(image_ref)

    path = Path(image_ref)
    if not path.is_file():
        logger.warning(f'{LOG_PREFIX} NSFW 检测跳过不存在的本地图片: {image_ref}')
        return None
    return await asyncio.to_thread(path.read_bytes)


async def _nsfw_check_image_ref(image_ref: str) -> bool:
    if not _nsfw_check_enabled():
        return True

    try:
        image = await _read_image_ref_bytes(image_ref)
        if image is None:
            return True
        passed, payload = await asyncio.to_thread(_nsfw_check_image_bytes_sync, image, image_ref)
    except Exception as exc:
        # 检测服务异常时不打断今日老婆流程，避免服务抖动导致全功能不可用。
        logger.warning(f'{LOG_PREFIX} NSFW 检测异常，已按放行处理: {exc}')
        return True

    if not passed:
        logger.info(f'{LOG_PREFIX} NSFW 检测未通过，静默重抽: image={image_ref} payload={payload}')
    return passed


async def _nsfw_record_passes(record: 'WifeRecord') -> bool:
    if not _nsfw_check_enabled():
        return True
    if not record.image:
        return True
    return await _nsfw_check_image_ref(record.image)


async def _pick_nsfw_checked_role_record(
    candidates: tuple['RoleCandidate', ...],
    rng: random.Random,
    mode: str = 'wife',
) -> 'WifeRecord | None':
    if not candidates:
        return None

    if not _nsfw_check_enabled():
        role = rng.choice(candidates)
        return WifeRecord.from_role(role, rng.choice(role.images))

    seen: set[tuple[str, str]] = set()
    total_images = sum(len(role.images) for role in candidates)
    max_attempts = max(1, min(NSFW_CHECK_MAX_ATTEMPTS, total_images))
    for _ in range(max_attempts):
        role = rng.choice(candidates)
        image = rng.choice(role.images)
        key = (role.name, image)
        if key in seen:
            continue
        seen.add(key)

        record = WifeRecord.from_role(role, image)
        if await _nsfw_record_passes(record):
            return record

    logger.warning(f'{LOG_PREFIX} NSFW 检测启用后未找到通过检测的 {mode} 图片，尝试次数={len(seen)}')
    return None


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
    for line in path.read_text(encoding='utf-8').splitlines():
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


def _load_pgr_local_candidates() -> tuple[RoleCandidate, ...]:
    rows = scan_named_role_directories(_pgr_wife_root(), IMAGE_EXTENSIONS)
    return tuple(
        RoleCandidate(name=name, role_ids=(name,), images=images)
        for name, images in rows
    )


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

def _husband_enabled() -> bool:
    return _cfg_bool('DailyWifeHusbandEnabled', False)


def _gallery_mode_enabled() -> bool:
    return _image_source() == 'gallery'


def _husband_unavailable_message() -> str:
    return '今日老公功能当前已关闭。'


def _husband_available() -> bool:
    return _husband_enabled()


def _filter_by_mode(candidates: tuple['RoleCandidate', ...], mode: str) -> tuple['RoleCandidate', ...]:
    role_map = _load_mode_role_map(mode)
    role_mode = _role_mode(mode)
    if role_mode == 'wife':
        role_map.update(_load_custom_upload_role_map())
        if _cfg_bool('DailyWifeNteMixedEnabled', False):
            role_map.update(_load_mode_role_map('nte'))
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
    return {'User-Agent': 'TodayWaifu/1.0'}


def _http_get(url: str, *, timeout: int = 15) -> bytes:
    request = Request(url, headers=_request_headers())
    with urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _fetch_gallery_payload_sync() -> dict[str, Any]:
    api_url = _gallery_api_url()
    if not api_url:
        raise RuntimeError('未配置图库接口地址。')
    try:
        body = _http_get(api_url, timeout=15)
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
        return _http_get(url, timeout=20)
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError('图库账号或密码不正确，图片返回 401。') from exc
        raise RuntimeError(f'下载图片失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'下载图片失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('下载图片超时。') from exc


async def _download_image(url: str) -> bytes:
    return await asyncio.to_thread(_download_image_sync, url)



async def _load_wuwa_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
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
        if custom_candidates:
            CANDIDATE_CACHE[cache_key] = (now, custom_candidates)
            return custom_candidates, None
        return None, str(exc)
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 读取图库接口异常: {exc}')
        if custom_candidates:
            CANDIDATE_CACHE[cache_key] = (now, custom_candidates)
            return custom_candidates, None
        return None, '读取图库接口失败。'

    if not candidates:
        return None, '图库接口里没有找到可用的角色立绘。'

    CANDIDATE_CACHE[cache_key] = (now, candidates)
    return candidates, None


async def _load_nte_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    cache_key = 'local:nte'
    now = time.time()
    cached = CANDIDATE_CACHE.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1], None
    candidates, error = await asyncio.to_thread(_load_nte_local_candidates)
    if error or not candidates:
        return None, error
    CANDIDATE_CACHE[cache_key] = (now, candidates)
    return candidates, None


async def _load_candidates(mode: str = 'wife') -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    role_mode = _role_mode(mode)
    if role_mode == 'nte':
        return await _load_nte_candidates()

    candidates, error = await _load_wuwa_candidates(role_mode)
    if error or not candidates:
        return None, error
    if role_mode != 'wife' or not _cfg_bool('DailyWifeNteMixedEnabled', False):
        return candidates, None

    nte_candidates, nte_error = await _load_nte_candidates()
    if nte_error or not nte_candidates:
        logger.warning(f'{LOG_PREFIX} 混合抽取未加载到异环候选: {nte_error}')
    else:
        candidates = _merge_role_candidates(candidates, nte_candidates)

    if _cfg_bool('DailyWifePgrEnabled', True):
        pgr_candidates = _load_pgr_local_candidates()
        if not pgr_candidates:
            logger.warning(f'{LOG_PREFIX} 混合抽取未加载到战双候选，继续使用其他图库')
        else:
            candidates = _merge_role_candidates(candidates, pgr_candidates)
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


def _load_wife_data() -> dict[str, Any]:
    path = _wife_data_path()
    if not path.is_file():
        # 兼容旧版本：把插件目录下的数据文件一次性迁移到 data 目录
        legacy = BASE_DIR / 'daily_wife_data.json'
        if legacy.is_file():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(legacy.read_bytes())
                logger.info(f'{LOG_PREFIX} 已迁移旧数据文件到 data 目录: {path}')
            except OSError as exc:
                logger.warning(f'{LOG_PREFIX} 迁移旧数据文件失败: {exc}')
    if not path.is_file():
        logger.debug(f'{LOG_PREFIX} 数据文件不存在，将创建新数据')
        return {'days': {}}
    data = read_json_dict(path)
    if not data:
        logger.warning(f'{LOG_PREFIX} 读取数据文件失败或内容为空，将使用空数据: {path}')
        return {'days': {}}
    logger.debug(f'{LOG_PREFIX} 成功加载数据文件: {path.name}')
    data.setdefault('days', {})
    return data


def _save_wife_data(data: dict[str, Any]) -> None:
    try:
        atomic_write_json(_wife_data_path(), data)
        logger.debug(f'{LOG_PREFIX} 数据文件已保存')
    except OSError as exc:
        logger.error(f'{LOG_PREFIX} 保存数据文件失败: {exc}')


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


def _daily_bucket_name(kind: str) -> str:
    return _daily_kind_metadata(kind).bucket


def _daily_item_title(kind: str) -> str:
    return _daily_kind_metadata(kind).title


def _daily_kind_metadata(kind: str) -> DailyKindMetadata:
    return daily_kind_metadata(kind)


DAILY_WIFE_KINDS = ('wife', 'nte', 'pgr')


def _get_other_daily_wife_name(ev: Event, requested_kind: str) -> str | None:
    """返回用户今天在其他老婆池已有的角色名。"""
    if requested_kind not in DAILY_WIFE_KINDS:
        return None
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    user_key = _user_key(ev)
    for kind in DAILY_WIFE_KINDS:
        if kind == requested_kind:
            continue
        raw = context[_daily_bucket_name(kind)].get(user_key)
        if not isinstance(raw, dict):
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
    if raw.get('stolen_by'):
        return 'lost_stolen'
    if raw.get('gifted_to'):
        return 'lost_gifted'
    if raw.get('divorced'):
        return 'divorced'
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



def _get_existing_daily_record(ev: Event, user_id: str | int, kind: str = 'wife') -> WifeRecord | None:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = context[_daily_bucket_name(kind)].get(_user_key(ev, user_id))
    if isinstance(current, dict):
        return _record_from_dict(current)
    return None


def _get_existing_daily_wife_record(ev: Event, user_id: str | int) -> WifeRecord | None:
    return _get_existing_daily_record(ev, user_id, 'wife')



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
            await _send_prefixed(bot, str(exc), kind=kind)
            return
    else:
        if not Path(image_url).is_file():
            logger.warning(f'{LOG_PREFIX} 本地图片不存在: {image_url}')
            await _send_prefixed(bot, '本地图片文件不存在，请检查 custom_role_pile 目录。', kind=kind)
            return
        image = Path(image_url)

    if await _try_send_official_qq_image_markdown(bot, image, text, user_id, is_group, kind):
        return

    messages: list[Any] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    messages.append(MessageSegment.image(image))
    await _send_prefixed(bot, messages if len(messages) > 1 else messages[0], kind=kind)


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
        image_ref = image if image.startswith(('http://', 'https://')) else Path(image)
    else:
        image_ref = image
    if await _try_send_official_qq_image_markdown(bot, image_ref, text, user_id, is_group, 'loli'):
        return
    messages.append(MessageSegment.image(image_ref))
    await _send_prefixed(bot, messages, kind='loli')


async def _send_local_image(
    bot: Bot,
    image_url: str,
    missing_hint: str,
    text: str | None = None,
    user_id: str | int | None = None,
    is_group: bool = True,
    kind: str = 'wife',
) -> None:
    if image_url and Path(image_url).is_file():
        if await _try_send_official_qq_image_markdown(
            bot,
            Path(image_url),
            text,
            user_id,
            is_group,
            kind,
        ):
            return

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
                await _send_prefixed(bot, missing_hint, kind=kind)
                return
        else:
            messages.append(MessageSegment.image(Path(image_url)))

    if not messages:
        await _send_prefixed(bot, missing_hint, kind=kind)
        return
    await _send_prefixed(bot, messages if len(messages) > 1 else messages[0], kind=kind)
