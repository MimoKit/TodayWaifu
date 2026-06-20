
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import html
import json
import random
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
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

Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

sv = SV('鸣潮今日老婆')
upload_sv = SV('鸣潮今日老婆上传', pm=1)
BASE_DIR = Path(__file__).parent.parent
HELP_IMAGE_PATH = BASE_DIR / 'help.png'
HELP_ICON_PATH = BASE_DIR / 'ICON.png'
DEFAULT_GALLERY_API_URL = 'https://img.xlinxc.cn/api/xwuid/roles'
CACHE_TTL_SECONDS = 300
MEMBER_AVATAR_CACHE_SECONDS = 7 * 24 * 60 * 60
LIST_FORWARD_THRESHOLD = 10
CUSTOM_ROLE_ID_START = 900001
UPLOAD_IMAGE_MAX_BYTES = 10 * 1024 * 1024
CUSTOM_ROLE_DELETE_CONFIRM_SECONDS = 120
LOLI_IMAGE_REPO_ZIP_URL = 'https://github.com/nnlmc/waifu-gallery/raw/main/img.zip'
# 备用下载源（自建镜像，定时同步 GitHub）。直连 GitHub 测速慢/失败时自动切换。
LOLI_IMAGE_BACKUP_ZIP_URL = 'http://luoli.dnymc.top/img.zip'
# 直连 GitHub 测速：在该秒数内拿到响应头则走直连，否则切备用
LOLI_DOWNLOAD_PROBE_TIMEOUT = 6
LOLI_IMAGE_ZIP_MAX_BYTES = 200 * 1024 * 1024
LOLI_IMAGE_DIR_NAME = 'loli_images'
GITHUB_UPDATE_API_URL = 'https://api.github.com/repos/nnlmc/TodayWaifu/commits?per_page=30'
GITHUB_UPDATE_RENDER_WIDTH = 860

# --- 日志前缀 ---
LOG_PREFIX = '[鸣潮今日老婆]'
LOLI_DOWNLOAD_LOG_PREFIX = '[今日萝莉下载]'
REPLY_PREFIX = '[今日老婆]'
LOLI_REPLY_PREFIX = '[今日萝莉]'
SHOTA_REPLY_PREFIX = '[今日正太]'
YUJIE_REPLY_PREFIX = '[今日御姐]'

