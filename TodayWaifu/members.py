"""TodayWaifu 的群成员目录与头像。

成员名单来自 GsCore 的 CoreUser 表，获取成本不低（一次数据库查询），因此经有界缓存收敛，
避免同一群被并发重复读取。

头像则**完全不缓存**：它本身是高频变化的实时资源，缓存一份本地副本既长期占用磁盘，又会在
用户换头像后继续发出旧图。因此这里只把成员的头像字段解析成可公开访问的 URL，真正的字节
获取推迟到发送那一刻（见 senders 模块）。

显示名的字段探测、有效值判定与回写策略集中在 `display_name` 模块，此处只用它。
"""

from __future__ import annotations

import time
import random
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy.exc import SQLAlchemyError

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.utils.database.models import CoreUser

from .paths import _user_key, _daily_rng
from .state import _MEMBER_CACHE
from .domain import WifeRecord, MemberCandidate
from .constants import LOG_PREFIX, _cfg_bool, _cfg_probability
from .display_name import usable_name, placeholder_name


def _member_feature_enabled() -> bool:
    # 默认关闭：群友模式会读取整群名单并对随机成员发起网络请求，属于需要用户明确
    # 同意的行为，不应在未配置时自动生效。
    return _cfg_bool('DailyWifeEnableGroupMember', False)


def _marry_member_enabled() -> bool:
    return _cfg_bool('DailyWifeMarryGroupMemberEnabled', False)


def _member_probability() -> float:
    # 默认 0.1 是概率而非开关：群友只是抽卡的补充结果，过高会挤占角色图库的出场机会。
    return _cfg_probability('DailyWifeGroupMemberProbability', 0.1)


def _valid_member_text(value: object) -> str:
    # 与显示名校验共用同一组占位值，但不排除与用户 ID 相同的情况：
    # 头像来源本身就是 ID 或 URL，此处只负责剔除无效值。
    text = str(value or '').strip()
    if text in {'', '1', 'None', 'none', 'NULL', 'null'}:
        return ''
    return text


def _qq_avatar_url(user_id: str) -> str:
    return f'https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640'


def _resolve_member_avatar(user_id: str, avatar_source: str) -> str:
    """把成员头像解析为一个可公开访问的 URL。

    解析顺序为「平台给出的 http 头像 → 数字 ID 的 QQ 头像接口」。平台来源优先，是因为
    它来自当前会话、与用户此刻的形象一致；QQ 头像接口作为兜底，覆盖平台未上报头像的
    情况；只有 openid 且没有头像地址时无法通过 QQ 号接口兜底。

    返回空串表示无法解析：调用方据此丢弃该候选。

    注意本函数**不做任何下载与落盘**。头像本身就是高频变化的实时资源，缓存一份本地副本
    既会长期占用磁盘，又会在用户换头像后继续发出旧图，因此改为每次发送时重新获取。
    """
    source = _valid_member_text(avatar_source)
    if source.startswith(('http://', 'https://')):
        return source
    if str(user_id).isdigit():
        return _qq_avatar_url(str(user_id))
    return ''


