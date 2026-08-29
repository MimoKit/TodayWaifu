"""Pure group-member display and probability helpers."""
from __future__ import annotations

from typing import Any


def valid_display_name(value: Any, user_id: str | int | None = None) -> str:
    text = str(value or '').strip()
    if not text or (user_id is not None and text == str(user_id)):
        return ''
    return text[:64]


def valid_member_text(value: Any) -> str:
    return str(value or '').strip()[:200]


def member_probability(value: Any, default: float = 0.1) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def qq_avatar_url(user_id: str) -> str:
    return f'https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640'
