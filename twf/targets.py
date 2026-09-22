"""TodayWaifu 的目标用户解析（@ / 富文本 / 纯文本三种上报形态）。"""
from __future__ import annotations

import re
from typing import Iterator

from gsuid_core.models import Event, Message

def _normalise_target_user_id(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return ''
    if isinstance(value, Message):
        value = value.data
    if isinstance(value, dict):
        for field in ('user_id', 'qq', 'openid', 'open_id', 'id', 'data'):
            user_id = _normalise_target_user_id(value.get(field))
            if user_id:
                return user_id
        return ''
    text = str(value).strip()
    if not text or text.lower() in {'none', 'true', 'false', 'all'}:
        return ''
    return text


def _target_user_id_from_text(text: str) -> str | None:
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


def _iter_event_messages(ev: Event) -> Iterator[object]:
    """遍历事件中承载消息段的字段。

    仅 `content` 是消息段列表：Core 的 Event/MessageReceive 字段固定（msgspec Struct），
    `ev.message` / `ev.original_message` 等属性并不存在（见 Core 的 Event 字段说明）。
    """
    for item in ev.content or ():
        yield item


def _get_event_target_user_id(ev: Event) -> str | None:
    """解析"抢/送老婆"等命令的目标用户，兼容 @、富文本与纯文本三种上报形态。"""
    for value in (ev.at_list, ev.at):
        if value is not None:
            if isinstance(value, (list, tuple, set)):
                value = next(iter(value), None)

            user_id = _normalise_target_user_id(value)
            if user_id:
                if 'CQ:at' in user_id or '<at' in user_id:
                    parsed = _target_user_id_from_text(user_id)
                    if parsed:
                        return parsed
                    continue
                return user_id

    for item in _iter_event_messages(ev):
        if isinstance(item, Message) and item.type in {'at', 'mention_user', 'mention'}:
            user_id = _normalise_target_user_id(item.data)
            if user_id:
                return user_id
        if isinstance(item, dict) and item.get('type') in {'at', 'mention_user', 'mention'}:
            user_id = _normalise_target_user_id(item.get('data'))
            if user_id:
                return user_id

    for text in (ev.text, ev.raw_text):
        if text:
            user_id = _target_user_id_from_text(str(text))
            if user_id:
                return user_id

    return None
