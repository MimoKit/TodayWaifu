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
        r'\b(\d{5,40})\b',
        # QQ 官方机器人等平台无数字 QQ 号，使用 openid（形如 16 位以上十六进制）
        r'\b([0-9A-Fa-f]{16,})\b',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def _self_user_ids(ev: Event) -> set[str]:
    # 平台把机器人自身上报为 openid/实例名时，@机器人 需被排除避免误认目标
    ids: set[str] = set()
    for attr in ('bot_self_id', 'self_id', 'real_bot_id'):
        value = getattr(ev, attr, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            ids.add(text)
    return ids


def _iter_event_messages(ev: Event) -> Iterator[object]:
    """遍历事件中承载消息段的字段。

    仅 `content` 是消息段列表：Core 的 Event/MessageReceive 字段固定（msgspec Struct），
    `ev.message` / `ev.original_message` 等属性并不存在（见 Core 的 Event 字段说明）。
    """
    for item in ev.content or ():
        yield item


def _get_event_target_user_id(ev: Event) -> str | None:
    """解析"抢/送老婆"等命令的目标用户，兼容 @、富文本与纯文本三种上报形态。"""
    self_ids = _self_user_ids(ev)
    for value in (ev.at_list, ev.at):
        if value is not None:
            if isinstance(value, (list, tuple, set)):
                value = next(iter(value), None)

            user_id = _normalise_target_user_id(value)
            if user_id:
                if 'CQ:at' in user_id or '<at' in user_id:
                    parsed = _target_user_id_from_text(user_id)
                    if parsed and parsed not in self_ids:
                        return parsed
                    continue
                if user_id in self_ids:
                    continue
                return user_id

    for item in _iter_event_messages(ev):
        if isinstance(item, Message) and item.type in {'at', 'mention_user', 'mention'}:
            user_id = _normalise_target_user_id(item.data)
            if user_id and user_id not in self_ids:
                return user_id
        if isinstance(item, dict) and item.get('type') in {'at', 'mention_user', 'mention'}:
            user_id = _normalise_target_user_id(item.get('data'))
            if user_id and user_id not in self_ids:
                return user_id

    for text in (ev.text, ev.raw_text):
        if text:
            user_id = _target_user_id_from_text(str(text))
            if user_id and user_id not in self_ids:
                return user_id

    return None
