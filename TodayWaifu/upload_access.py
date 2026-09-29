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


def can_use_pm_or_whitelisted_feature(
    user_id: str | int,
    user_pm: int | str,
    required_pm: int | str,
    master_ids: object,
    whitelist_ids: object,
) -> bool:
    """允许达到当前服务权限，或命中主人/白名单。"""
    if can_use_whitelisted_feature(user_id, master_ids, whitelist_ids):
        return True
    try:
        return int(user_pm) <= int(required_pm)
    except (TypeError, ValueError):
        return False


def can_upload_images(
    user_id: str | int,
    master_ids: object,
    whitelist_ids: object,
) -> bool:
    return can_use_whitelisted_feature(user_id, master_ids, whitelist_ids)

