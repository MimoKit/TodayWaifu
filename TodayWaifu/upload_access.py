from __future__ import annotations

from collections.abc import Iterable


def normalized_user_ids(values: object) -> frozenset[str]:
    if isinstance(values, str):
        items: Iterable[object] = values.replace(',', ' ').split()
    elif isinstance(values, (list, tuple, set, frozenset)):
        items = values
    else:
        return frozenset()
    return frozenset(text for value in items if (text := str(value).strip()))


def can_use_whitelisted_feature(
    user_id: str | int,
    master_ids: object,
    whitelist_ids: object,
) -> bool:
    """主人或白名单用户可用；master_ids 留空时由调用方另行判断主人身份。"""
    normalized_user_id = str(user_id).strip()
    return (
        normalized_user_id in normalized_user_ids(master_ids)
        or normalized_user_id in normalized_user_ids(whitelist_ids)
    )


def can_upload_images(
    user_id: str | int,
    master_ids: object,
    whitelist_ids: object,
) -> bool:
    return can_use_whitelisted_feature(user_id, master_ids, whitelist_ids)

