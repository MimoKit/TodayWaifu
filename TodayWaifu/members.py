"""TodayWaifu 的群成员目录与头像。

成员相关数据分布在两处：GsCore 的 CoreUser 表提供群成员缓存，头像需要按需下载到本地。
两者都有时效性且获取成本不低（一次数据库查询 / 一次网络请求），因此分别经有界缓存与
在途合并表收敛，避免同一群、同一成员被并发重复读取。

显示名的字段探测、有效值判定与回写策略集中在 `display_name` 模块，此处只用它。
"""

from __future__ import annotations

import re
import time
import random
import asyncio
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from sqlalchemy.exc import SQLAlchemyError

from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.utils.database.models import CoreUser

from .paths import _user_key, _daily_rng, _custom_upload_data_root
from .state import _MEMBER_CACHE, _MEMBER_AVATAR_INFLIGHT
from .domain import WifeRecord, MemberCandidate
from .executor import run_blocking
from .constants import LOG_PREFIX, MEMBER_AVATAR_CACHE_SECONDS, _cfg_bool, _cfg_probability
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


def _member_avatar_cache_path(user_id: str) -> Path:
    # 用户 ID 来自平台，直接参与拼接路径存在目录穿越风险；此处只保留安全字符集，
    # 其余一律替换为下划线，使生成的文件名必定落在缓存目录内。
    safe_user_id = re.sub(r'[^0-9A-Za-z_-]+', '_', str(user_id)) or 'unknown'
    return _custom_upload_data_root() / 'group_member_avatar_cache' / f'{safe_user_id}.jpg'


def _usable_cached_avatar(path: Path, check_ttl: bool = True) -> bool:
    try:
        # 零字节文件通常来自上次写盘中断，视为不可用，否则会把空文件当成头像发出去。
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        if check_ttl and time.time() - path.stat().st_mtime > MEMBER_AVATAR_CACHE_SECONDS:
            logger.debug(f'{LOG_PREFIX} 缓存的头像已过期: {path}')
            return False
        return True
    except OSError:
        # 并发删除或权限问题下 stat 可能失败，按不可用处理而不中断流程。
        return False


def _download_avatar(url: str, path: Path) -> bool:
    try:
        logger.debug(f'{LOG_PREFIX} 开始下载头像: {url} -> {path}')
        path.parent.mkdir(parents=True, exist_ok=True)
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        # 8 秒超时：头像属于可选素材，长时间阻塞会拖慢整个群友抽取流程。
        with urlopen(request, timeout=8) as response:
            # 多读 1 字节用于判定是否超限，避免把超大响应整个载入内存。
            data = response.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            logger.warning(f'{LOG_PREFIX} 下载头像数据无效或体积过大: {url}')
            return False
        # 先写临时文件再原子替换：并发读方不会读到只写了一半的图片。
        tmp_path = path.with_suffix('.tmp')
        tmp_path.write_bytes(data)
        tmp_path.replace(path)
        logger.debug(f'{LOG_PREFIX} 头像下载完成: {path}')
        return True
    except (OSError, HTTPError, URLError, TimeoutError) as exc:
        # 网络与磁盘异常一律降级为失败：调用方会继续尝试其它候选，不需要异常穿透。
        logger.warning(f'{LOG_PREFIX} 下载群友头像失败: {url} -> {exc}')
        return False


def _resolve_member_avatar(user_id: str, avatar_source: str) -> str:
    # 解析顺序为「新鲜缓存 → 平台给出 http 头像 → 本地路径 → QQ 头像接口 →
    # 过期缓存」。把平台来源排在 QQ 接口之前，是因为前者来自当前平台、与用户实际
    # 形象一致；最后仍允许使用过期缓存，使离线或接口限流时至少还有图可发。
    cache_path = _member_avatar_cache_path(user_id)
    if _usable_cached_avatar(cache_path):
        return str(cache_path)

    source = _valid_member_text(avatar_source)
    if source.startswith(('http://', 'https://')):
        if _download_avatar(source, cache_path):
            return str(cache_path)
    elif source:
        try:
            local_path = Path(source)
            if local_path.is_file():
                # 本地路径直接返回，不复制到缓存：调用方只读取，无需二次占用磁盘。
                return str(local_path)
        except (OSError, ValueError):
            logger.debug(f'{LOG_PREFIX} 头像本地路径无效: {source}')

    if str(user_id).isdigit() and _download_avatar(_qq_avatar_url(str(user_id)), cache_path):
        return str(cache_path)

    # 过期缓存作为最后兜底，仅在前面全部失败时使用；check_ttl=False 只跳过时效判断，
    # 空文件仍会被拒绝。
    if _usable_cached_avatar(cache_path, check_ttl=False):
        return str(cache_path)
    return ''


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
    # 按用户 ID 合并在途任务：同一成员可能被多个并发命令同时抽到，
    # 共享一次下载即可，否则会重复占用网络与线程池。
    task = _MEMBER_AVATAR_INFLIGHT.get(member.user_id)
    if task is None:
        task = asyncio.create_task(run_blocking(_resolve_member_avatar, member.user_id, member.avatar))
        _MEMBER_AVATAR_INFLIGHT[member.user_id] = task
    try:
        avatar = await task
    finally:
        # 仅在任务已结束且仍是当前项时移除，避免覆盖掉后来者登记的新任务。
        if task.done() and _MEMBER_AVATAR_INFLIGHT.get(member.user_id) is task:
            _MEMBER_AVATAR_INFLIGHT.pop(member.user_id, None)
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
