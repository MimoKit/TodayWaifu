"""TodayWaifu daily context and record state helpers."""
from __future__ import annotations

from typing import Any

from .kind_metadata import daily_kind_metadata

DAILY_WIFE_KINDS = ('wife', 'nte', 'pgr')
ALL_DAILY_RECORD_KINDS = ('wife', 'nte', 'pgr', 'husband', 'loli')


def daily_bucket_name(kind: str) -> str:
    return daily_kind_metadata(kind).bucket


def daily_item_title(kind: str) -> str:
    return daily_kind_metadata(kind).title


def daily_kind_metadata_for(kind: str):
    return daily_kind_metadata(kind)


def wife_state(raw: Any) -> str:
    if not isinstance(raw, dict):
        return 'empty'
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
    return wife_origin(raw) in {'robbed', 'gifted'}


def has_active_wife(raw: Any) -> bool:
    return isinstance(raw, dict) and bool(raw.get('name')) and wife_state(raw) == 'owned'