__all__ = [
    'Any', 'BASE_DIR', 'Bot', 'CACHE_TTL_SECONDS', 'CANDIDATE_CACHE',
    'CUSTOM_ROLE_DELETE_CONFIRM_SECONDS', 'CUSTOM_ROLE_DELETE_PENDING',
    'CUSTOM_ROLE_ID_START', 'CoreUser', 'DEFAULT_GALLERY_API_URL', 'DailyWifeConfig',
    'EXCLUDED_ROLE_KEYWORDS', 'EXCLUDED_ROLE_NAMES', 'Event', 'GITHUB_UPDATE_API_URL',
    'GITHUB_UPDATE_RENDER_WIDTH', 'GitHubUpdateRecord', 'HELP_ICON_PATH', 'HELP_IMAGE_PATH',
    'HTTPError', 'IMAGE_EXTENSIONS', 'Image', 'LIST_FORWARD_THRESHOLD', 'LOG_PREFIX',
    'LOLI_DOWNLOAD_LOG_PREFIX', 'LOLI_DOWNLOAD_PROBE_TIMEOUT', 'LOLI_IMAGE_BACKUP_ZIP_URL',
    'LOLI_IMAGE_DIR_NAME', 'LOLI_IMAGE_REPO_ZIP_URL', 'LOLI_IMAGE_ZIP_MAX_BYTES',
    'LOLI_REPLY_PREFIX', 'LoliImageDownloadResult', 'MEMBER_AVATAR_CACHE_SECONDS',
    'MemberCandidate', 'Message', 'MessageSegment', 'Path', 'Plugins', 'REPLY_PREFIX',
    'ROLE_MAP_RE', 'Request', 'RoleCandidate', 'SHOTA_REPLY_PREFIX', 'SV',
    'UPLOAD_IMAGE_MAX_BYTES', 'URLError', 'WifeRecord', 'YUJIE_REPLY_PREFIX',
    '_MALE_ROLE_NAMES_NORM', '_cfg', '_cfg_bool', '_cfg_probability',
    '_collect_role_candidates', '_configured_path', '_context_key',
    '_custom_upload_data_root', '_custom_upload_role_map_path',
    '_custom_upload_role_pile_root', '_daily_rng', '_download_avatar', '_download_image',
    '_download_image_sync', '_event_rng', '_fetch_gallery_payload_sync', '_filter_by_mode',
    '_gallery_api_url', '_gallery_auth_header', '_gallery_mode_enabled',
    '_get_event_target_user_id', '_get_existing_daily_wife_record', '_get_recorded_owner',
    '_get_today_context',
    '_has_active_wife', '_http_get', '_husband_available', '_husband_enabled',
    '_husband_unavailable_message', '_image_source', '_invalidate_candidate_cache',
    '_is_excluded_role', '_is_male_role', '_is_master', '_is_secondhand_wife',
    '_is_valid_image_ref', '_load_candidates', '_load_group_display_names',
    '_load_group_member_candidates', '_load_local_candidates', '_load_role_map',
    '_load_wife_data', '_loli_image_root', '_marry_member_enabled', '_marry_owner_enabled',
    '_member_avatar_cache_path', '_member_feature_enabled', '_member_probability',
    '_normalize_role_name', '_parse_role_candidates', '_pick_group_member',
    '_prefix_outgoing_message', '_qq_avatar_url', '_record_from_dict', '_record_to_dict',
    '_reply_text', '_request_headers', '_resolve_default_role_pile_root',
    '_resolve_member_avatar', '_resolve_member_candidate_avatar',
    '_resolve_role_map_path', '_resolve_role_pile_root', '_role_images',
    '_roll_group_member_wife', '_save_wife_data', '_send_local_image', '_send_loli_text',
    '_send_prefixed', '_send_role_image', '_send_shota_text', '_send_yujie_text',
    '_today_key', '_usable_cached_avatar', '_user_display_name', '_user_key',
    '_valid_display_name', '_valid_member_text', '_wife_data_path', '_wife_origin',
    '_wife_state', '_with_loli_reply_prefix', '_with_shota_reply_prefix',
    '_with_yujie_reply_prefix', '_writable_role_map_path', '_writable_role_pile_root',
    'asyncio', 'base64', 'binascii', 'core_config', 'date', 'get_res_path', 'hashlib',
    'html', 'json', 'logger', 'random', 're', 'register_help', 'shutil', 'sv', 'time',
    'upload_sv', 'urlopen', 'urlparse', 'zipfile',
]


def _with_loli_reply_prefix(text: str) -> str:
    if not text.strip():
        return text
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    if stripped.startswith(LOLI_REPLY_PREFIX):
        return text
    return f'{leading}{LOLI_REPLY_PREFIX}{stripped}'


async def _send_loli_text(bot: Bot, text: str, *args: Any, **kwargs: Any) -> Any:
    return await bot.send(_with_loli_reply_prefix(text), *args, **kwargs)


def _with_shota_reply_prefix(text: str) -> str:
    if not text.strip():
        return text
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    if stripped.startswith(SHOTA_REPLY_PREFIX):
        return text
    return f'{leading}{SHOTA_REPLY_PREFIX}{stripped}'


async def _send_shota_text(bot: Bot, text: str, *args: Any, **kwargs: Any) -> Any:
    return await bot.send(_with_shota_reply_prefix(text), *args, **kwargs)


def _with_yujie_reply_prefix(text: str) -> str:
    if not text.strip():
        return text
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    if stripped.startswith(YUJIE_REPLY_PREFIX):
        return text
    return f'{leading}{YUJIE_REPLY_PREFIX}{stripped}'


async def _send_yujie_text(bot: Bot, text: str, *args: Any, **kwargs: Any) -> Any:
    return await bot.send(_with_yujie_reply_prefix(text), *args, **kwargs)


def _reply_text(text: str) -> str:
    if not text.strip():
        return text
    stripped = text.lstrip()
    leading = text[: len(text) - len(stripped)]
    if stripped.startswith(REPLY_PREFIX):
        return text
    return f'{leading}{REPLY_PREFIX}{stripped}'


