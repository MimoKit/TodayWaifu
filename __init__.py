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

from .daily_wife_config import DailyWifeConfig

Plugins(
    name='TodayWaifu',
    disable_force_prefix=True,
    allow_empty_prefix=True,
)

sv = SV('鸣潮今日老婆')
upload_sv = SV('鸣潮今日老婆上传', pm=1)
BASE_DIR = Path(__file__).parent
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


def _role_ids_by_name(role_map: dict[str, str], role_name: str) -> tuple[str, ...]:
    target = _normalize_role_name(role_name)
    ids = [role_id for role_id, name in role_map.items() if _normalize_role_name(name) == target]
    return tuple(sorted(ids, key=lambda item: int(item) if item.isdigit() else item))


def _next_custom_role_id(role_map: dict[str, str], pile_root: Path) -> str:
    used_ids: set[int] = set()
    for role_id in role_map:
        if role_id.isdigit():
            used_ids.add(int(role_id))
    if pile_root.is_dir():
        for item in pile_root.iterdir():
            if item.is_dir() and item.name.isdigit():
                used_ids.add(int(item.name))

    role_id = max([CUSTOM_ROLE_ID_START - 1, *(item for item in used_ids if item >= CUSTOM_ROLE_ID_START)]) + 1
    while role_id in used_ids:
        role_id += 1
    return str(role_id)


def _append_role_map_line(path: Path, role_id: str, role_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding='utf-8') if path.is_file() else ''
    prefix = '' if not text or text.endswith('\n') else '\n'
    path.write_text(f'{text}{prefix}{role_id}：{role_name}\n', encoding='utf-8')


def _clean_upload_role_name(raw: str, strip_wife_suffix: bool = False) -> str:
    name = str(raw or '').strip().strip('"“”‘’')
    name = re.sub(r'\s+', ' ', name)
    if strip_wife_suffix and name.endswith('老婆') and len(name) > 2:
        name = name[:-2].strip()
    return name


def _create_or_get_custom_role(role_name: str) -> tuple[str, bool, str | None]:
    role_name = _clean_upload_role_name(role_name)
    if not role_name:
        return '', False, '请输入角色名，例如：老婆创建达妮娅老婆'

    map_path = _writable_role_map_path()
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    pile_root = _writable_role_pile_root()
    role_ids = _role_ids_by_name(role_map, role_name)
    if role_ids:
        role_id = role_ids[0]
        (pile_root / role_id).mkdir(parents=True, exist_ok=True)
        return role_id, False, None

    role_id = _next_custom_role_id(role_map, pile_root)
    _append_role_map_line(map_path, role_id, role_name)
    (pile_root / role_id).mkdir(parents=True, exist_ok=True)
    _invalidate_candidate_cache()
    logger.info(f'{LOG_PREFIX} 创建自定义老婆角色: {role_name} -> {role_id}')
    return role_id, True, None


def _upload_image_refs(ev: Event) -> tuple[str, ...]:
    refs: list[str] = []
    for content in ev.content or []:
        if content.type in {'image', 'img'} and isinstance(content.data, str):
            ref = content.data.strip()
            if ref:
                refs.append(ref)
    for item in ev.image_list or []:
        if isinstance(item, str) and item.strip():
            refs.append(item.strip())
    if isinstance(ev.image, str) and ev.image.strip():
        refs.append(ev.image.strip())
    return tuple(dict.fromkeys(refs))


def _image_suffix_from_source(source: str) -> str:
    text = str(source or '').strip()
    if text.startswith('link://'):
        text = text[7:]
    try:
        path_text = urlparse(text).path if text.startswith(('http://', 'https://')) else text
        suffix = Path(path_text.split('?', 1)[0]).suffix.lower()
    except Exception:
        suffix = ''
    return suffix if suffix in IMAGE_EXTENSIONS else ''


def _detect_upload_image_suffix(data: bytes, source: str) -> str:
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return '.gif'
    if data.startswith(b'BM'):
        return '.bmp'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    return _image_suffix_from_source(source)


def _read_upload_image_bytes(source: str) -> tuple[bytes, str] | None:
    text = str(source or '').strip()
    if not text:
        return None

    try:
        if text.startswith('data:image/') and ',' in text:
            data = base64.b64decode(text.split(',', 1)[1], validate=False)
        elif text.startswith('base64://'):
            data = base64.b64decode(text[9:], validate=False)
        else:
            if text.startswith('link://'):
                text = text[7:]
            if text.startswith(('http://', 'https://')):
                request = Request(text, headers={'User-Agent': 'Mozilla/5.0'})
                with urlopen(request, timeout=15) as response:
                    data = response.read(UPLOAD_IMAGE_MAX_BYTES + 1)
            else:
                path = Path(text)
                if not path.is_file():
                    return None
                data = path.read_bytes()
    except (OSError, ValueError, binascii.Error) as exc:
        logger.warning(f'{LOG_PREFIX} 读取上传图片失败: {exc}')
        return None

    if not data or len(data) > UPLOAD_IMAGE_MAX_BYTES:
        return None
    suffix = _detect_upload_image_suffix(data, source)
    if suffix not in IMAGE_EXTENSIONS:
        return None
    return data, suffix


def _unique_upload_image_path(role_dir: Path, role_id: str, suffix: str, index: int) -> Path:
    stamp = int(time.time() * 1000)
    counter = 0
    while True:
        tail = f'_{counter}' if counter else ''
        path = role_dir / f'{role_id}_{stamp}_{index}{tail}{suffix}'
        if not path.exists():
            return path
        counter += 1


def _custom_image_hash_id(path: Path | str) -> str:
    return hashlib.sha256(Path(path).name.encode()).hexdigest()[:8]


