"""抢夺他人今日老婆/老公/萝莉/正太：按功能元数据配置概率，并原子地完成归属转移。

四种条目共用一条实现：功能差异全部来自 `_daily_kind_metadata(kind)`，因此新增条目只需补齐
元数据，无需复制判定与转移逻辑。校验、记次、转移归属、落库在同一把每日上下文锁内串行执行，
使「失败也消耗当日次数」与「成功才发生归属转移」两个约束在并发下都成立。
"""

from __future__ import annotations

import random

from .shared import (
    LOG_PREFIX,
    Bot,
    Event,
    RoleCandidate,
    _cfg,
    logger,
    rob_sv,
    _cfg_bool,
    _user_key,
    _is_master,
    _safe_send,
    _wife_state,
    _record_to_dict,
    name_from_event,
    _cfg_probability,
    _has_active_wife,
    _daily_item_title,
    _daily_bucket_name,
    _husband_available,
    _daily_context_lock,
    _is_secondhand_wife,
    _load_daily_context,
    _save_daily_records,
    _daily_kind_metadata,
    _send_daily_result_image,
    _get_event_target_user_id,
    _get_existing_daily_record,
)
from .payloads import RoleRecordValue


def _rob_enabled(kind: str) -> bool:
    # 开关键由元数据提供：各条目可独立关闭，缺省为开启
    return _cfg_bool(_daily_kind_metadata(kind).rob_enabled_key, True)


def _rob_success_rate(kind: str) -> float:
    # 概率经 _cfg_probability 夹取到 [0, 1]，非法配置回落缺省值而不抛错
    metadata = _daily_kind_metadata(kind)
    return _cfg_probability(metadata.rob_success_rate_key, 0.5)


def _rob_success_template(kind: str) -> str:
    # 配置为空时退回元数据自带文案：空模板会让成功提示缺失关键信息（角色名与目标）
    metadata = _daily_kind_metadata(kind)
    return str(_cfg(metadata.rob_success_key) or metadata.rob_success_default)


def _build_rob_success_text(role: RoleCandidate, target_user_id: str, kind: str) -> str:
    # 模板变量由元数据约定：{name} 角色名、{role_id} 标识、{target} 被抢用户 ID
    template = _rob_success_template(kind)
    return template.format(
        name=role.name,
        role_id='/'.join(role.role_ids),
        target=target_user_id,
    )


def _rob_attempt_key(kind: str, user_id: str) -> str:
    # wife 沿用裸用户 ID：历史记录里已存在该键，改名会让升级当天的次数限制失效
    return user_id if kind == 'wife' else f'{kind}:{user_id}'


async def _send_rob_result_image(
    bot: Bot,
    role: RoleCandidate,
    image: str,
    text: str,
    user_id: str,
    is_group: bool,
    kind: str,
) -> None:
    # 必须经由共享发送器：由它统一完成图片下载、压缩与 AI 摘要注入，绕过则各条目表现不一致
    await _send_daily_result_image(bot, role, image, text, user_id, is_group, kind)