def _prefix_outgoing_message(message: Any) -> Any:
    prefixed = False

    def prefix_node_item(item: Any) -> Any:
        if isinstance(item, str):
            return _reply_text(item) if item.strip() else item
        if isinstance(item, Message):
            if item.type == 'text' and isinstance(item.data, str):
                return Message(type=item.type, data=_reply_text(item.data)) if item.data.strip() else item
            if item.type == 'node' and isinstance(item.data, list):
                return Message(type=item.type, data=[prefix_node_item(part) for part in item.data])
        return item

    def prefix_item(item: Any) -> Any:
        nonlocal prefixed
        if isinstance(item, str):
            if not prefixed and item.strip():
                prefixed = True
                return _reply_text(item)
            return item
        if isinstance(item, Message):
            if item.type == 'text' and isinstance(item.data, str):
                if not prefixed and item.data.strip():
                    prefixed = True
                    return Message(type=item.type, data=_reply_text(item.data))
                return item
            if item.type == 'node' and isinstance(item.data, list):
                return Message(type=item.type, data=[prefix_node_item(part) for part in item.data])
        return item

    if isinstance(message, list):
        return [prefix_item(item) for item in message]
    return prefix_item(message)


async def _send_prefixed(bot: Bot, message: Any, *args: Any, **kwargs: Any) -> Any:
    if not _cfg_bool('DailyWifeReplyPrefixEnabled', True):
        return await bot.send(message, *args, **kwargs)
    return await bot.send(_prefix_outgoing_message(message), *args, **kwargs)

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


@dataclass(frozen=True)
class LoliImageDownloadResult:
    saved: int
    duplicated: int
    skipped: int


@dataclass(frozen=True)
class GitHubUpdateRecord:
    message: str
    sha: str
    author: str
    updated_at: str
    url: str


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
            logger.debug(f'{LOG_PREFIX} 成功定位角色对照表文件: {path}')
            return path
    logger.warning(f'{LOG_PREFIX} 未能找到角色对照表文件')
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
            for ext in IMAGE_EXTENSIONS:
                fallback_img = default_pile_root / f'role_pile_{role_id}{ext}'
                if fallback_img.is_file():
                    images.append(str(fallback_img))
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
    logger.info(f'{LOG_PREFIX} 成功归并候选角色 {len(candidates)} 名')
    return tuple(sorted(candidates, key=lambda item: item.name))


def _load_local_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    logger.debug(f'{LOG_PREFIX} 开始从本地加载角色候选列表...')
    role_map_path = _resolve_role_map_path()
    if role_map_path is None:
        return None, '没有找到鸣潮角色 ID 对照表。'

    pile_root = _resolve_role_pile_root()
    default_pile_root = _resolve_default_role_pile_root()
    upload_pile_root = _custom_upload_role_pile_root()
    if pile_root is None and default_pile_root is None and not upload_pile_root.is_dir():
        return None, '没有找到 custom_role_pile 或默认 role_pile 图片目录。'

    if pile_root is None:
        pile_root = Path("dummy_non_existent_path")

    try:
        role_map = _load_role_map(role_map_path)
        upload_role_map_path = _custom_upload_role_map_path()
        if upload_role_map_path.is_file():
            role_map.update(_load_role_map(upload_role_map_path))
        candidates = _collect_role_candidates(role_map, pile_root, default_pile_root, upload_pile_root)
    except Exception as exc:
        logger.exception(f'{LOG_PREFIX} 读取本地图片目录失败: {exc}')
        return None, '读取本地图片目录失败。'

    if not candidates:
        logger.warning(f'{LOG_PREFIX} 扫描图片目录完成，但未找到可用角色图片')
        return None, '图片目录里没有找到可用角色图片。'
    return candidates, None


def _normalize_role_name(name: str) -> str:
    return name.replace('・', '·').replace('•', '·').strip()

_MALE_ROLE_NAMES_NORM = {_normalize_role_name(n) for n in EXCLUDED_ROLE_NAMES}

def _is_male_role(name: str) -> bool:
    return _normalize_role_name(name) in _MALE_ROLE_NAMES_NORM

def _is_excluded_role(name: str) -> bool:
    return any(keyword in name for keyword in EXCLUDED_ROLE_KEYWORDS)

def _husband_enabled() -> bool:
    return _cfg_bool('DailyWifeHusbandEnabled', False)


def _gallery_mode_enabled() -> bool:
    return _image_source() == 'gallery'


def _husband_unavailable_message() -> str:
    if _gallery_mode_enabled():
        return '图库模式下禁止使用今日老公'
    return '今日老公功能当前已关闭。'


def _husband_available() -> bool:
    return _husband_enabled() and not _gallery_mode_enabled()


def _filter_by_mode(candidates: tuple['RoleCandidate', ...], mode: str) -> tuple['RoleCandidate', ...]:
    if mode == 'husband':
        return tuple(role for role in candidates if _is_male_role(role.name))
    return tuple(role for role in candidates if not _is_male_role(role.name))


