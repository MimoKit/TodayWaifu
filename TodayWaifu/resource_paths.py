from __future__ import annotations

from pathlib import Path

from gsuid_core.data_store import get_res_path

BASE_DIR = Path(__file__).parent.parent
ROLE_MAP_JSON_PATH = BASE_DIR / 'role_id_map.json'
LEGACY_ROLE_MAP_PATH = BASE_DIR / 'role_id_map.txt'
HELP_ICON_PATH = BASE_DIR / 'ICON.png'
PGR_WIFE_DIR_NAME = 'pgr_wife'
LOLI_IMAGE_DIR_NAME = 'loli_images'
ROLE_QUOTES_FILE_NAME = 'role_quotes.json'
# 随插件分发的内置台词库（含鸣潮、异环、战双角色），与 ICON.png / role_id_map.json 同级
BUNDLED_ROLE_QUOTES_PATH = BASE_DIR / ROLE_QUOTES_FILE_NAME


def data_root() -> Path:
    return get_res_path('TodayWaifu')


def role_upload_map() -> Path:
    return data_root() / 'custom_role_map.json'


def role_upload_root() -> Path:
    return data_root() / 'custom_role_pile'


def pgr_root() -> Path:
    return data_root() / PGR_WIFE_DIR_NAME


def loli_root() -> Path:
    return data_root() / LOLI_IMAGE_DIR_NAME


def role_quotes_path() -> Path:
    """只读随插件分发的台词库。

    曾把内置库播种到 data 并优先读那份，结果是升级后的新库永远被旧副本挡住
    （#27 播种的自创台词一直压过 #30 的官方原文），故不再读 data 副本。
    """
    return BUNDLED_ROLE_QUOTES_PATH
