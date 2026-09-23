"""TodayWaifu 的模块级常量与配置读取助手（最底层，不依赖其它新拆分模块）。"""
from __future__ import annotations

import re
from pathlib import Path

from .payloads import ConfigValue
from .kind_metadata import DailyKindMetadata, daily_kind_metadata
from ..daily_wife_config import DailyWifeConfig

BASE_DIR = Path(__file__).parent.parent


# 内置角色对照表：单文件按模式分节，替代旧版 wife/husband/nte_role_id_map.txt
ROLE_MAP_JSON_PATH = BASE_DIR / 'role_id_map.json'


LEGACY_ROLE_MAP_PATH = BASE_DIR / 'role_id_map.txt'


HELP_ICON_PATH = BASE_DIR / 'ICON.png'


DEFAULT_GALLERY_API_URL = 'https://img.mimokit.dpdns.org/api/xwuid/roles'


NTE_DETAIL_CDN_BASE = 'https://webstatic.tajiduo.com/bbs/yh-game-records-web-source/character/detail'


PGR_WIFE_DIR_NAME = 'pgr_wife'


CACHE_TTL_SECONDS = 300


MEMBER_AVATAR_CACHE_SECONDS = 7 * 24 * 60 * 60


CACHE_MAINTENANCE_INTERVAL_SECONDS = 60 * 60


CACHE_MAINTENANCE_FILE_LIMIT = 1000


MAX_GALLERY_RESPONSE_BYTES = 2 * 1024 * 1024


MAX_IMAGE_RESPONSE_BYTES = 10 * 1024 * 1024


# 单次命令等待图库图片的上限。命令协程会一直占着 Core 的命令并发额度
# （CommandSemaphore），等太久会让 bot 的 _process 停止消费队列，拖死整个 Core。
# 超时只放弃等待，底层下载继续跑完并写盘，下次请求直接命中缓存。
IMAGE_ACQUIRE_TIMEOUT_SECONDS = 6.0


# 远程请求策略：重试次数越少越好。原来的 retries=3 + 固定 5 秒间隔会把一次失败
# 放大成 4 倍请求量，且最坏占用 95 秒，是零点高峰的主要放大器。
HTTP_RETRIES = 1


GALLERY_HTTP_TIMEOUT_SECONDS = 8


IMAGE_HTTP_TIMEOUT_SECONDS = 8


# 指数退避 + 抖动：避免所有失败请求在同一时刻一起重试形成同步脉冲
RETRY_BASE_DELAY_SECONDS = 1.0


RETRY_MAX_DELAY_SECONDS = 4.0


RETRY_JITTER_SECONDS = 0.5


# 连续失败达到阈值后熔断，冷却期内直接快速失败、不打网络
CIRCUIT_FAILURE_THRESHOLD = 5


CIRCUIT_COOLDOWN_SECONDS = 30.0


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


LOG_PREFIX = '[鸣潮今日老婆]'


LOLI_DOWNLOAD_LOG_PREFIX = '[今日萝莉下载]'


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


def _cfg(key: str) -> ConfigValue:
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


def _daily_item_title(kind: str) -> str:
    return _daily_kind_metadata(kind).title


def _daily_kind_metadata(kind: str) -> DailyKindMetadata:
    return daily_kind_metadata(kind)


def _daily_bucket_name(kind: str) -> str:
    return _daily_kind_metadata(kind).bucket


DAILY_WIFE_KINDS = ('wife', 'nte', 'pgr')


ALL_DAILY_RECORD_KINDS = ('wife', 'nte', 'pgr', 'husband', 'loli', 'shota', 'normal')
