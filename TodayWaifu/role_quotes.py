"""TodayWaifu 角色剧情与对话台词模块，从 data/TodayWaifu/role_quotes.json 读取。"""

from __future__ import annotations

import json
import random
from typing import Tuple

from .resource_paths import role_quotes_path

_QUOTES_CACHE: dict[str, tuple[str, ...]] = {}
_DEFAULT_QUOTES_CACHE: tuple[str, ...] = ()
_CACHE_MTIME: float = 0.0


def _load_quotes_from_data() -> Tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    global _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE, _CACHE_MTIME
    path = role_quotes_path()
    if not path.is_file():
        return {}, ()

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    if _QUOTES_CACHE and mtime == _CACHE_MTIME:
        return _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE

    if not isinstance(data, dict):
        return _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE

    raw_quotes = data.get("role_quotes")
    raw_default = data.get("default_quotes")

    quotes: dict[str, tuple[str, ...]] = {}
    if isinstance(raw_quotes, dict):
        for k, v in raw_quotes.items():
            if isinstance(v, (list, tuple)):
                quotes[str(k)] = tuple(str(x) for x in v if str(x).strip())

    defaults: tuple[str, ...] = ()
    if isinstance(raw_default, (list, tuple)):
        defaults = tuple(str(x) for x in raw_default if str(x).strip())

    _QUOTES_CACHE = quotes
    _DEFAULT_QUOTES_CACHE = defaults
    _CACHE_MTIME = mtime
    return _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE


def get_role_quote(name: str) -> str:
    """获取角色的剧情/对话文本，附带角色名，文本内容不超过 20 字。"""
    clean_name = name.strip()
    role_quotes, default_quotes = _load_quotes_from_data()

    quotes: tuple[str, ...] | None = role_quotes.get(clean_name)
    matched_name = clean_name
    if quotes is None:
        for k, v in role_quotes.items():
            if k in clean_name or clean_name in k:
                quotes = v
                matched_name = k
                break
    if quotes is None:
        quotes = default_quotes
        matched_name = clean_name

    if not quotes:
        return ""

    selected = random.choice(quotes)
    if len(selected) > 20:
        selected = selected[:20]

    quote_line = f"「{selected}」"
    author_line = f"——{matched_name}"

    target_width = max(len(quote_line), 18)
    author_width = len(author_line)
    spaces = "\u3000" * max(0, target_width - author_width)
    return f"{quote_line}\n{spaces}{author_line}"
