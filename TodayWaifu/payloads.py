"""TodayWaifu 的持久化 JSON 与远程接口载荷类型。

这些结构来自磁盘上的旧版 JSON、数据库 `payload` 列与远程图库接口，属于不可信
外部输入。把它们显式写成 TypedDict，类型检查器与读者都能追踪字段，无需 `Any`。
"""
from __future__ import annotations

from typing import Union, TypedDict

from gsuid_core.models import Message

# `Bot.send` / `target_send` 接受的出站消息形态（与框架签名保持一致）。
SendMessage = Union[Message, list[Message], str, bytes, list[str]]

# GsCore 配置项 `.data` 的全部可能类型（见 gsuid_core/utils/plugins_config/models.py）。
ConfigValue = Union[str, bool, int, float, list[str], list[int], dict[str, list[str]], None]


class RoleRecordValue(TypedDict, total=False):
    """单条老婆/老公记录，对应旧 JSON 里的记录值与数据库 `payload` 反序列化结果。"""

    name: str
    role_ids: list[str]
    image: str
    record_type: str
    target_user_id: str
    display_name: str
    updated_at: int
    created_at: int
    divorced: bool
    divorced_at: int
    stolen_by: str
    stolen_by_name: str
    stolen_from: str
    gifted_to: str
    gifted_to_name: str
    gifted_from: str
    safe: bool


# 桶名（wives / husbands / lolis / ...）→ 用户键 → 记录
RecordBucket = dict[str, RoleRecordValue]
# 上下文键（群/私聊）→ 桶
DailyContext = dict[str, RecordBucket]
# 日期 → 上下文
DailyDay = dict[str, DailyContext]


class WifeData(TypedDict, total=False):
    """`daily_wife_data.json` / 内存每日数据的顶层结构。"""

    days: dict[str, DailyDay]


class GalleryImageEntry(TypedDict, total=False):
    """图库接口里一张图片的描述。"""

    url: str


class GalleryRoleEntry(TypedDict, total=False):
    """图库接口里一个角色的描述。"""

    role_ids: list[str]
    images: list[GalleryImageEntry | str]


class GalleryPayload(TypedDict, total=False):
    """远程图库接口的响应体。"""

    roles: list[GalleryRoleEntry]


class RoleAccumulator(TypedDict):
    """角色候选归并中间态（按角色名聚合，无需携带 name）。"""

    role_ids: list[str]
    images: list[str]


class NamedRoleAccumulator(TypedDict):
    """角色候选归并中间态（按归一化名聚合，需保留展示用 name）。"""

    name: str
    role_ids: list[str]
    images: list[str]


class CustomRoleEntry(TypedDict, total=False):
    """自定义老婆条目（对角表 + 图片集合）。"""

    role_id: str
    role_name: str
    images: list[tuple[str, str]]


class PendingGift(TypedDict, total=False):
    """待确认的送老婆请求。"""

    created_at: float
    target_user_id: str
    kind: str


class PendingCustomRoleDelete(TypedDict, total=False):
    """待确认的自定义老婆删除请求。"""

    created_at: float
    role_id: str
    role_name: str
