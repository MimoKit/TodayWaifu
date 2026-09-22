from __future__ import annotations

from pathlib import Path

from gsuid_core.data_store import get_res_path


BASE_DIR = Path(__file__).parent.parent
WIFE_ROLE_MAP_PATH = BASE_DIR / 'wife_role_id_map.txt'
HUSBAND_ROLE_MAP_PATH = BASE_DIR / 'husband_role_id_map.txt'
NTE_ROLE_MAP_PATH = BASE_DIR / 'nte_role_id_map.txt'
LEGACY_ROLE_MAP_PATH = BASE_DIR / 'role_id_map.txt'
HELP_ICON_PATH = BASE_DIR / 'ICON.png'
PGR_WIFE_DIR_NAME = 'pgr_wife'
LOLI_IMAGE_DIR_NAME = 'loli_images'


def data_root() -> Path:
    return get_res_path('TodayWaifu')


def role_upload_map() -> Path:
    return data_root() / 'custom_role_map.txt'


def role_upload_root() -> Path:
    return data_root() / 'custom_role_pile'


def pgr_root() -> Path:
    return data_root() / PGR_WIFE_DIR_NAME


def loli_root() -> Path:
    return data_root() / LOLI_IMAGE_DIR_NAME
