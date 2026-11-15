from __future__ import annotations

from collections.abc import Iterable


def normalized_user_ids(values: object) -> frozenset[str]:
    # 入参可能是配置系统给出的任意结构（字符串、列表或 None），此处统一归一为字符串
    # 集合：比较时按去空白后的精确值匹配，避免 "123 " 与 "123" 被当成两个用户。
    # 非法类型返回空集合而非抛错，使配置写错时表现为「无权限」而不是命令报错中断。
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
    """主人或白名单用户可用；master_ids 留空时由调用方另行判断主人身份。

    主人集合为空并不等于拒绝：部分部署把主人身份交给 Core 的统一鉴权，本函数只负责
    白名单与显式主人名单的匹配，故返回 False 时调用方仍需自行判断。
    """
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
    # 独立入口而非让调用方直接调 can_use_whitelisted_feature：上传策略将来若与
    # 白名单策略分叉，调用方无需改动，语义也不会被误用成「通用权限判断」。
    return can_use_whitelisted_feature(user_id, master_ids, whitelist_ids)