def _gallery_api_url() -> str:
    return str(_cfg('DailyWifeGalleryApiUrl') or DEFAULT_GALLERY_API_URL).strip()


def _gallery_auth_header() -> str | None:
    username = str(_cfg('DailyWifeGalleryUsername') or '').strip()
    password = str(_cfg('DailyWifeGalleryPassword') or '').strip()
    if not username or not password:
        return None
    token = base64.b64encode(f'{username}:{password}'.encode('utf-8')).decode('ascii')
    return f'Basic {token}'


def _request_headers() -> dict[str, str]:
    headers = {'User-Agent': 'TodayWaifu/1.0'}
    auth = _gallery_auth_header()
    if auth:
        headers['Authorization'] = auth
    return headers


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


def _parse_role_candidates(payload: dict[str, Any]) -> tuple[RoleCandidate, ...]:
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list):
        return ()

    candidates: list[RoleCandidate] = []
    for item in roles_data:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '').strip()
        if not name or _is_excluded_role(name):
            continue

        role_ids_data = item.get('role_ids') or []
        role_ids = tuple(str(role_id) for role_id in role_ids_data if str(role_id).strip())

        images: list[str] = []
        for image_item in item.get('images') or []:
            if isinstance(image_item, dict):
                url = str(image_item.get('url') or '').strip()
            else:
                url = str(image_item or '').strip()
            if url.startswith(('http://', 'https://')):
                images.append(url)
        if images:
            candidates.append(RoleCandidate(name=name, role_ids=role_ids, images=tuple(images)))

    logger.info(f'{LOG_PREFIX} 成功从图库解析候选角色 {len(candidates)} 名')
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



async def _load_candidates() -> tuple[tuple[RoleCandidate, ...] | None, str | None]:
    source = _image_source()
    now = time.time()
    cached = CANDIDATE_CACHE.get(source)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        logger.debug(f'{LOG_PREFIX} 使用缓存的候选角色列表: {source}')
        return cached[1], None

    if source == 'local':
        candidates, error = await asyncio.to_thread(_load_local_candidates)
        if error or not candidates:
            return None, error
        CANDIDATE_CACHE[source] = (now, candidates)
        return candidates, None

    try:
        payload = await asyncio.to_thread(_fetch_gallery_payload_sync)
        candidates = _parse_role_candidates(payload)
    except RuntimeError as exc:
        logger.warning(f'{LOG_PREFIX} 读取图库接口失败: {exc}')
        return None, str(exc)
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 读取图库接口异常: {exc}')
        return None, '读取图库接口失败。'

    if not candidates:
        return None, '图库接口里没有找到可用的角色立绘。'

    CANDIDATE_CACHE[source] = (now, candidates)
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
        logger.info(f'{LOG_PREFIX} 头像下载完成: {path}')
        return True
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 下载群友头像失败: {url} -> {exc}')
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
        name = _valid_display_name(getattr(user, 'user_name', ''), user_id) or user_id
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
            logger.info(f'{LOG_PREFIX} 成功挑选群友: {resolved.name} ({resolved.user_id})')
            return resolved
    logger.warning(f'{LOG_PREFIX} 未能成功获取任一群友的有效头像')
    return None


# GsCore 没有持久化"本群群主是谁"的数据，user_pm 只是当前这条消息发送者的权限等级
# （0 主人 / 1 超管 / 2 群主 / 3 管理员 / 6 普通用户）。这里仿照 CoreUser 的"路过记一笔"
# 做法：群主自己发过消息时顺手记下来，没发过消息就识别不到。
GROUP_OWNER_PERMISSION_LEVEL = 2


def _owner_cache_path() -> Path:
    return _custom_upload_data_root() / 'group_owner_cache.json'


def _load_owner_cache() -> dict[str, Any]:
    path = _owner_cache_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 读取群主缓存失败: {exc}')
        return {}
    return data if isinstance(data, dict) else {}


def _save_owner_cache(data: dict[str, Any]) -> None:
    try:
        path = _owner_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        logger.error(f'{LOG_PREFIX} 保存群主缓存失败: {exc}')


def _owner_group_key(ev: Event) -> str:
    return f'{ev.bot_id}:{ev.group_id}'


