from __future__ import annotations

import shutil
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


def user_role_quotes_path() -> Path:
    """用户 data 目录里的台词库；要增补其他游戏角色就改这个文件。"""
    return data_root() / ROLE_QUOTES_FILE_NAME


def role_quotes_path() -> Path:
    """优先用户 data 目录的台词库，缺失时回退到插件内置的那份。"""
    user_path = user_role_quotes_path()
    if user_path.is_file():
        return user_path
    return BUNDLED_ROLE_QUOTES_PATH


def ensure_role_quotes_seeded() -> bool:
    """首次运行把内置台词库播种到 data 目录，方便用户自行增补；已存在则不动。"""
    target = user_role_quotes_path()
    if target.is_file() or not BUNDLED_ROLE_QUOTES_PATH.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUNDLED_ROLE_QUOTES_PATH, target)
    return True
