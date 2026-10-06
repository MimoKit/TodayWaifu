"""显示名解析：把名字的三个来源收敛成一个入口。

名字可能来自三处，新鲜度与覆盖面各不相同：

1. **事件**（``ev.sender``）—— 最新，但只覆盖正在发消息的那一个人
2. **GsCore 的 CoreUser 群名单** —— 覆盖整群，但成员要先发过言才会被写进去
3. **已有记录的 display_name** —— 上一次解析的缓存，覆盖面最广但会过期

这三条此前分别散在 members 与 daily 两处：同一用户会拿到不同的名字，兜底也各写各的
（``str(user_id)`` / ``user_id`` / ``_user_key``），而且记录里的名字一旦写入就再也不会
刷新。本模块把它们收成一条链——``usable_name`` 判有效，``name_from_event`` 与
``load_group_names`` 取来源，``resolve_display_name`` 决定展示值与是否回写。
"""

from __future__ import annotations

import time
from collections.abc import Mapping

from sqlalchemy.exc import SQLAlchemyError

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.utils.database.models import CoreUser

from .paths import _user_key
from .state import _GROUP_DISPLAY_NAME_CACHE
from .constants import LOG_PREFIX, DISPLAY_NAME_REFRESH_SECONDS

# 上游写入的占位值多种多样（Python 的 None、字符串 "None"、数据库 NULL 字面量、
# 甚至直接用数字 1 兜底），逐项排除而非只判空：这些值一旦进入展示文案，
# 用户会看到「今天的老婆是 None」。
_PLACEHOLDER_VALUES = frozenset({'', '1', 'None', 'none', 'NULL', 'null'})

# 适配器对昵称字段的命名不统一（群名片 / 昵称 / 用户名），按语义由具体到宽泛依次回退。
_NAME_FIELDS = ('card', 'nickname', 'name', 'username', 'user_name')

# 长 ID（openid 等）整串铺进列表会撑破排版，且首尾相同的长串肉眼分不出谁是谁；
# 截断保留首尾，至少能人工比对。
_ID_HEAD_KEEP = 6
_ID_TAIL_KEEP = 4
_ID_KEEP_WHOLE = 12


def usable_name(value: object, user_id: str | int | None = None) -> str:
    """返回可展示的名字；占位值与「名字恰好等于用户 ID」都视为没有名字。"""
    text = str(value or '').strip()
    if text in _PLACEHOLDER_VALUES:
        return ''
    # 名字等于 ID 时交由调用方回落到占位，避免「12345 → 角色」这种两边同值的文案。
    if user_id is not None and text == str(user_id):
        return ''
    return text


def name_from_mapping(data: object, user_id: str | int | None = None) -> str:
    """从适配器上报的 sender 字典里按字段优先级取名字。"""
    if not isinstance(data, Mapping):
        return ''
    for field in _NAME_FIELDS:
        value = usable_name(data.get(field), user_id)
        if value:
            return value
    return ''


def name_from_event(ev: Event, user_id: str | int | None = None) -> str:
    """取事件发送者的名字，取不到时回落到用户键。

    事件比数据库新（用户刚改群名片时 CoreUser 尚未同步），因此查询目标就是发送者本人时
    优先用事件数据；查他人时没有事件上下文，只能回落到用户键。
    """
    key = _user_key(ev, user_id)
    if user_id is None or key == str(ev.user_id):
        value = name_from_mapping(ev.sender or {}, key)
        if value:
            return value
    return key


def placeholder_name(user_id: str | int | None) -> str:
    """三个来源都拿不到名字时的展示占位。

    纯数字 ID 就是 QQ 号，本身可读、可复制去加好友，原样保留；openid 一类的长串截断，
    否则一条记录就能把列表撑出屏幕。
    """
    text = str(user_id or '').strip()
    if not text or len(text) <= _ID_KEEP_WHOLE:
        return text
    return f'{text[:_ID_HEAD_KEEP]}…{text[-_ID_TAIL_KEEP:]}'


def is_stale(updated_at: object, now: float | None = None) -> bool:
    """记录里的名字是否已过期。取不到时间戳时按过期处理，让它有机会被刷新一次。"""
    if isinstance(updated_at, bool):
        return True
    if isinstance(updated_at, (int, float)):
        stamp = float(updated_at)
    elif isinstance(updated_at, str):
        try:
            stamp = float(updated_at)
        except ValueError:
            return True
    else:
        return True
    return (now if now is not None else time.time()) - stamp > DISPLAY_NAME_REFRESH_SECONDS


async def load_group_names(ev: Event) -> dict[str, str]:
    """读取 CoreUser 里本群成员的显示名，键为 user_id。

    非群聊没有成员名单，返回空表而非报错，使调用方统一走「无显示名」分支。
    """
    if not ev.group_id:
        return {}

    # 缓存键只含 bot_id 与 group_id，不含发起请求的用户：成员名单是群级数据，
    # 按用户区分缓存只会重复查询同一份名单。
    cache_key = f'{ev.bot_id}:{ev.group_id}'

    async def load_names() -> dict[str, str]:
        try:
            users = await CoreUser.get_group_all_user(str(ev.group_id))
        except SQLAlchemyError as exc:
            # 数据库异常降级为空表：显示名只是文案修饰，不值得让整条抽卡流程失败。
            logger.warning(f'{LOG_PREFIX} 读取 GsCore 群成员缓存失败: {exc}')
            return {}

        # 同一用户可能在多个 bot_id 下各有一行（多适配器接入同一群）。优先取当前
        # 真实 bot 的行，使显示名与用户实际看到的来源一致；取不到再退到任意一行。
        preferred_bot_id = str(ev.real_bot_id or ev.bot_id or '').strip()
        exact: dict[str, str] = {}
        fallback: dict[str, str] = {}
        for user in users or []:
            user_id = str(user.user_id or '').strip()
            if not user_id:
                continue
            name = usable_name(user.user_name, user_id)
            if name:
                fallback[user_id] = name
                if preferred_bot_id and str(user.bot_id or '').strip() == preferred_bot_id:
                    exact[user_id] = name
        logger.debug(f'{LOG_PREFIX} 成功加载群 {ev.group_id} 的成员显示名称')
        # 只有当精确匹配确实命中时才采用它：exact 为空说明没有该 bot 的行，
        # 此时回退结果比空表更有用。
        return exact or fallback

    return await _GROUP_DISPLAY_NAME_CACHE.get(cache_key, load_names)


def resolve_display_name(
    *,
    stored: object,
    group_names: Mapping[str, str],
    user_id: str | int,
    updated_at: object = None,
) -> tuple[str, bool]:
    """解析最终展示名，并给出是否需要把新名字回写进记录。

    优先级：群名单的当前名字（记录里的名字已过期时）> 记录里的名字 > ID 占位。
    群名单来自 CoreUser，每条消息都会刷新，因此比记录里的缓存新；记录里的名字只在没有
    更权威来源时保留，否则用户改一次群名片就会被永久钉在旧名字上。
    """
    current = usable_name(stored, user_id)
    latest = usable_name(group_names.get(str(user_id)), user_id)

    if latest and (not current or is_stale(updated_at)):
        return latest, latest != current
    if current:
        return current, False
    return placeholder_name(user_id), False