def _custom_role_image_map(role_id: str) -> dict[str, Path]:
    role_dir = _writable_role_pile_root() / str(role_id)
    if not role_dir.is_dir():
        return {}
    result: dict[str, Path] = {}
    for path in sorted(role_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            result[_custom_image_hash_id(path)] = path
    return result


def _custom_role_ids_for_name(role_name: str) -> tuple[str, ...]:
    map_path = _writable_role_map_path()
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    return _role_ids_by_name(role_map, role_name)


def _custom_role_name_by_id(role_id: str) -> str:
    map_path = _writable_role_map_path()
    role_map = _load_role_map(map_path) if map_path.is_file() else {}
    return role_map.get(str(role_id), str(role_id))


def _remove_custom_role_map_ids(role_ids: tuple[str, ...]) -> None:
    if not role_ids:
        return
    map_path = _writable_role_map_path()
    if not map_path.is_file():
        return
    role_id_set = {str(role_id) for role_id in role_ids}
    kept: list[str] = []
    for line in map_path.read_text(encoding='utf-8').splitlines():
        match = ROLE_MAP_RE.match(line)
        if match and match.group(1) in role_id_set:
            continue
        kept.append(line)
    text = '\n'.join(kept)
    if text:
        text += '\n'
    map_path.write_text(text, encoding='utf-8')


def _custom_role_delete_confirm_key(ev: Event) -> str:
    return f'{_context_key(ev)}:{_user_key(ev)}'


def _get_pending_custom_role_delete(ev: Event) -> dict[str, Any] | None:
    key = _custom_role_delete_confirm_key(ev)
    pending = CUSTOM_ROLE_DELETE_PENDING.get(key)
    if not isinstance(pending, dict):
        return None
    try:
        created_at = int(pending.get('created_at') or 0)
    except (TypeError, ValueError):
        created_at = 0
    if time.time() - created_at > CUSTOM_ROLE_DELETE_CONFIRM_SECONDS:
        CUSTOM_ROLE_DELETE_PENDING.pop(key, None)
        return None
    return pending


def _set_pending_custom_role_delete(ev: Event, role_id: str, role_name: str, image_count: int) -> None:
    CUSTOM_ROLE_DELETE_PENDING[_custom_role_delete_confirm_key(ev)] = {
        'role_id': role_id,
        'role_name': role_name,
        'image_count': image_count,
        'created_at': int(time.time()),
    }


def _clear_pending_custom_role_delete(ev: Event) -> None:
    CUSTOM_ROLE_DELETE_PENDING.pop(_custom_role_delete_confirm_key(ev), None)


def _custom_role_image_entries(role_name: str) -> tuple[str, str, list[tuple[str, Path]]] | None:
    role_name = _clean_upload_role_name(role_name, strip_wife_suffix=True)
    if not role_name:
        return None
    role_ids = _custom_role_ids_for_name(role_name)
    if not role_ids:
        return None
    role_id = role_ids[0]
    image_map = _custom_role_image_map(role_id)
    return role_id, role_name, sorted(image_map.items(), key=lambda item: item[1].name.lower())


def _resolve_custom_role_for_delete(role_name: str) -> tuple[str, str, list[tuple[str, Path]], str | None]:
    entries = _custom_role_image_entries(role_name)
    if entries is None:
        cleaned = _clean_upload_role_name(role_name, strip_wife_suffix=True)
        return '', cleaned, [], f'未找到自定义老婆【{cleaned or role_name}】。'
    role_id, role_name, images = entries
    return role_id, role_name, images, None


def _delete_custom_role(role_id: str) -> int:
    role_dir = _writable_role_pile_root() / str(role_id)
    image_count = len(_custom_role_image_map(role_id))
    if role_dir.is_dir():
        shutil.rmtree(role_dir)
    _remove_custom_role_map_ids((str(role_id),))
    _invalidate_candidate_cache()
    return image_count


def _resolve_custom_image_for_delete(role_name: str, hash_id: str) -> tuple[str, str, Path | None, str | None]:
    role_name = _clean_upload_role_name(role_name, strip_wife_suffix=True)
    hash_id = str(hash_id or '').strip().lower()
    if not role_name:
        return '', '', None, '请输入角色名，例如：老婆删除图片达妮娅 abcd1234'
    if not re.fullmatch(r'[0-9a-f]{8}', hash_id):
        return '', role_name, None, '图片ID格式错误，请使用列表里显示的 8 位 ID。'

    role_ids = _custom_role_ids_for_name(role_name)
    if not role_ids:
        return '', role_name, None, f'未找到自定义老婆【{role_name}】。'

    role_id = role_ids[0]
    image_map = _custom_role_image_map(role_id)
    image_path = image_map.get(hash_id)
    if image_path is None:
        return role_id, role_name, None, f'【{role_name}】未找到图片ID：{hash_id}'
    return role_id, role_name, image_path, None


def _parse_delete_custom_image_text(text: str) -> tuple[str, str]:
    raw = str(text or '').strip()
    match = re.search(r'([0-9a-fA-F]{8})\s*$', raw)
    if not match:
        return _clean_upload_role_name(raw, strip_wife_suffix=True), ''
    hash_id = match.group(1).lower()
    role_name = _clean_upload_role_name(raw[: match.start()], strip_wife_suffix=True)
    return role_name, hash_id


def _save_upload_image_ref(role_dir: Path, role_id: str, source: str, index: int) -> Path | None:
    image_data = _read_upload_image_bytes(source)
    if image_data is None:
        return None
    data, suffix = image_data
    role_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_upload_image_path(role_dir, role_id, suffix, index)
    path.write_bytes(data)
    return path

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


def _safe_loli_image_name(filename: str, index: int) -> str:
    suffix = Path(filename).suffix.lower()
    stem = re.sub(r'[^0-9A-Za-z._-]+', '_', Path(filename).stem).strip('._-')
    if not stem:
        stem = f'image_{index}'
    return f'{stem}{suffix}'


def _unique_loli_image_path(root: Path, filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem
    candidate = root / filename
    counter = 1
    while candidate.exists():
        candidate = root / f'{stem}_{counter}{suffix}'
        counter += 1
    return candidate


def _loli_image_paths() -> tuple[Path, ...]:
    root = _loli_image_root()
    if not root.is_dir():
        return ()
    images = [
        path
        for path in root.rglob('*')
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return tuple(sorted(images, key=lambda path: str(path).lower()))


def _probe_url_ok(url: str, timeout: int) -> bool:
    """快速测速：用 1 字节 Range 请求，timeout 内成功响应即视为可用。"""
    if not url:
        return False
    request = Request(url, headers={'User-Agent': 'TodayWaifu/1.0', 'Range': 'bytes=0-0'})
    try:
        with urlopen(request, timeout=timeout) as response:
            code = getattr(response, 'status', 200)
            response.read(1)
            return 200 <= code < 400
    except Exception as exc:  # noqa: BLE001
        logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 测速下载源失败: {exc}')
        return False


def _select_loli_download_source() -> tuple[str, bool]:
    """选择下载源：优先测速直连 GitHub，慢/失败则切备用源。

    返回 (下载地址, 是否走备用源)。
    """
    logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 开始测速直连 GitHub 下载源')
    if _probe_url_ok(LOLI_IMAGE_REPO_ZIP_URL, LOLI_DOWNLOAD_PROBE_TIMEOUT):
        logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 直连 GitHub 测速正常，使用直连下载源')
        return LOLI_IMAGE_REPO_ZIP_URL, False
    if LOLI_IMAGE_BACKUP_ZIP_URL:
        logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 直连 GitHub 测速偏慢/失败，改用备用下载源')
        return LOLI_IMAGE_BACKUP_ZIP_URL, True
    logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 未配置备用下载源，仍使用直连下载源')
    return LOLI_IMAGE_REPO_ZIP_URL, False


def _human_size(num: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if num < 1024 or unit == 'GB':
            return f'{num:.1f}{unit}' if unit != 'B' else f'{int(num)}B'
        num /= 1024
    return f'{num:.1f}GB'


def _download_loli_zip(zip_path: Path, url: str, *, source_label: str = '') -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    label = f'（{source_label}）' if source_label else ''
    logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 开始下载压缩包{label} url={url}')
    request = Request(url, headers={'User-Agent': 'TodayWaifu/1.0'})
    try:
        with urlopen(request, timeout=60) as response:
            content_length = 0
            try:
                content_length = int(response.headers.get('Content-Length') or 0)
            except (TypeError, ValueError):
                content_length = 0
            size_hint = _human_size(content_length) if content_length else '未知'
            logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 已连接下载源，文件大小：{size_hint}')

            total = 0
            next_log_at = 8 * 1024 * 1024  # 每约 8MB 打一次进度
            start_ts = time.time()
            with zip_path.open('wb') as file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > LOLI_IMAGE_ZIP_MAX_BYTES:
                        raise RuntimeError('下载到的图片压缩包过大。')
                    file.write(chunk)
                    if total >= next_log_at:
                        if content_length:
                            pct = total * 100 / content_length
                            logger.info(
                                f'{LOLI_DOWNLOAD_LOG_PREFIX} 下载进度 {pct:.0f}% '
                                f'（{_human_size(total)}/{_human_size(content_length)}）'
                            )
                        else:
                            logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 下载进度 {_human_size(total)}')
                        next_log_at += 8 * 1024 * 1024
    except HTTPError as exc:
        raise RuntimeError(f'下载萝莉图片失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'下载萝莉图片失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('下载萝莉图片超时。') from exc

    if not zip_path.is_file() or zip_path.stat().st_size <= 0:
        raise RuntimeError('下载到的图片压缩包为空。')
    if not zipfile.is_zipfile(zip_path):
        raise RuntimeError('下载到的文件不是有效 zip 压缩包。')
    elapsed = max(0.001, time.time() - start_ts)
    logger.info(
        f'{LOLI_DOWNLOAD_LOG_PREFIX} 压缩包下载完成，共 {_human_size(zip_path.stat().st_size)}，'
        f'耗时 {elapsed:.1f}s，均速 {_human_size(total / elapsed)}/s'
    )


def _existing_loli_image_hashes() -> set[str]:
    hashes: set[str] = set()
    for path in _loli_image_paths():
        try:
            hashes.add(hashlib.sha256(path.read_bytes()).hexdigest())
        except OSError as exc:
            logger.warning(f'{LOG_PREFIX} 读取萝莉图片用于查重失败: {path} -> {exc}')
    return hashes


def _extract_loli_images(zip_path: Path) -> LoliImageDownloadResult:
    target_root = _loli_image_root()
    target_root.mkdir(parents=True, exist_ok=True)
    logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 开始解压并查重，目标目录：{target_root}')
    existing_hashes = _existing_loli_image_hashes()
    logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 已有图片 {len(existing_hashes)} 张用于查重')

    saved = 0
    duplicated = 0
    skipped = 0
    total_size = 0
    processed = 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            entries = archive.infolist()
            total_entries = sum(1 for info in entries if not info.is_dir())
            logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 压缩包内共 {total_entries} 个文件，开始逐个处理')
            for index, info in enumerate(entries, 1):
                if info.is_dir():
                    continue
                processed += 1
                filename = Path(info.filename.replace('\\', '/')).name
                suffix = Path(filename).suffix.lower()
                if suffix not in IMAGE_EXTENSIONS:
                    skipped += 1
                    continue
                if info.file_size <= 0 or info.file_size > UPLOAD_IMAGE_MAX_BYTES:
                    skipped += 1
                    continue
                total_size += info.file_size
                if total_size > LOLI_IMAGE_ZIP_MAX_BYTES:
                    raise RuntimeError('图片压缩包解压后体积过大。')
                with archive.open(info) as source:
                    data = source.read(UPLOAD_IMAGE_MAX_BYTES + 1)
                if not data or len(data) > UPLOAD_IMAGE_MAX_BYTES:
                    skipped += 1
                    continue
                image_hash = hashlib.sha256(data).hexdigest()
                if image_hash in existing_hashes:
                    duplicated += 1
                    continue
                image_name = _safe_loli_image_name(filename, index)
                image_path = _unique_loli_image_path(target_root, image_name)
                image_path.write_bytes(data)
                existing_hashes.add(image_hash)
                saved += 1
                if saved % 50 == 0:
                    logger.info(
                        f'{LOLI_DOWNLOAD_LOG_PREFIX} 解压进度：已处理 {processed}/{total_entries}，'
                        f'新增 {saved} 张'
                    )
    except zipfile.BadZipFile as exc:
        raise RuntimeError('图片压缩包解压失败。') from exc

    if saved <= 0 and duplicated <= 0:
        raise RuntimeError('图片压缩包里没有找到可用图片。')
    logger.info(
        f'{LOLI_DOWNLOAD_LOG_PREFIX} 解压完成：新增 {saved} 张，重复跳过 {duplicated} 张，'
        f'无效跳过 {skipped} 个，累计解压 {_human_size(total_size)}'
    )
    return LoliImageDownloadResult(saved=saved, duplicated=duplicated, skipped=skipped)


def _download_and_extract_loli_images(url: str, *, source_label: str = '') -> LoliImageDownloadResult:
    zip_path = _custom_upload_data_root() / f'loli_images.{int(time.time() * 1000)}.zip.tmp'
    logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 临时压缩包路径：{zip_path}')
    try:
        _download_loli_zip(zip_path, url, source_label=source_label)
        return _extract_loli_images(zip_path)
    finally:
        if zip_path.exists():
            zip_path.unlink()
            logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 已清理临时压缩包')


def _delete_loli_images() -> int:
    root = _loli_image_root()
    count = len(_loli_image_paths())
    if root.exists():
        if root.is_dir():
            shutil.rmtree(root)
        else:
            root.unlink()
    return count


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
    return 'self'


def _is_secondhand_wife(raw: Any) -> bool:
    """二手老婆 = 抢来的或别人送的（到手即终结，不能再流转）。"""
    return _wife_origin(raw) in ('robbed', 'gifted')


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


def _build_gift_success_text(role: RoleCandidate, target_user_id: str) -> str:
    return str(_cfg('DailyWifeGiftSuccessTemplate') or '你把今天的老婆{name}送给了对方！').format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )


def _github_update_api_url() -> str:
    url = str(_cfg('DailyWifeUpdateLogApiUrl') or GITHUB_UPDATE_API_URL).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise RuntimeError('老婆更新记录接口地址无效，请检查 DailyWifeUpdateLogApiUrl 配置。')
    return url


def _github_update_limit() -> int:
    try:
        value = int(_cfg('DailyWifeUpdateLogLimit'))
    except (TypeError, ValueError):
        value = 6
    return max(1, min(12, value))


def _github_update_headers() -> dict[str, str]:
    return {
        'User-Agent': 'TodayWaifu/1.0',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }


def _github_update_fetch_sync() -> tuple[GitHubUpdateRecord, ...]:
    request = Request(_github_update_api_url(), headers=_github_update_headers())
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read(512 * 1024 + 1)
    except HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError('GitHub 更新记录接口访问受限或频率过高，请稍后再试。') from exc
        if exc.code == 404:
            raise RuntimeError('GitHub 更新记录接口不存在，请检查仓库地址。') from exc
        raise RuntimeError(f'获取 GitHub 更新记录失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'获取 GitHub 更新记录失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('获取 GitHub 更新记录超时。') from exc

    if len(body) > 512 * 1024:
        raise RuntimeError('GitHub 更新记录返回内容过大。')

    try:
        payload = json.loads(body.decode('utf-8'))
    except Exception as exc:
        raise RuntimeError('GitHub 更新记录返回内容不是有效 JSON。') from exc

    if isinstance(payload, dict):
        message = str(payload.get('message') or '').strip()
        if message:
            raise RuntimeError(f'GitHub 更新记录接口返回错误：{message}')
    if not isinstance(payload, list):
        raise RuntimeError('GitHub 更新记录返回格式不正确。')

    records = _parse_github_update_records(payload)
    if not records:
        raise RuntimeError('没有获取到可展示的 GitHub 更新记录。')
    return records


def _parse_github_update_records(payload: list[Any]) -> tuple[GitHubUpdateRecord, ...]:
    records: list[GitHubUpdateRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        commit = item.get('commit')
        if not isinstance(commit, dict):
            continue

        raw_message = str(commit.get('message') or '').strip()
        if not raw_message:
            continue
        message = raw_message.splitlines()[0].strip()

        author_data = commit.get('author') if isinstance(commit.get('author'), dict) else {}
        committer_data = commit.get('committer') if isinstance(commit.get('committer'), dict) else {}
        github_author = item.get('author') if isinstance(item.get('author'), dict) else {}
        author = str(
            github_author.get('login')
            or author_data.get('name')
            or committer_data.get('name')
            or 'unknown'
        )
        updated_at = str(author_data.get('date') or committer_data.get('date') or '')
        sha = str(item.get('sha') or '')
        url = str(item.get('html_url') or '')
        records.append(
            GitHubUpdateRecord(
                message=message,
                sha=sha[:7],
                author=author,
                updated_at=updated_at,
                url=url,
            )
        )
    return tuple(records)


def _select_github_update_records(records: tuple[GitHubUpdateRecord, ...]) -> tuple[GitHubUpdateRecord, ...]:
    # GitHub commits 接口本身按时间倒序返回，直接取最新 N 条，不打乱顺序
    return records[:_github_update_limit()]


def _format_github_update_time(value: str) -> str:
    value = value.strip()
    if not value:
        return '未知时间'
    if 'T' in value and len(value) >= 19:
        return f'{value[:19].replace("T", " ")} UTC'
    return value


def _build_github_update_html(records: tuple[GitHubUpdateRecord, ...]) -> str:
    cards: list[str] = []
    for index, record in enumerate(records):
        is_latest = index == 0
        card_class = 'record latest' if is_latest else 'record'
        badge = '最新' if is_latest else f'#{index + 1}'
        safe_message = html.escape(record.message)
        safe_author = html.escape(record.author)
        safe_time = html.escape(_format_github_update_time(record.updated_at))
        safe_sha = html.escape(record.sha or 'unknown')
        cards.append(
            f'''
            <div class="{card_class}">
              <div class="record-top">
                <span class="badge">{badge}</span>
                <span class="sha">{safe_sha}</span>
                <span class="time">{safe_time}</span>
              </div>
              <div class="message">{safe_message}</div>
              <div class="meta">by {safe_author}</div>
            </div>
            '''
        )

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: {GITHUB_UPDATE_RENDER_WIDTH}px;
    padding: 40px;
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    color: #fff;
    background: linear-gradient(135deg, #2b1055 0%, #43275f 48%, #7c2d6b 100%);
  }}
  .wrap {{
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 28px;
    padding: 34px 36px 32px;
    background: rgba(20, 12, 38, 0.58);
    box-shadow: 0 22px 68px rgba(0,0,0,0.42);
  }}
  .header {{ display: flex; align-items: center; gap: 18px; }}
  .logo {{
    width: 70px; height: 70px; border-radius: 22px;
    display: flex; align-items: center; justify-content: center;
    font-size: 36px; background: linear-gradient(135deg, #ff7eb3, #ff758c);
    box-shadow: 0 8px 24px rgba(255,117,140,0.48);
  }}
  h1 {{ font-size: 30px; letter-spacing: 1px; }}
  .subtitle {{ margin-top: 7px; color: #c9b8e8; font-size: 14px; line-height: 1.55; }}
  .divider {{ height: 1px; margin: 26px 0 22px; background: linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent); }}
  .list {{ display: grid; gap: 13px; }}
  .record {{
    border-radius: 18px; padding: 16px 18px;
    background: rgba(255,255,255,0.055);
    border: 1px solid rgba(255,255,255,0.09);
  }}
  .record.latest {{
    background: linear-gradient(135deg, rgba(255,126,179,0.22), rgba(182,155,255,0.15));
    border-color: rgba(255,126,179,0.48);
    box-shadow: inset 0 0 0 1px rgba(255,255,255,0.06), 0 10px 28px rgba(255,117,140,0.16);
  }}
  .record-top {{ display: flex; align-items: center; gap: 10px; }}
  .badge {{
    padding: 4px 11px; border-radius: 999px; font-size: 12px; color: #ffe1ee;
    background: rgba(255,126,179,0.18); border: 1px solid rgba(255,126,179,0.32);
  }}
  .record.latest .badge {{ background: rgba(255,126,179,0.34); border-color: rgba(255,126,179,0.6); color: #fff; }}
  .sha {{ font-family: "Cascadia Code", "JetBrains Mono", monospace; color: #b69bff; font-size: 13px; }}
  .time {{ margin-left: auto; color: #9d8ec0; font-size: 12.5px; }}
  .message {{ margin-top: 12px; color: #fff; font-size: 17px; line-height: 1.55; font-weight: 700; word-break: break-word; }}
  .meta {{ margin-top: 10px; color: #c9b8e8; font-size: 12.5px; }}
  .footer {{ margin-top: 24px; text-align: center; color: #9d8ec0; font-size: 12px; }}
  .footer span {{ color: #ff9ecb; font-weight: 700; }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div class="logo">📝</div>
      <div>
        <h1>老婆更新记录</h1>
        <div class="subtitle">实时获取 GitHub 最近更新，按时间从新到旧排列</div>
      </div>
    </div>
    <div class="divider"></div>
    <div class="list">{''.join(cards)}</div>
    <div class="footer">TodayWaifu · GitHub 实时更新记录 · Created by <span>nnlmc</span></div>
  </div>
</body>
</html>'''


async def _render_github_update_image(records: tuple[GitHubUpdateRecord, ...]) -> Any:
    try:
        from gsuid_core.utils.html_render import render_html_to_bytes
    except Exception as exc:
        raise RuntimeError('当前 GSCore 未提供 HTML 渲染组件，请更新 GSCore 或安装 pyrenderhtml>=0.0.5。') from exc

    html_doc = _build_github_update_html(records)
    try:
        image = await render_html_to_bytes(
            html_doc,
            max_width=GITHUB_UPDATE_RENDER_WIDTH,
            dpi=96,
            default_font_size=14,
            font_name='sans-serif',
            image_format='png',
            lang='zh',
        )
    except Exception as exc:
        raise RuntimeError('老婆更新记录图片渲染失败，请查看控制台日志。') from exc

    try:
        from gsuid_core.utils.image.convert import convert_img

        return await convert_img(image)
    except Exception:
        return image


async def _send_github_update_log(bot: Bot, ev: Event) -> None:
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 请求老婆更新记录')
    if not _cfg_bool('DailyWifeUpdateLogEnabled', True):
        return await _send_prefixed(bot, '老婆更新记录功能当前已关闭。')

    try:
        records = await asyncio.to_thread(_github_update_fetch_sync)
        selected = _select_github_update_records(records)
        image = await _render_github_update_image(selected)
    except RuntimeError as exc:
        logger.warning(f'{LOG_PREFIX} 获取老婆更新记录失败: {exc}')
        return await _send_prefixed(bot, str(exc))

    await _send_prefixed(bot, MessageSegment.image(image))


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
    key = _user_key(ev, user_id)

    # 快速路径：当天已有记录直接返回
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = context[bucket].get(key)
    if isinstance(current, dict):
        record = _record_from_dict(current)
        if record is not None:
            logger.debug(f'{LOG_PREFIX} 命中已有的 {mode} 记录: {record.name}')
            return record

    # 准备阶段：含 await，先不持有待写入的 data，避免覆盖期间其它协程的写入
    chosen: WifeRecord | None = None
    if mode == 'wife':
        chosen = await _roll_group_member_wife(ev, key)

    if chosen is None:
        candidates, error = await _load_candidates()
        if error or not candidates:
            logger.error(f'{LOG_PREFIX} 获取候选列表失败: {error}')
            return None
        candidates = _filter_by_mode(candidates, mode)
        if not candidates:
            logger.warning(f'{LOG_PREFIX} 过滤后没有可用的 {mode} 角色')
            return None
        rng = _daily_rng(ev, key, salt)
        role = rng.choice(candidates)
        image = rng.choice(role.images)
        chosen = WifeRecord.from_role(role, image)

    # 写入阶段：重新加载并二次校验，整段不含 await，事件循环下保证原子
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    existing = context[bucket].get(key)
    if isinstance(existing, dict):
        existing_record = _record_from_dict(existing)
        if existing_record is not None:
            logger.debug(f'{LOG_PREFIX} 写入前发现已有 {mode} 记录，直接复用: {existing_record.name}')
            return existing_record

    logger.info(f'{LOG_PREFIX} 为用户 {key} 生成新的 {mode}: {chosen.name}')
    context[bucket][key] = _record_to_dict(chosen, ev, key)
    _save_wife_data(data)
    return chosen


def _get_existing_daily_wife_record(ev: Event, user_id: str | int) -> WifeRecord | None:
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = context['wives'].get(_user_key(ev, user_id))
    if isinstance(current, dict):
        return _record_from_dict(current)
    return None


async def _wife_list_items(ev: Event, mode: str = 'wife') -> tuple[str, list[tuple[int, str, str]]]:
    bucket = 'husbands' if mode == 'husband' else 'wives'
    title = '老公' if mode == 'husband' else '老婆'
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    wives = context.get(bucket, {})
    if not isinstance(wives, dict) or not wives:
        return f'今天本群还没有人抽{title}。', []

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
        state = _wife_state(raw_record)
        if state == 'lost_stolen':
            wife_name = '被抢走了~'
        elif state == 'lost_gifted':
            wife_name = '送出去了~'
        else:
            wife_name = record.name

        items.append((order, display_name, wife_name))

    if not items:
        return f'今天本群还没有可用的{title}记录。', []

    if data_changed:
        _save_wife_data(data)

    items.sort(key=lambda item: (item[0], item[1]))
    return f'今日{title}列表：', items


def _wife_list_text_from_items(title_text: str, items: list[tuple[int, str, str]]) -> str:
    if not items:
        return title_text
    lines = [title_text]
    lines.extend(f'{index}. {display_name} → {wife_name}' for index, (_, display_name, wife_name) in enumerate(items, 1))
    return '\n'.join(lines)


async def _wife_list_text(ev: Event, mode: str = 'wife') -> str:
    title_text, items = await _wife_list_items(ev, mode)
    return _wife_list_text_from_items(title_text, items)


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


async def _send_record_image(
    bot: Bot,
    record: WifeRecord,
    mode: str = 'wife',
    user_id: str | int | None = None,
) -> None:
    text = _record_text(record, mode) if bool(_cfg('DailyWifeSendText')) else None
    if record.record_type == 'member':
        await _send_local_image(bot, record.image, '本地群友头像文件不存在，请稍后重试。', text, user_id)
        return
    await _send_role_image(bot, record.to_role(), record.image, text, user_id)


async def _send_loli_image(bot: Bot, ev: Event) -> None:
    images = _loli_image_paths()
    if not images:
        return await _send_loli_text(bot, '还没有萝莉图片，请先发送“下载萝莉图片”。')

    image = random.choice(images)
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日萝莉，发送图片: {image}')
    await bot.send([
        _with_loli_reply_prefix('你今天的萝莉是'),
        MessageSegment.image(image),
    ])


async def _send_download_loli_images(bot: Bot, ev: Event) -> None:
    logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 用户 {ev.user_id} 触发下载萝莉图片命令')
    await _send_loli_text(bot, '正在测速选择下载线路，请稍等...')

    url, use_backup = await asyncio.to_thread(_select_loli_download_source)
    if use_backup:
        logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 直连 GitHub 较慢，切换备用下载源')
        await _send_loli_text(bot, '直连 GitHub 较慢，已切换备用下载源，开始下载...')
    else:
        logger.info(f'{LOLI_DOWNLOAD_LOG_PREFIX} 直连 GitHub 正常，使用直连下载')
        await _send_loli_text(bot, '直连 GitHub 正常，开始下载...')

    source_label = '备用下载源' if use_backup else '直连 GitHub'
    try:
        result = await asyncio.to_thread(_download_and_extract_loli_images, url, source_label=source_label)
    except RuntimeError as exc:
        # 直连失败时再兜底尝试一次备用源
        if not use_backup and LOLI_IMAGE_BACKUP_ZIP_URL:
            logger.warning(f'{LOLI_DOWNLOAD_LOG_PREFIX} 直连下载失败({exc})，改用备用下载源重试')
            await _send_loli_text(bot, '直连下载失败，正在改用备用下载源重试...')
            try:
                result = await asyncio.to_thread(
                    _download_and_extract_loli_images, LOLI_IMAGE_BACKUP_ZIP_URL, source_label='备用下载源'
                )
            except RuntimeError as exc2:
                logger.warning(f'{LOLI_DOWNLOAD_LOG_PREFIX} 备用下载源也失败: {exc2}')
                return await _send_loli_text(bot, str(exc2))
        else:
            logger.warning(f'{LOLI_DOWNLOAD_LOG_PREFIX} 下载萝莉图片失败: {exc}')
            return await _send_loli_text(bot, str(exc))

    logger.info(
        f'{LOLI_DOWNLOAD_LOG_PREFIX} 下载完成 新增={result.saved} 重复={result.duplicated} 无效={result.skipped}'
    )
    await _send_loli_text(
        bot,
        f'萝莉图片下载完成\n新增：{result.saved} 张\n重复跳过：{result.duplicated} 张\n无效跳过：{result.skipped} 个',
    )


async def _send_delete_loli_images(bot: Bot, ev: Event) -> None:
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 触发删除萝莉图片命令')
    count = await asyncio.to_thread(_delete_loli_images)
    await _send_loli_text(bot, f'已删除全部萝莉图片，共 {count} 张。')


async def _send_daily_wife(bot: Bot, ev: Event, mode: str = 'wife', specified_name: str = ''):
    if mode == 'husband' and _gallery_mode_enabled():
        return await _send_prefixed(bot, _husband_unavailable_message())

    title = '老公' if mode == 'husband' else '老婆'
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} 请求 {title} (指定: {specified_name or "无"})')
    
    is_master = _is_master(ev)
    is_debug = _cfg_bool('DailyWifeDebugMode', False)
    is_debug_active = is_debug and is_master

    if mode == 'wife' and not is_debug_active:
        data = _load_wife_data()
        context = _get_today_context(data, ev)
        user_key = _user_key(ev)
        current_record = context['wives'].get(user_key)

        # 离手即结算：被抢走 / 送出去后当天锁死，不再分配新角色
        state = _wife_state(current_record)
        if state == 'lost_stolen':
            wife_name = current_record.get('name', '老婆')
            stolen_by_name = current_record.get('stolen_by_name') or current_record.get('stolen_by')
            logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 的老婆已被抢，拒绝分配新角色')
            return await _send_prefixed(bot,f'你的{wife_name}已经被{stolen_by_name}抢走了，今天就先忍忍吧~')
        if state == 'lost_gifted':
            wife_name = current_record.get('name', '老婆')
            gifted_to_name = current_record.get('gifted_to_name') or current_record.get('gifted_to')
            logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 的老婆已送出，拒绝分配新角色')
            return await _send_prefixed(bot,f'你的{wife_name}已经送给{gifted_to_name}了，今天就先忍忍吧~')

    record: WifeRecord | None = None

    if is_debug_active:
        logger.debug(f'{LOG_PREFIX} 主人 Debug 模式开启')
        candidates, error = await _load_candidates()
        if error or not candidates:
            return await _send_prefixed(bot, error or '没有找到可用角色。')
        candidates = _filter_by_mode(candidates, mode)
        if not candidates:
            return await _send_prefixed(bot, f'没有找到可用的{title}角色。')
        if specified_name:
            target_candidates = [c for c in candidates if c.name == specified_name]
            if not target_candidates:
                return await _send_prefixed(bot, f'未找到名为“{specified_name}”的{title}角色。')
            role = target_candidates[0]
        else:
            role = random.choice(candidates)

        image = random.choice(role.images)
        record = WifeRecord.from_role(role, image)
    else:
        if specified_name:
            logger.warning(f'{LOG_PREFIX} 普通用户 {ev.user_id} 尝试指定角色 {specified_name}，已拒绝')
            return await _send_prefixed(bot, f'只有在 Debug 模式下主人才能指定{title}哦。')

        record = await _ensure_daily_wife_record(ev, mode=mode)
        if record is None:
            return await _send_prefixed(bot, f'没有找到可用的{title}角色。')

    if record.record_type == 'member':
        member = record.to_member()
        logger.info(
            f'{LOG_PREFIX} mode={mode} user={ev.user_id} group={ev.group_id or "direct"} '
            f'member={member.name} qq={member.user_id} avatar={record.image} debug={is_debug_active}'
        )
    else:
        role = record.to_role()
        logger.info(
            f'{LOG_PREFIX} mode={mode} user={ev.user_id} group={ev.group_id or "direct"} '
            f'role={role.name} ids={role.role_ids} image={record.image} debug={is_debug_active}'
        )
    await _send_record_image(bot, record, mode, ev.user_id)


async def _send_group_member_wife(bot: Bot, ev: Event):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 触发了娶群友命令')
    if not _marry_member_enabled():
        return await _send_prefixed(bot,'娶群友功能当前已关闭。')
    if not ev.group_id:
        return await _send_prefixed(bot,'这个命令只能在群聊里使用。')

    member = await _pick_group_member(ev, _event_rng(ev))
    if member is None:
        return await _send_prefixed(bot,'没有获取到本群成员，暂时娶不到群友。')

    logger.info(
        f'{LOG_PREFIX} marry_member user={ev.user_id} group={ev.group_id} '
        f'member={member.name} qq={member.user_id} avatar={member.avatar}'
    )
    text = _build_member_text(member, 'marry') if bool(_cfg('DailyWifeSendText')) else None
    await _send_local_image(bot, member.avatar, '本地群友头像文件不存在，请稍后重试。', text, ev.user_id)


async def _send_rob_wife(bot: Bot, ev: Event):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id} 发起了抢老婆操作')
    if not _cfg_bool('DailyWifeRobEnabled', True):
        return await _send_prefixed(bot,'抢老婆功能当前已关闭。')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await _send_prefixed(bot,'要抢谁的老婆？请艾特对方或在命令后面写对方 QQ。')

    robber_id = _user_key(ev)
    if target_user_id == robber_id:
        return await _send_prefixed(bot,'自己抢自己的老婆也太奇怪了吧！')

    target_record = _get_existing_daily_wife_record(ev, target_user_id)
    if target_record is None:
        return await _send_prefixed(bot,'对方今天还没有老婆呢~')
    if target_record.record_type == 'member':
        return await _send_prefixed(bot,'对方今天娶到的是群友，不能被抢走哦~')

    data = _load_wife_data()
    context = _get_today_context(data, ev)

    target_data = context['wives'].get(_user_key(ev, target_user_id))
    # 离手即结算：对方老婆已被抢走 / 已送出，不能再抢（堵死复制 BUG 与链式抢）
    if _wife_state(target_data) != 'owned':
        return await _send_prefixed(bot, '对方的老婆已经不在身边了，抢不到了哦~')
    # 到手即终结：抢来的 / 别人送的老婆不能再被抢走
    if _is_secondhand_wife(target_data):
        return await _send_prefixed(bot, '对方这个老婆是抢来或别人送的，抢不动哦~')

    attempts = context.setdefault('rob_attempts', {})
    is_master = _is_master(ev)
    
    if not is_master and attempts.get(robber_id):
        logger.info(f'{LOG_PREFIX} 用户 {robber_id} 今天抢老婆次数已用尽')
        return await _send_prefixed(bot,'今天已经抢过老婆啦，明天再来吧！')

    if not is_master:
        attempts[robber_id] = True

    if random.random() >= _rob_success_rate():
        logger.info(f'{LOG_PREFIX} 用户 {robber_id} 抢 {target_user_id} 的老婆失败')
        _save_wife_data(data)
        return await _send_prefixed(bot,'抢老婆失败了，还被对方痛扁了一顿！🤣')

    logger.info(f'{LOG_PREFIX} 用户 {robber_id} 成功抢走了 {target_user_id} 的老婆')
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


async def _send_gift_wife(bot: Bot, ev: Event):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id} 发起了送老婆操作')
    if not _cfg_bool('DailyWifeGiftEnabled', True):
        return await _send_prefixed(bot, '送老婆功能当前已关闭。')

    target_user_id = _get_event_target_user_id(ev)
    if not target_user_id:
        return await _send_prefixed(bot, '要送给谁？请艾特对方或在命令后面写对方 QQ。')

    giver_id = _user_key(ev)
    if target_user_id == giver_id:
        return await _send_prefixed(bot, '不能把老婆送给自己哦！')

    giver_record = _get_existing_daily_wife_record(ev, giver_id)
    if giver_record is None:
        return await _send_prefixed(bot, '你今天还没有老婆，先去抽一个吧~')
    if giver_record.record_type == 'member':
        return await _send_prefixed(bot, '群友老婆不能送出去哦~')

    data = _load_wife_data()
    context = _get_today_context(data, ev)

    giver_data = context['wives'].get(giver_id)
    # 离手即结算：自己的老婆已被抢走 / 已送出，没有老婆可送
    giver_state = _wife_state(giver_data)
    if giver_state == 'lost_stolen':
        return await _send_prefixed(bot, '你的老婆已经被抢走了，没有老婆可以送了~')
    if giver_state == 'lost_gifted':
        return await _send_prefixed(bot, '你今天已经把老婆送出去了~')
    # 到手即终结：抢来的 / 别人送的老婆不能再送出去
    if _is_secondhand_wife(giver_data):
        return await _send_prefixed(bot, '这个老婆是抢来或别人送的，不能再送出去哦~')

    # 对方仍持有有效老婆时不需要再送（被抢走/送出后变“空”才可接收）
    target_existing = context['wives'].get(target_user_id)
    if _has_active_wife(target_existing):
        return await _send_prefixed(bot, '对方今天已经有老婆了，不需要你送哦~')

    logger.info(f'{LOG_PREFIX} 用户 {giver_id} 把老婆送给了 {target_user_id}')
    context['wives'][target_user_id] = _record_to_dict(giver_record, ev, target_user_id)
    context['wives'][target_user_id]['gifted_from'] = giver_id

    giver_name = _user_display_name(ev, giver_id)
    if isinstance(context['wives'].get(giver_id), dict):
        context['wives'][giver_id]['gifted_to'] = target_user_id
        context['wives'][giver_id]['gifted_to_name'] = _user_display_name(ev, target_user_id)

    _save_wife_data(data)

    role = giver_record.to_role()
    text = _build_gift_success_text(role, target_user_id)
    await _send_role_image(bot, role, giver_record.image, text, giver_id)


async def _send_wife_list(bot: Bot, ev: Event, mode: str = 'wife'):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id} 请求了 {mode} 列表')
    title_text, items = await _wife_list_items(ev, mode)
    if len(items) > LIST_FORWARD_THRESHOLD:
        await _send_prefixed(bot,MessageSegment.node([_wife_list_text_from_items(title_text, items)]))
        return
    await _send_prefixed(bot,_wife_list_text_from_items(title_text, items))


async def _send_create_custom_wife_role(bot: Bot, ev: Event):
    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    role_id, created, error = _create_or_get_custom_role(role_name)
    if error:
        return await _send_prefixed(bot,error)

    if created:
        await _send_prefixed(bot,f'自定义老婆创建成功\n角色ID：{role_id}')
    else:
        await _send_prefixed(bot,f'自定义老婆已存在\n角色ID：{role_id}')


async def _send_upload_custom_wife_images(bot: Bot, ev: Event):
    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    if not role_name:
        return await _send_prefixed(bot,'请输入角色名，例如：老婆上传图片达妮娅 并附带图片')

    image_refs = _upload_image_refs(ev)
    if not image_refs:
        return await _send_prefixed(bot,f'请同时发送图片及命令，例如：老婆上传图片{role_name}')

    role_id, created, error = _create_or_get_custom_role(role_name)
    if error:
        return await _send_prefixed(bot,error)

    role_dir = _writable_role_pile_root() / role_id
    saved: list[Path] = []
    failed = 0
    for index, image_ref in enumerate(image_refs, 1):
        path = await asyncio.to_thread(_save_upload_image_ref, role_dir, role_id, image_ref, index)
        if path is None:
            failed += 1
        else:
            saved.append(path)

    if not saved:
        return await _send_prefixed(bot,f'【{role_name}】上传图片失败，请确认消息里附带的是图片。')

    _invalidate_candidate_cache()
    created_text = '（已自动创建角色）' if created else ''
    success_ids = [_custom_image_hash_id(path) for path in saved]
    msg = [
        f'【{role_name}】上传老婆图片成功{created_text}',
        f'角色ID：{role_id}',
        f'成功：{len(saved)} 张',
        f'图片ID：{", ".join(success_ids)}',
    ]
    if failed:
        msg.append(f'失败：{failed} 张')
    await _send_prefixed(bot,'\n'.join(msg))


async def _send_custom_wife_image_list(bot: Bot, ev: Event):
    role_name = _clean_upload_role_name(ev.text, strip_wife_suffix=True)
    entries = _custom_role_image_entries(role_name)
    if entries is None:
        return await _send_prefixed(bot,'未找到这个自定义老婆，请先使用：老婆创建角色名老婆')

    role_id, role_name, images = entries
    if not images:
        return await _send_prefixed(bot,f'自定义老婆【{role_name}】暂未上传过图片。')

    nodes: list[Any] = []
    for hash_id, path in images:
        nodes.append(f'{role_name} 老婆图片ID：{hash_id}')
        nodes.append(MessageSegment.image(path))
    await _send_prefixed(bot, MessageSegment.node(nodes))


async def _send_request_delete_custom_wife_role(bot: Bot, ev: Event):
    role_name = _clean_upload_role_name(ev.regex_dict.get('role') or ev.text, strip_wife_suffix=True)
    role_id, role_name, images, error = _resolve_custom_role_for_delete(role_name)
    if error:
        return await _send_prefixed(bot, error)

    _set_pending_custom_role_delete(ev, role_id, role_name, len(images))
    await _send_prefixed(
        bot,
        f'将删除自定义老婆【{role_name}】\n'
        f'角色ID：{role_id}\n'
        f'图片数量：{len(images)}\n'
        f'确认删除请在 {CUSTOM_ROLE_DELETE_CONFIRM_SECONDS} 秒内发送：老婆删除确认\n'
        f'取消请发送：老婆删除取消',
    )


async def _send_confirm_delete_custom_wife_role(bot: Bot, ev: Event):
    pending = _get_pending_custom_role_delete(ev)
    if pending is None:
        return await _send_prefixed(bot, '没有待确认删除的自定义老婆。')

    role_id = str(pending.get('role_id') or '')
    role_name = str(pending.get('role_name') or role_id)
    if not role_id:
        _clear_pending_custom_role_delete(ev)
        return await _send_prefixed(bot, '待删除记录无效，请重新发起删除。')

    deleted_count = _delete_custom_role(role_id)
    _clear_pending_custom_role_delete(ev)
    await _send_prefixed(bot, f'已删除自定义老婆【{role_name}】\n角色ID：{role_id}\n删除图片：{deleted_count} 张')


async def _send_cancel_delete_custom_wife_role(bot: Bot, ev: Event):
    if _get_pending_custom_role_delete(ev) is None:
        return await _send_prefixed(bot, '没有待取消的自定义老婆删除。')
    _clear_pending_custom_role_delete(ev)
    await _send_prefixed(bot, '已取消删除自定义老婆。')


async def _send_delete_custom_wife_image(bot: Bot, ev: Event):
    role_name, hash_id = _parse_delete_custom_image_text(ev.text)
    role_id, role_name, image_path, error = _resolve_custom_image_for_delete(role_name, hash_id)
    if error:
        return await _send_prefixed(bot,error)
    if image_path is None:
        return await _send_prefixed(bot,f'【{role_name}】未找到图片ID：{hash_id}')

    try:
        image_path.unlink()
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 删除自定义老婆图片失败: {image_path} -> {exc}')
        return await _send_prefixed(bot,f'【{role_name}】图片删除失败：{hash_id}')

    _invalidate_candidate_cache()
    await _send_prefixed(bot,f'已删除【{role_name}】老婆图片：{hash_id}')


@upload_sv.on_prefix('老婆创建', block=True)
async def custom_wife_create(bot: Bot, ev: Event):
    await _send_create_custom_wife_role(bot, ev)


@upload_sv.on_prefix('老婆上传图片', block=True)
async def custom_wife_upload(bot: Bot, ev: Event):
    await _send_upload_custom_wife_images(bot, ev)


@upload_sv.on_prefix(('老婆图片列表', '老婆图片'), block=True)
async def custom_wife_image_list(bot: Bot, ev: Event):
    await _send_custom_wife_image_list(bot, ev)


@upload_sv.on_prefix(('老婆删除图片', '老婆删图片'), block=True)
async def custom_wife_delete_image(bot: Bot, ev: Event):
    await _send_delete_custom_wife_image(bot, ev)


@upload_sv.on_fullmatch('老婆删除确认', block=True)
async def custom_wife_confirm_delete(bot: Bot, ev: Event):
    await _send_confirm_delete_custom_wife_role(bot, ev)


@upload_sv.on_fullmatch('老婆删除取消', block=True)
async def custom_wife_cancel_delete(bot: Bot, ev: Event):
    await _send_cancel_delete_custom_wife_role(bot, ev)


@upload_sv.on_regex(r'^老婆删除(?!图片|确认|取消)(?P<role>.+)$', block=True)
async def custom_wife_delete_role(bot: Bot, ev: Event):
    await _send_request_delete_custom_wife_role(bot, ev)


@upload_sv.on_fullmatch('下载萝莉图片', block=True)
async def download_loli_images(bot: Bot, ev: Event):
    await _send_download_loli_images(bot, ev)


@upload_sv.on_fullmatch('删除萝莉图片', block=True)
async def delete_loli_images(bot: Bot, ev: Event):
    await _send_delete_loli_images(bot, ev)


@sv.on_fullmatch('今日老婆帮助', block=True)
async def daily_wife_help(bot: Bot, ev: Event):
    if not HELP_IMAGE_PATH.is_file():
        logger.warning(f'{LOG_PREFIX} 帮助图片不存在: {HELP_IMAGE_PATH}')
        return await bot.send('帮助图片缺失，请联系管理员。')
    await bot.send(MessageSegment.image(HELP_IMAGE_PATH))


@sv.on_fullmatch(('老婆更新记录', '今日老婆更新记录', '老婆更新日志'), block=True)
async def daily_wife_update_log(bot: Bot, ev: Event):
    await _send_github_update_log(bot, ev)


@sv.on_fullmatch('今日萝莉', block=True)
async def daily_loli(bot: Bot, ev: Event):
    await _send_loli_image(bot, ev)


@sv.on_fullmatch('今日正太', block=True)
async def daily_shota(bot: Bot, ev: Event):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 触发今日正太命令')
    await _send_shota_text(bot, '今日正太功能正在完善，敬请期待~')


@sv.on_fullmatch('今日御姐', block=True)
async def daily_yujie(bot: Bot, ev: Event):
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 触发今日御姐命令')
    await _send_yujie_text(bot, '今日御姐功能正在完善，敬请期待~')


@sv.on_prefix(('今日老婆', '娶婆娘', 'jrlp', 'qlp'), block=True)
async def daily_wife_prefix(bot: Bot, ev: Event):
    specified_name = str(ev.text or '').strip()
    if specified_name == '列表':
        return await _send_wife_list(bot, ev, mode='wife')
    await _send_daily_wife(bot, ev, mode='wife', specified_name=specified_name)


@sv.on_fullmatch(('今日老婆', '娶婆娘', 'jrlp', 'qlp'), block=True)
async def daily_wife_full(bot: Bot, ev: Event):
    await _send_daily_wife(bot, ev, mode='wife', specified_name='')


@sv.on_fullmatch(('老婆列表', '今日老婆列表'), block=True)
async def daily_wife_list(bot: Bot, ev: Event):
    await _send_wife_list(bot, ev)


@sv.on_prefix('今日老公', block=True)
async def daily_husband_prefix(bot: Bot, ev: Event):
    if not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message())
    specified_name = str(ev.text or '').strip()
    await _send_daily_wife(bot, ev, mode='husband', specified_name=specified_name)


@sv.on_fullmatch('今日老公', block=True)
async def daily_husband_full(bot: Bot, ev: Event):
    if not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message())
    await _send_daily_wife(bot, ev, mode='husband', specified_name='')


@sv.on_fullmatch(('老公列表', '今日老公列表'), block=True)
async def daily_husband_list(bot: Bot, ev: Event):
    if not _husband_available():
        return await _send_prefixed(bot, _husband_unavailable_message())
    await _send_wife_list(bot, ev, mode='husband')


@sv.on_fullmatch('娶群友', block=True)
async def group_member_wife(bot: Bot, ev: Event):
    await _send_group_member_wife(bot, ev)


@sv.on_prefix(('抢老婆', '抢今日老婆', '抢婆娘'), block=True)
async def rob_wife(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)


@sv.on_fullmatch(('抢老婆', '抢今日老婆', '抢婆娘'), block=True)
async def rob_wife_at(bot: Bot, ev: Event):
    await _send_rob_wife(bot, ev)


@sv.on_prefix(('送老婆', '送今日老婆'), block=True)
async def gift_wife(bot: Bot, ev: Event):
    await _send_gift_wife(bot, ev)


@sv.on_fullmatch(('送老婆', '送今日老婆'), block=True)
async def gift_wife_at(bot: Bot, ev: Event):
    await _send_gift_wife(bot, ev)


# 注册到 GsCore 帮助一览页（core帮助）
# icon 必须是带 alpha 的方形小图标，否则核心合成帮助图时会报 bad transparency mask
if HELP_ICON_PATH.is_file():
    try:
        with Image.open(HELP_ICON_PATH) as _help_icon:
            register_help('TodayWaifu', '今日老婆帮助', _help_icon.convert('RGBA'))
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 注册插件帮助失败: {exc}')