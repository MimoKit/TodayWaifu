"""Pure role-name filtering helpers for TodayWaifu."""
from __future__ import annotations

import re


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
NTE_EXCLUDED_ROLE_NAMES = {'翳', '埃德嘉', '阿德勒', '卡厄斯'}
NTE_EXCLUDED_ROLE_KEYWORDS = ('异能者·零', '异能者零', '男主', '女主')


def normalize_role_name(name: str) -> str:
    return re.sub(r'\s+', '', str(name)).casefold()


_MALE_ROLE_NAMES_NORM = {normalize_role_name(name) for name in EXCLUDED_ROLE_NAMES}


def is_male_role(name: str) -> bool:
    return normalize_role_name(name) in _MALE_ROLE_NAMES_NORM


def is_excluded_role(name: str) -> bool:
    normalized = normalize_role_name(name)
    return normalized in _MALE_ROLE_NAMES_NORM or any(
        normalize_role_name(keyword) in normalized for keyword in EXCLUDED_ROLE_KEYWORDS
    )


def is_excluded_nte_role(name: str) -> bool:
    normalized = normalize_role_name(name)
    excluded = {normalize_role_name(item) for item in NTE_EXCLUDED_ROLE_NAMES}
    return normalized in excluded or any(
        normalize_role_name(keyword) in normalized for keyword in NTE_EXCLUDED_ROLE_KEYWORDS
    )
