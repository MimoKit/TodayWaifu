"""Target-user parsing helpers shared by interaction commands."""
from __future__ import annotations

import re
from typing import Any, Iterator

from gsuid_core.models import Message


def normalise_target_user_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ''
    if isinstance(value, Message):
        value = value.data
    if isinstance(value, dict):
        for field in ('user_id', 'qq', 'openid', 'open_id', 'id', 'data'):
            user_id = normalise_target_user_id(value.get(field))
            if user_id:
                return user_id
        return ''
    text = str(value).strip()
    if not text or text.lower() in {'none', 'true', 'false', 'all'}:
        return ''
    return text


def target_user_id_from_text(text: str) -> str | None:
    text = str(text or '').strip()
    if not text:
        return None
    patterns = (
        r'\[CQ:at,[^\]]*qq=([0-9A-Za-z_-]{5,})',
        r'<at[^>]*(?:id|qq|user_id)=["\']?([0-9A-Za-z_-]{5,})',
        r'(?:qq=|qq:|QQ=|QQ:|@)\s*([0-9A-Za-z_-]{5,})',
        r'\b(\d{5,20})\b',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def iter_event_messages(ev: Any) -> Iterator[Any]:
    for attr in ('content', 'message', 'original_message', 'node'):
        value = getattr(ev, attr, None)
        if isinstance(value, (list, tuple)):
            yield from value
        elif value is not None:
            yield value
