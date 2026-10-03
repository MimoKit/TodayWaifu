"""鸣潮角色别名：默认关闭，开启后优先用 XutheringWavesUID 的别名表。

配置项 DailyWifeAliasSource 决定来源，默认 off 即不做别名解析：
- xwuid：用 XWUID 的两份表（data/XutheringWavesUID/resource/map/alias/char_alias.json 内置表
  + alias/char_alias.json 用户自定义表，按 XWUID 的语义合并取并集）；该插件未安装时
  自动改用插件根随包分发的 role_aliases.json
- local：始终用插件自带的别名表

只做精确匹配，不用子串模糊匹配——子串匹配会让单字角色名（如「心」）误命中「鉴心」。
"""

from __future__ import annotations

import json
from pathlib import Path

from gsuid_core.data_store import get_res_path

from .constants import _cfg
from .resource_paths import BASE_DIR

ROLE_ALIAS_FILE_NAME = "role_aliases.json"
ALIAS_SOURCE_OFF = "off"
ALIAS_SOURCE_XWUID = "xwuid"
ALIAS_SOURCE_LOCAL = "local"

# 别名 -> 标准角色名；来源或文件 mtime 变化时重建
_ALIAS_INDEX: dict[str, str] = {}
_ALIAS_SIGNATURE: tuple[float, ...] = ()


def bundled_alias_path() -> Path:
    """随插件分发的别名表，用户没装 XWUID 时靠它提供基础别名。"""
    return BASE_DIR / ROLE_ALIAS_FILE_NAME


def _xwuid_alias_paths() -> tuple[Path, ...]:
    root = get_res_path("XutheringWavesUID")
    return (
        root / "resource" / "map" / "alias" / "char_alias.json",
        root / "alias" / "char_alias.json",
    )


def alias_source() -> str:
    """当前实际生效的来源：off 关闭解析；xwuid 的表不可用时回退 local。"""
    configured = str(_cfg("DailyWifeAliasSource") or ALIAS_SOURCE_OFF).strip().lower()
    if configured == ALIAS_SOURCE_LOCAL:
        return ALIAS_SOURCE_LOCAL
    if configured == ALIAS_SOURCE_XWUID:
        if any(path.is_file() for path in _xwuid_alias_paths()):
            return ALIAS_SOURCE_XWUID
        return ALIAS_SOURCE_LOCAL
    return ALIAS_SOURCE_OFF


def _alias_source_paths() -> tuple[Path, ...]:
    """按配置与可用性返回要读取的别名表。"""
    if alias_source() == ALIAS_SOURCE_LOCAL:
        return (bundled_alias_path(),)
    return _xwuid_alias_paths()


def _path_signature(paths: tuple[Path, ...]) -> tuple[float, ...]:
    return tuple(path.stat().st_mtime if path.is_file() else 0.0 for path in paths)


def _merge_into(index: dict[str, str], standard: str, aliases: object) -> None:
    index.setdefault(standard, standard)
    if not isinstance(aliases, list):
        return
    for alias in aliases:
        text = str(alias).strip()
        # 标准名优先：其它角色的别名不得覆盖已成形的标准名
        if text and text != standard and index.get(text, standard) == standard:
            index[text] = standard


def _load_alias_index() -> dict[str, str]:
    """合并插件自带与 XWUID 的别名表；按 mtime 缓存，文件缺失时跳过。"""
    global _ALIAS_INDEX, _ALIAS_SIGNATURE
    paths = _alias_source_paths()
    signature = _path_signature(paths)
    if _ALIAS_INDEX and signature == _ALIAS_SIGNATURE:
        return _ALIAS_INDEX

    index: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw, dict):
            continue
        # 先收标准名，再挂别名，保证标准名不会被别的角色的别名顶掉
        for standard, aliases in raw.items():
            _merge_into(index, str(standard).strip(), aliases)

    _ALIAS_INDEX = index
    _ALIAS_SIGNATURE = signature
    return _ALIAS_INDEX


def resolve_role_name(name: str) -> str:
    """把别名解析为标准角色名；关闭别名或解析不到时原样返回。"""
    clean = name.strip()
    if not clean or alias_source() == ALIAS_SOURCE_OFF:
        return clean
    return _load_alias_index().get(clean, clean)
