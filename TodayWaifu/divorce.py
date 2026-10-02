"""TodayWaifu divorce commands.

离婚采用「标记记录」而非删除记录：把 ``divorced`` 置位并保留原始抽取结果，
使当天的重复离婚、被抢、被送等判断仍能读到记录本身。若直接删除记录，后续命令将
无法区分「今天没抽过」与「抽过但已离婚」，只能给出错误提示。
"""
from __future__ import annotations

from .shared import (
    LOG_PREFIX,
    Bot,
    Event,
    time,
    logger,
    _user_key,
    _safe_send,
    divorce_sv,
    _wife_state,
    _daily_bucket_name,
    _daily_context_lock,
    _load_daily_context,
    _save_daily_records,
)

# 同一语义提供多组命令写法：各平台用户习惯不同（「离婚」「和老婆离婚」「今日老婆离婚」
# 都在实际使用中出现），全部列入匹配集合，避免用户因措辞差异而收不到响应。
DIVORCE_COMMANDS = (
    '离婚',
    '老婆离婚',
    '离婚老婆',
    '今日老婆离婚',
    '和老婆离婚',
    '离婚群友',
    '群友离婚',
    '今日群友离婚',
    '和群友离婚',
)
# 老公/萝莉/正太各自独立成组：其存储桶与展示名不同，不能与老婆命令合并处理。
HUSBAND_DIVORCE_COMMANDS = (
    '老公离婚',
    '离婚老公',
    '今日老公离婚',
    '和老公离婚',
)
LOLI_DIVORCE_COMMANDS = (
    '萝莉离婚',
    '离婚萝莉',
    '今日萝莉离婚',
    '和萝莉离婚',
)
SHOTA_DIVORCE_COMMANDS = (
    '正太离婚',
    '离婚正太',
    '今日正太离婚',
    '和正太离婚',
)
# 异环与战双只有固定入口，不提供「今日」前缀变体：其标题本身已足够具体。
NTE_DIVORCE_COMMANDS = ('异环老婆离婚', '离婚异环老婆')
PGR_DIVORCE_COMMANDS = ('战双老婆离婚', '离婚战双老婆')


def _divorce_result_name(kind: str, name: str) -> str:
    """把内部记录名称转换为适合用户阅读的离婚结果。

    萝莉与正太的记录名来自角色库，直接回显会得到「已经和今天的萝莉离婚：xxx」，
    与用户实际操作的直觉不符；因此这两类固定展示为类型名，其余类型保留角色名。
    """
    if kind == 'loli':
        return '今日萝莉'
    if kind == 'shota':
        return '今日正太'
    return name