async def _send_rob_daily(bot: Bot, ev: Event, kind: str = 'wife') -> None:
    title = _daily_item_title(kind)
    # 老公的夺取能力跟随「今日老公」功能开关：该功能未启用时不存在可抢的目标
    if kind == 'husband' and not _husband_available():
        return
    if not _rob_enabled(kind):
        return
    logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} 发起抢{title}')

    target_user_id = _get_event_target_user_id(ev)
    # 解析失败时提示用户显式指定目标，而不是回退到发送者本人：否则会变成自己抢自己，
    # 或把目标错判为发起者
    if not target_user_id:
        return await _safe_send(
            bot,
            f'要抢谁的{title}？请艾特对方，或在命令后面写对方 QQ。\n'
            'QQ 官方机器人等没有 QQ 号的平台，可直接粘贴对方的用户 ID。',
        )

    robber_id = _user_key(ev)
    if target_user_id == robber_id:
        return await _safe_send(bot, f'自己抢自己的{title}也太奇怪了吧！')

    # 预先读取目标记录仅为快速失败：真正的归属校验在锁内重做，此处的结果不作为判定依据
    target_record = await _get_existing_daily_record(ev, target_user_id, kind)
    if target_record is None:
        return await _safe_send(bot, f'对方今天还没有{title}呢~')

    # 读-改-写全程持锁：校验、记次数、转移归属、落库串行执行。若拆成多段临界区，并发的两次
    # 抢夺可能都通过校验，导致同一记录被转移给两个人，或次数被重复消耗。
    refusal: str | None = None
    rob_failed = False
    updates: list[tuple[str, str, RoleRecordValue | bool | None]] = []
    async with _daily_context_lock(ev):
        context = await _load_daily_context(ev)
        bucket = _daily_bucket_name(kind)
        target_key = _user_key(ev, target_user_id)
        target_data = context[bucket].get(target_key)

        # 拒绝原因按优先级互斥判定：条件互不重叠，一旦命中即短路，避免同时消耗次数
        if _wife_state(target_data) != 'owned':
            refusal = f'对方的{title}已经不在身边了，抢不到了哦~'
        elif _is_secondhand_wife(target_data):
            # 抢来的或别人送的不再流转：否则同一条记录可在群内无限次易手
            refusal = f'对方这个{title}是抢来或别人送的，抢不动哦~'
        elif not _has_active_wife(target_data):
            refusal = f'对方今天还没有{title}呢~'

        # 抢方必须处于无有效条目的状态：抢与被抢互斥，防止用抢夺替换自己已有的名额
        if refusal is None:
            robber_data = context[bucket].get(robber_id)
            if _has_active_wife(robber_data):
                refusal = f'你今天已经有{title}了，先离婚再抢吧~'

        if refusal is None:
            attempts = context.setdefault('rob_attempts', {})
            is_master = _is_master(ev)
            attempt_key = _rob_attempt_key(kind, robber_id)
            # 兼容读取 wife 的旧键：升级前写入的裸用户 ID 记录仍需生效，否则当天限制被重置。
            # 主人不受次数限制，便于验证与处理异常。
            if not is_master and (attempts.get(attempt_key) or (kind == 'wife' and attempts.get(robber_id))):
                logger.info(f'{LOG_PREFIX} 用户 {robber_id} 今天抢{title}次数已用尽')
                refusal = f'今天已经抢过{title}啦，明天再来吧！'

        if refusal is None:
            # 次数在判定成败之前记入：失败同样消耗当日次数，否则可以反复尝试直到成功
            if not is_master:
                updates.append(('rob_attempts', attempt_key, True))
            if random.random() >= _rob_success_rate(kind):
                logger.info(f'{LOG_PREFIX} 用户 {robber_id} 抢 {target_user_id} 的{title}失败')
                rob_failed = True
            else:
                logger.info(f'{LOG_PREFIX} 用户 {robber_id} 成功抢走 {target_user_id} 的{title}')
                robbed_record = _record_to_dict(target_record, ev, robber_id)
                # 标记来源：抢来的记录据此被判定为二手，不再参与后续流转
                robbed_record['stolen_from'] = target_user_id
                updates.append((bucket, robber_id, robbed_record))
                target_update = context[bucket].get(target_key)
                if isinstance(target_update, dict):
                    # 仅为原持有者补写失主标记：记录本体保留，使对方仍能看到当天的历史留痕
                    target_update = dict(target_update)
                    target_update['stolen_by'] = robber_id
                    target_update['stolen_by_name'] = name_from_event(ev, robber_id)
                    updates.append((bucket, target_key, target_update))

            # 成败与记次在同一批次落库：分两次提交时中途失败会留下「次数已扣但归属未变」的
            # 不一致状态
            await _save_daily_records(ev, updates)

    if refusal is not None:
        return await _safe_send(bot, refusal)
    if rob_failed:
        return await _safe_send(bot, f'这次没抢到{title}，下次再试试吧~')

    # 展示被抢走的原始图片：记录已在锁内完成转移，此处只读取，不再改动状态
    role = target_record.to_role()
    await _send_rob_result_image(
        bot,
        role,
        target_record.image,
        _build_rob_success_text(role, target_user_id, kind),
        robber_id,
        ev.group_id is not None,
        kind,
    )


async def _send_rob_wife(bot: Bot, ev: Event) -> None:
    await _send_rob_daily(bot, ev, 'wife')


async def _send_rob_husband(bot: Bot, ev: Event) -> None:
    await _send_rob_daily(bot, ev, 'husband')