def _cache_busted_avatar_url(url: str) -> str:
    """给 qlogo 头像地址追加时间戳，绕开其 30 天的边缘缓存。

    仅对 qlogo 生效：其它图床（如平台自建 CDN）可能对未知查询参数做严格校验，无差别追加
    会让本来可用的地址失败。地址已带查询串时用 ``&`` 追加，保持 URL 合法。
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').lower()
    if hostname != 'qlogo.cn' and not hostname.endswith('.qlogo.cn'):
        return url
    query = f'{parsed.query}&' if parsed.query else ''
    return parsed._replace(query=f'{query}_t={int(time.time())}').geturl()


def _download_member_avatar(url: str) -> bytes | None:
    """下载头像字节；失败或体积异常时返回 None。仅供线程池调用。

    8 秒超时：头像属于可选素材，长时间阻塞会拖慢整个群友抽取流程。

    对 qlogo 系地址追加一个时间戳参数：该 CDN 对头像下发 ``max-age=2592000``（30 天），
    而头像 URL 本身是「用户 ID 固定拼接」因此永不改变，导致边缘节点可能长期回源旧图。
    时间戳只作用于本次请求，不写回记录，故不会污染存储的地址。实测 qlogo 忽略该参数。
    """
    request_url = _cache_busted_avatar_url(url)
    try:
        logger.debug(f'{LOG_PREFIX} 开始下载头像: {request_url}')
        request = Request(request_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(request, timeout=8) as response:
            # 多读 1 字节用于判定是否超限，避免把超大响应整个载入内存。
            data = response.read(2 * 1024 * 1024 + 1)
    except (OSError, ValueError, HTTPError, URLError, TimeoutError) as exc:
        logger.warning(f'{LOG_PREFIX} 下载群友头像失败: {url} -> {exc}')
        return None
    if not data or len(data) > 2 * 1024 * 1024:
        logger.warning(f'{LOG_PREFIX} 头像数据无效或体积过大: {url}')
        return None
    return data


async def _load_group_member_candidates(ev: Event) -> tuple[MemberCandidate, ...]:
    if not ev.group_id:
        return ()

    cache_key = f'{ev.bot_id}:{ev.group_id}'

    async def load_members() -> tuple[MemberCandidate, ...]:
        try:
            users = await CoreUser.get_group_all_user(str(ev.group_id))
        except SQLAlchemyError as exc:
            logger.warning(f'{LOG_PREFIX} 读取 GsCore 群成员缓存失败: {exc}')
            return ()

        # 收集所有可能是机器人自身的 ID：不同适配器分别填写 bot_id / real_bot_id /
        # bot_self_id，任何一个都可能是该群里的机器人账号。三者全部排除，
        # 否则用户可能抽到自己或机器人。
        bot_ids = {
            str(item).strip()
            for item in (
                ev.bot_id,
                ev.real_bot_id,
                ev.bot_self_id,
            )
            if str(item or '').strip()
        }
        excluded_user_ids = set(bot_ids)
        preferred_bot_id = str(ev.real_bot_id or ev.bot_id or '').strip()
        exact: dict[str, MemberCandidate] = {}
        fallback: dict[str, MemberCandidate] = {}

        for user in users or []:
            user_id = str(user.user_id or '').strip()
            if not user_id or user_id in excluded_user_ids:
                continue
            # CoreUser 的展示名列固定为 user_name（不存在 nickname/name/username 列）。
            # 取不到显示名时用统一的占位而非裸 ID：成员记录即便没有昵称也应可被抽中。
            name = usable_name(user.user_name, user_id) or placeholder_name(user_id)
            avatar = _valid_member_text(user.user_icon)
            candidate = MemberCandidate(name=name, user_id=user_id, avatar=avatar)
            fallback[user_id] = candidate
            if preferred_bot_id and str(user.bot_id or '').strip() == preferred_bot_id:
                exact[user_id] = candidate

        result = exact or fallback
        logger.debug(f'{LOG_PREFIX} 获取到 {len(result)} 个群友候选对象')
        # 按名称与 ID 排序固定顺序：随机抽取随后会在此基础上打乱，
        # 若输入顺序本身不稳定，同一用户当天的结果将无法复现。
        return tuple(sorted(result.values(), key=lambda item: (item.name, item.user_id)))

    return await _MEMBER_CACHE.get(cache_key, load_members)


async def _resolve_member_candidate_avatar(member: MemberCandidate) -> MemberCandidate | None:
    """把候选的头像字段解析为可公开访问的 URL；无法解析时丢弃该候选。

    此处只解析 URL、不下载字节：头像要在发送那一刻才取最新版本，提前下载等于又引入一层
    隐式缓存。解析是纯字符串运算，无需线程池与在途合并。
    """
    avatar = _resolve_member_avatar(member.user_id, member.avatar)
    if not avatar:
        # 无头像的候选直接丢弃：结果图片缺少人像会呈现为空位，弱于换一个候选。
        return None
    return MemberCandidate(member.name, member.user_id, avatar)


async def _pick_group_member(
    ev: Event,
    rng: random.Random,
    exclude_user_id: str | int | None = None,
) -> MemberCandidate | None:
    candidates = list(await _load_group_member_candidates(ev))
    if not candidates:
        return None

    # 排除发起者本人与机器人自身；显式传入 exclude_user_id 时以它为准，
    # 用于代替他人抽卡（如管理员代抽）时仍需排除被代替的用户。
    target_user_id = str(exclude_user_id if exclude_user_id is not None else ev.user_id).strip()
    exclude_ids = {target_user_id}
    bot_self_id = str(ev.bot_self_id or '').strip()
    if bot_self_id:
        exclude_ids.add(bot_self_id)

    candidates = [c for c in candidates if str(c.user_id) not in exclude_ids]
    if not candidates:
        logger.warning(f'{LOG_PREFIX} 过滤自身及Bot后无可用群友候选')
        return None

    # 先打乱再逐个尝试头像：任何候选都可能因网络原因取不到头像，
    # 打乱保证回退顺序与名单顺序无关，不会总是固定落到同一个人。
    rng.shuffle(candidates)
    for member in candidates:
        resolved = await _resolve_member_candidate_avatar(member)
        if resolved is not None:
            logger.debug(f'{LOG_PREFIX} 成功挑选群友: {resolved.name} ({resolved.user_id})')
            return resolved
    # 全部候选均无有效头像时返回 None，由调用方决定是否回落到角色图库。
    logger.warning(f'{LOG_PREFIX} 未能成功获取任一群友的有效头像')
    return None


async def _roll_group_member_wife(
    ev: Event,
    user_id: str | int | None = None,
    rng: random.Random | None = None,
) -> WifeRecord | None:
    # 逐层短路：功能未开启、非群聊、概率为 0 时都不需要读取成员名单。
    if not _member_feature_enabled() or not ev.group_id:
        return None

    probability = _member_probability()
    if probability <= 0:
        return None

    key = _user_key(ev, user_id)
    # 检定与挑选使用两个独立的随机源：若共用同一个实例，检定消耗的随机数会影响
    # 挑选结果，使同一天同一用户在不同调用次数下得到不同群友。
    hit_rng = rng or _daily_rng(ev, key, 'group_member_probability')
    rolled_prob = hit_rng.random()
    if rolled_prob >= probability:
        logger.debug(f'{LOG_PREFIX} 抽群友检定未通过: {rolled_prob:.4f} >= {probability}')
        return None

    logger.debug(f'{LOG_PREFIX} 触发抽群友逻辑')
    pick_rng = rng or _daily_rng(ev, key, 'group_member_pick')
    member = await _pick_group_member(ev, pick_rng, exclude_user_id=user_id)
    if member is None:
        return None
    return WifeRecord.from_member(member)