async def _send_divorce(bot: Bot, ev: Event, kind: str) -> None:
    user_key = _user_key(ev)
    # 展示名按 kind 就地映射：离婚是低频操作，为此引入类型元数据表反而增加耦合。
    title = {
        'wife': '老婆',
        'husband': '老公',
        'loli': '萝莉',
        'shota': '正太',
        'nte': '异环老婆',
        'pgr': '战双老婆',
    }[kind]
    logger.info(
        f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} '
        f'发起{title}离婚'
    )

    response: str | None = None
    result_name = ''
    # 读取、校验与写回必须在同一把上下文锁内完成。若在锁外读取、锁内仅写回，
    # 两个并发离婚请求会各自读到「未离婚」并先后写盘，用户收到两次成功提示，
    # 且第二次写入基于已过期的快照，可能覆盖期间发生的抢/送结果。
    async with _daily_context_lock(ev):
        context = await _load_daily_context(ev)
        bucket_name = _daily_bucket_name(kind)
        bucket = context[bucket_name]
        record = bucket.get(user_key)
        # 补偿老婆另存于 safe_wives；旧记录仅用于保留被抢历史，不能作为离婚对象。
        if kind == 'wife' and isinstance(record, dict) and record.get('stolen_by'):
            safe_record = context['safe_wives'].get(user_key)
            if isinstance(safe_record, dict) and str(safe_record.get('name') or '').strip():
                bucket_name = 'safe_wives'
                record = safe_record
        # 群友记录的展示名固定为「群友」：其 name 是具体群昵称，回显会暴露他人昵称。
        item_title = '群友' if isinstance(record, dict) and record.get('record_type') == 'member' else title
        # 状态校验与写入同处锁内，因此这里读到的 record 就是写回时依据的最新状态。
        if not isinstance(record, dict) or not str(record.get('name') or '').strip():
            response = f'你今天没有可以离婚的{item_title}。'
        elif record.get('divorced'):
            # 已离婚时不再写盘：避免无意义的磁盘写入与 mtime 抖动。
            response = f'你今天已经和{item_title}离婚了。'
        elif _wife_state(record) != 'owned':
            response = f'你今天没有可以离婚的{item_title}。'
        else:
            # 复制后再改：原 record 可能仍被上下文缓存中的其它引用持有，
            # 就地修改会让未持锁的读取方看到半成品状态。
            updated_record = dict(record)
            updated_record['divorced'] = True
            # 记录离婚时刻，供后续按时间判断记录是否属于今天。
            updated_record['divorced_at'] = int(time.time())
            await _save_daily_records(ev, [(bucket_name, user_key, updated_record)])
            result_name = _divorce_result_name(kind, str(record['name']))

    # 消息发送放在锁外：网络往返耗时不可控，持锁发送会阻塞同上下文的所有命令
    #（包括抽卡与赠送），把这些请求一起拖慢。
    if response is not None:
        return await _safe_send(bot, response)
    await _safe_send(bot, f'已经和今天的{item_title}离婚：{result_name}。')


@divorce_sv.on_fullmatch(
    DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的老婆婚姻关系。
    “离婚”默认表示离婚老婆，也可以使用老婆离婚等同义命令。
    Args:
        text: 无需参数，留空。
    """,
    covers=['结束今日老婆婚姻关系'],
    aliases=['今日老婆·离婚', '今日老婆·和老婆离婚'],
)
async def divorce_wife(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'wife')


@divorce_sv.on_fullmatch(
    HUSBAND_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的老公婚姻关系。
    当用户说“跟老公离婚”“老公离婚”“离婚老公”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日老公婚姻关系'],
    aliases=['今日老婆·和老公离婚', '今日老婆·老公离婚'],
)
async def divorce_husband(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'husband')


@divorce_sv.on_fullmatch(
    LOLI_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的萝莉关系。
    当用户说“跟萝莉离婚”“萝莉离婚”“离婚萝莉”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日萝莉关系'],
    aliases=['今日老婆·和萝莉离婚', '今日老婆·萝莉离婚'],
)
async def divorce_loli(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'loli')


@divorce_sv.on_fullmatch(
    SHOTA_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的正太关系。
    当用户说“跟正太离婚”“正太离婚”“离婚正太”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日正太关系'],
    aliases=['今日老婆·和正太离婚', '今日老婆·正太离婚'],
)
async def divorce_shota(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'shota')


@divorce_sv.on_fullmatch(
    NTE_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的异环老婆婚姻关系。
    当用户说“异环老婆离婚”“离婚异环老婆”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日异环老婆婚姻关系'],
    aliases=['今日老婆·异环老婆离婚'],
)
async def divorce_nte(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'nte')


@divorce_sv.on_fullmatch(
    PGR_DIVORCE_COMMANDS,
    block=True,
    to_ai="""结束当前用户今天的战双老婆婚姻关系。
    当用户说“战双老婆离婚”“离婚战双老婆”时调用。

    Args:
        text: 无需参数，留空即可
    """,
    covers=['结束今日战双老婆婚姻关系'],
    aliases=['今日老婆·战双老婆离婚'],
)
async def divorce_pgr(bot: Bot, ev: Event) -> None:
    await _send_divorce(bot, ev, 'pgr')