async def _send_rob_loli(bot: Bot, ev: Event) -> None:
    await _send_rob_daily(bot, ev, 'loli')


async def _send_rob_shota(bot: Bot, ev: Event) -> None:
    await _send_rob_daily(bot, ev, 'shota')


# ── 触发器注册 ────────────────────────────────────────────────────────────────
# 前缀与全匹配成对注册：on_prefix 处理带目标参数的抢夺，on_fullmatch 只在用户只输入命令时
# 命中并给出用法提示。二者都交由同一实现处理，因为缺少目标时它会返回同样的提示文案。
# block=True：命令命中后不再交给后续处理器，避免同一条消息被多个功能重复消费。


@rob_sv.on_prefix(
    ('抢老婆', '抢今日老婆', '抢婆娘'),
    block=True,
    to_ai="""抢夺指定用户今天的老婆。
    当用户说“抢某人的老婆”“抢老婆 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
    covers=['抢夺他人今日老婆，按概率判定成败'],
    aliases=['今日老婆·抢老婆', '今日老婆·抢婆娘'],
)
async def rob_wife(bot: Bot, ev: Event) -> None:
    await _send_rob_wife(bot, ev)


@rob_sv.on_fullmatch(
    ('抢老婆', '抢今日老婆', '抢婆娘'),
    block=True,
    to_ai="""显示抢老婆的用法。
    当用户只说“抢老婆”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['抢夺他人今日老婆，按概率判定成败'],
    aliases=['今日老婆·抢老婆', '今日老婆·抢婆娘'],
)
async def rob_wife_at(bot: Bot, ev: Event) -> None:
    await _send_rob_wife(bot, ev)


@rob_sv.on_prefix(
    ('抢老公', '抢今日老公'),
    block=True,
    to_ai="""抢夺指定用户今天的老公。
    当用户说“抢某人的老公”“抢老公 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
    covers=['抢夺他人今日老公，按概率判定成败'],
    aliases=['今日老婆·抢老公'],
)
async def rob_husband(bot: Bot, ev: Event) -> None:
    await _send_rob_husband(bot, ev)


@rob_sv.on_fullmatch(
    ('抢老公', '抢今日老公'),
    block=True,
    to_ai="""显示抢老公的用法。
    当用户只说“抢老公”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['抢夺他人今日老公，按概率判定成败'],
    aliases=['今日老婆·抢老公'],
)
async def rob_husband_at(bot: Bot, ev: Event) -> None:
    await _send_rob_husband(bot, ev)


@rob_sv.on_prefix(
    ('抢萝莉', '抢今日萝莉'),
    block=True,
    to_ai="""抢夺指定用户今天的萝莉。
    当用户说“抢某人的萝莉”“抢萝莉 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
    covers=['抢夺他人今日萝莉，按概率判定成败'],
    aliases=['今日老婆·抢萝莉'],
)
async def rob_loli(bot: Bot, ev: Event) -> None:
    await _send_rob_loli(bot, ev)


@rob_sv.on_fullmatch(
    ('抢萝莉', '抢今日萝莉'),
    block=True,
    to_ai="""显示抢萝莉的用法。
    当用户只说“抢萝莉”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['抢夺他人今日萝莉，按概率判定成败'],
    aliases=['今日老婆·抢萝莉'],
)
async def rob_loli_at(bot: Bot, ev: Event) -> None:
    await _send_rob_loli(bot, ev)


@rob_sv.on_prefix(
    ('抢正太', '抢今日正太'),
    block=True,
    to_ai="""抢夺指定用户今天的正太。
    当用户说“抢某人的正太”“抢正太 @某人”时调用。
    Args:
        text: 目标用户，通常是 @用户 或用户 ID。
    """,
    covers=['抢夺他人今日正太，按概率判定成败'],
    aliases=['今日老婆·抢正太'],
)
async def rob_shota(bot: Bot, ev: Event) -> None:
    await _send_rob_shota(bot, ev)


@rob_sv.on_fullmatch(
    ('抢正太', '抢今日正太'),
    block=True,
    to_ai="""显示抢正太的用法。
    当用户只说“抢正太”但没有指定目标用户时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['抢夺他人今日正太，按概率判定成败'],
    aliases=['今日老婆·抢正太'],
)
async def rob_shota_at(bot: Bot, ev: Event) -> None:
    await _send_rob_shota(bot, ev)