def _record_owner_sighting(ev: Event) -> None:
    if not ev.group_id:
        return
    try:
        user_pm = int(getattr(ev, 'user_pm', 6) or 6)
    except (TypeError, ValueError):
        return
    if user_pm > GROUP_OWNER_PERMISSION_LEVEL:
        return

    user_id = str(ev.user_id)
    name = _user_display_name(ev)
    key = _owner_group_key(ev)
    cache = _load_owner_cache()
    existing = cache.get(key)
    if isinstance(existing, dict) and existing.get('user_id') == user_id and existing.get('user_name') == name:
        return
    cache[key] = {'user_id': user_id, 'user_name': name, 'updated_at': int(time.time())}
    _save_owner_cache(cache)
    logger.debug(f'{LOG_PREFIX} 记录到群 {ev.group_id} 的群主: {name} ({user_id})')


def _get_recorded_owner(ev: Event) -> MemberCandidate | None:
    if not ev.group_id:
        return None
    cache = _load_owner_cache()
    entry = cache.get(_owner_group_key(ev))
    if not isinstance(entry, dict):
        return None
    user_id = _valid_member_text(entry.get('user_id'))
    if not user_id:
        return None
    name = _valid_display_name(entry.get('user_name'), user_id) or user_id
    return MemberCandidate(name=name, user_id=user_id, avatar='')


def _marry_owner_enabled() -> bool:
    return _cfg_bool('DailyWifeMarryOwnerEnabled', False)


@sv.on_message(block=False)
async def _watch_group_owner(bot: Bot, ev: Event) -> None:
    try:
        _record_owner_sighting(ev)
    except Exception as exc:
        logger.debug(f'{LOG_PREFIX} 记录群主缓存时出现异常（忽略）: {exc}')


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

    logger.info(f'{LOG_PREFIX} 触发抽群友逻辑')
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
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        logger.debug(f'{LOG_PREFIX} 成功加载数据文件: {path.name}')
    except Exception as exc:
        logger.exception(f'{LOG_PREFIX} 读取数据文件失败，将使用空数据: {exc}')
        return {'days': {}}
    if not isinstance(data, dict):
        return {'days': {}}
    data.setdefault('days', {})
    return data


def _save_wife_data(data: dict[str, Any]) -> None:
    try:
        _wife_data_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        logger.debug(f'{LOG_PREFIX} 数据文件已保存')
    except Exception as exc:
        logger.error(f'{LOG_PREFIX} 保存数据文件失败: {exc}')


def _get_today_context(data: dict[str, Any], ev: Event) -> dict[str, Any]:
    day = data.setdefault('days', {}).setdefault(_today_key(), {})
    context = day.setdefault(_context_key(ev), {})
    context.setdefault('wives', {})
    context.setdefault('husbands', {})
    context.setdefault('marry_members', {})
    context.setdefault('rob_attempts', {})
    context.setdefault('safe_wives', {})
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
#   stolen_by / gifted_to ：记录已离手（被抢走 / 送出去），原主变“空”
#   stolen_from / gifted_from ：记录来源（抢来的 / 别人送的），即“二手”
def _wife_state(raw: Any) -> str:
    """返回老婆记录的持有状态：owned 正常持有 / lost_stolen 被抢走 / lost_gifted 送出去。"""
    if not isinstance(raw, dict):
        return 'owned'
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



def _get_existing_daily_wife_record(ev: Event, user_id: str | int) -> WifeRecord | None:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = context['wives'].get(_user_key(ev, user_id))
    if isinstance(current, dict):
        return _record_from_dict(current)
    return None



async def _send_role_image(
    bot: Bot,
    role: RoleCandidate,
    image_url: str,
    text: str | None = None,
    user_id: str | int | None = None,
) -> None:
    if image_url.startswith(('http://', 'https://')):
        try:
            image: Any = await _download_image(image_url)
        except RuntimeError as exc:
            logger.warning(f'{LOG_PREFIX} 下载图库图片失败: {exc}')
            await _send_prefixed(bot, str(exc))
            return
    else:
        if not Path(image_url).is_file():
            logger.warning(f'{LOG_PREFIX} 本地图片不存在: {image_url}')
            await _send_prefixed(bot,'本地图片文件不存在，请检查 custom_role_pile 目录。')
            return
        image = Path(image_url)

    messages: list[Any] = []
    if user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    messages.append(MessageSegment.image(image))
    await _send_prefixed(bot,messages if len(messages) > 1 else messages[0])


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
            logger.warning(f'{LOG_PREFIX} 本地图片不存在: {image_url}')
            if not text:
                await _send_prefixed(bot,missing_hint)
                return
        else:
            messages.append(MessageSegment.image(Path(image_url)))

    if not messages:
        await _send_prefixed(bot,missing_hint)
        return
    await _send_prefixed(bot,messages if len(messages) > 1 else messages[0])


