"""TodayWaifu 领域状态与记录转换策略。"""
from __future__ import annotations

from typing import Any

from .domain import WifeRecord
from .kind_metadata import DailyKindMetadata, daily_kind_metadata

DAILY_WIFE_KINDS = ('wife', 'nte', 'pgr')
ALL_DAILY_RECORD_KINDS = ('wife', 'nte', 'pgr', 'husband', 'loli')


def daily_item_title(kind: str) -> str:
    return daily_kind_metadata(kind).title


def daily_kind(kind: str) -> DailyKindMetadata:
    return daily_kind_metadata(kind)


def daily_bucket_name(kind: str) -> str:
    return daily_kind_metadata(kind).bucket


def wife_state(raw: Any) -> str:
    if not isinstance(raw, dict):
        return 'owned'
    if raw.get('divorced'):
        return 'divorced'
    if raw.get('stolen_by'):
        return 'lost_stolen'
    if raw.get('gifted_to'):
        return 'lost_gifted'
    return 'owned'


def wife_origin(raw: Any) -> str:
    if not isinstance(raw, dict):
        return 'self'
    if raw.get('stolen_from'):
        return 'robbed'
    if raw.get('gifted_from'):
        return 'gifted'
    if raw.get('safe'):
        return 'safe'
    return 'self'


def is_secondhand_wife(raw: Any) -> bool:
    return wife_origin(raw) in ('robbed', 'gifted', 'safe')


def has_active_wife(raw: Any) -> bool:
    return isinstance(raw, dict) and bool(raw.get('name')) and wife_state(raw) == 'owned'


def record_to_dict(
    record: WifeRecord,
    *,
    user_id: str,
    display_name: str,
    group_id: str,
    bot_id: str,
    day: str,
    updated_at: int,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        'name': record.name,
        'role_ids': list(record.role_ids),
        'image': record.image,
        'record_type': record.record_type,
        'user_id': user_id,
        'display_name': display_name,
        'group_id': group_id,
        'bot_id': bot_id,
        'day': day,
        'updated_at': updated_at,
    }
    if record.target_user_id:
        data['target_user_id'] = record.target_user_id
    return data
