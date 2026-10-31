"""QQ 官方素材图床的纯决策逻辑：配额腾挪、复用判定、文件命名。

QQ 开放平台的「素材管理」给每条 Markdown 提供 ``qqbot-media://`` 图片引用，但单机器人
最多 100 张，且按「最后一次被消息引用」滑动过期 90 天。因此判断「还能不能复用」「该删
哪几张」成为正确性问题，而不是优化：删错会把仍在用的图删掉，不删就会在上传时被平台以
40035007 拒绝。

本模块只依赖标准库，可独立加载（测试用 importlib 直接加载），不碰网络与数据库。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

# 平台硬上限：单机器人 100 张有效素材，超出后 prepare/complete 直接返回 40035007。
PLATFORM_MEDIA_LIMIT = 100

# 平台侧的滑动有效期：每次被消息引用后顺延 90 天。
PLATFORM_MEDIA_TTL_SECONDS = 90 * 24 * 60 * 60

# 复用判定的安全余量：距过期不足一天就不再复用，宁可重新上传。
# 留余量是因为本地时间与平台时间存在偏差，且上传与发送之间还有延迟。
REUSE_SAFETY_SECONDS = 24 * 60 * 60

# 平台接受的图片格式（扩展名必须与 Content-Type 一致）。
SUPPORTED_EXTENSIONS = frozenset({'jpg', 'jpeg', 'png', 'gif', 'webp'})


def as_int(value: object, default: int) -> int:
    """把配置/接口返回的松散数字收敛为 int。

    WebConsole 的整数项可能是 int、字符串（``"10"``）或浮点；接口的 ``expires_in``
    文档里就明确可能以字符串返回，故统一在此收口，而不是在每个调用点各写一遍。
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def supported_extension(extension: str) -> bool:
    """该扩展名能否上传到素材库。"""
    return extension.strip().lower() in SUPPORTED_EXTENSIONS


def is_enabled(value: object) -> bool:
    """按宽松语义解析布尔开关；WebConsole 可能回传 bool / int / 字符串。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {'true', '1', 'yes', 'y', 'on', 'enable', 'enabled', '开启'}:
            return True
        if text in {'false', '0', 'no', 'n', 'off', 'disable', 'disabled', '关闭'}:
            return False
    return False


def clamp_quota(quota: object, reserve: object) -> tuple[int, int]:
    """把「数量上限 / 预留张数」收敛到平台允许的区间，返回 (上限, 预留)。

    上限超过 100 时按 100 生效——平台会直接拒绝，配置写得再大也没用；预留必须小于上限，
    否则永远腾不出位置，上传必然失败。
    """
    limit = max(1, min(as_int(quota, PLATFORM_MEDIA_LIMIT), PLATFORM_MEDIA_LIMIT))
    kept = max(0, min(as_int(reserve, 0), limit - 1))
    return limit, kept


def should_reuse(expires_at: object, now: int, safety: int = REUSE_SAFETY_SECONDS) -> bool:
    """本地台账记录的素材是否还能继续引用。

    `expires_at` 取平台返回的过期时间：值不可解析时一律视为不可复用，避免拿一个可能
    已失效的引用去发消息（平台会返回 40035009，用户看到的是空图）。
    """
    expire = as_int(expires_at, 0)
    if expire <= 0:
        return False
    return expire - int(now) > int(safety)


def plan_evictions(
    images: Sequence[Mapping[str, Any]],
    limit: int,
    reserve: int,
    extra_needed: int = 1,
) -> list[str]:
    """按「最久未被引用」挑出要删除的 image_id，为本次上传腾位置。

    `images` 是平台查询接口返回的有效素材列表。平台的 ``expires_at`` 会在每次被消息
    引用后顺延到 90 天后，因此升序排列即为「最久没被引用」；删它们对在线消息无影响
    （已发出的消息在发送时平台已转存副本）。
    """
    keep = max(0, int(limit) - max(0, int(reserve)) - max(1, int(extra_needed)))
    excess = len(images) - keep
    if excess <= 0:
        return []
    ordered = sorted(images, key=lambda item: as_int(item.get('expires_at'), 0))
    victims: list[str] = []
    for item in ordered:
        if len(victims) >= excess:
            break
        image_id = str(item.get('image_id') or '').strip()
        if image_id:
            victims.append(image_id)
    return victims


def media_file_name(extension: str, digest: str) -> str:
    """上传用文件名：内容寻址，同一张图重复上传得到同名（平台侧仍会分配新 image_id）。"""
    return f'todaywaifu-{digest[:32]}.{extension.lower()}'


def content_type_for_extension(extension: str, default: str = 'application/octet-stream') -> str:
    """扩展名 → MIME；平台会校验扩展名与 Content-Type 必须一致，否则 40035001。"""
    mapping = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'webp': 'image/webp',
    }
    return mapping.get(extension.strip().lower(), default)
