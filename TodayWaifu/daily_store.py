"""TodayWaifu 的每日记录持久化、上下文快照与记录结构转换。

每日记录以「日期 + bot + 群」为分片落库，内存中另维护一层上下文快照以承载高频读取。
快照的发布时刻严格晚于数据库提交，跨日时按新日期回收旧分片，二者共同保证：读到内存
快照的调用方不会观察到尚未落盘的状态，跨日翻转后也不会驻留前一日的数据。
"""

from __future__ import annotations

import copy
import time
import asyncio

from sqlalchemy.exc import SQLAlchemyError

from gsuid_core.logger import logger
from gsuid_core.models import Event

from .paths import _user_key, _today_key, _context_key, _daily_context_key
from .state import _CONTEXT_REGISTRY, _DAILY_CONTEXT_CACHE
from .domain import WifeRecord
from .models import DailyWifeRecord
from .senders import _is_valid_image_ref
from .payloads import WifeData, DailyContext, RoleRecordValue
from .constants import LOG_PREFIX, DAILY_WIFE_KINDS, ALL_DAILY_RECORD_KINDS, _daily_bucket_name
from .display_name import name_from_event
from .invalidation import _invalidate_status_cache


def _daily_context_lock(ev: Event) -> asyncio.Lock:
    """返回按 bot 与群分片的每日记录锁，使不同群之间互不阻塞。

    若改用单一全局锁，零点抽签高峰时所有群会在同一把锁上排队，单群延迟被放大为整体
    串行耗时。
    """
    return _CONTEXT_REGISTRY.lock_for(_daily_context_key(ev))


# 最近一次见到的日期，用于在翻转瞬间立即回收前一日的上下文快照
_LAST_CONTEXT_DAY: str | None = None


# ── 写合并 ────────────────────────────────────────────────────────────────────
# GsCore 默认使用 SQLite，所有写入共用一个**进程级单写者闸门**，实测吞吐约 250 写/秒
# （约 4ms/次）。零点高峰每个用户一次抽签即一次写入，逐条提交会占满闸门，而等待写入
# 的命令协程仍占着 Core 的命令并发额度。
#
# 因此将同一事件循环回合内到达的写入合并为**一条多值 upsert**，所有调用方 await 同一
# task，由此获得三点性质：
#   - 落库时机不变（调用方仍等到真正提交完成才返回），持久性不受影响
#   - 异常自然向所有等待者传播，无须额外实现 future 广播
#   - 闸门压力按合并倍数摊薄（25 个群同时抽签只产生 1 次提交而非 25 次）
_PendingRow = tuple[str, str, str, str, str, 'RoleRecordValue | bool | None']


class _WriteBatch:
    __slots__ = ('rows', 'deletes', 'task')

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str, str], 'RoleRecordValue | bool | None'] = {}
        self.deletes: set[tuple[str, str, str, str, str]] = set()
        self.task: asyncio.Task[None] | None = None

    def add(self, key: tuple[str, str, str, str, str], value: 'RoleRecordValue | bool | None') -> None:
        """写入一行，并撤销同一批中针对该键的删除。

        同一批次内先删后写时，删除集合中的残留项会在真正落库时覆盖新值，故此处必须
        显式丢弃。
        """
        self.rows[key] = value
        self.deletes.discard(key)

    def drop(self, key: tuple[str, str, str, str, str]) -> None:
        """删除一行，并移除同一批中针对该键的待写入值，避免旧值被重新落库。"""
        self.rows.pop(key, None)
        self.deletes.add(key)

    def ensure_task(self) -> asyncio.Task[None]:
        """返回本批次的提交 task，不存在时惰性创建。

        同一批次只创建一个 task，多个调用方由此共享同一次提交与同一个异常结果。
        """
        if self.task is None:
            self.task = asyncio.create_task(_flush_write_batch(self))
            self.task.add_done_callback(_consume_batch_exception)
        return self.task


_PENDING_BATCH: _WriteBatch | None = None


def _consume_batch_exception(task: asyncio.Task[None]) -> None:
    """取回 task 异常，避免调用方被取消时产生 'exception was never retrieved'。

    异常仍会通过 await 传播给等待者，此处仅消除事件循环的未取回告警。
    """
    if not task.cancelled():
        task.exception()


def _current_batch() -> _WriteBatch:
    """返回当前待合并的批次，不存在时创建。"""
    global _PENDING_BATCH
    if _PENDING_BATCH is None:
        _PENDING_BATCH = _WriteBatch()
    return _PENDING_BATCH


async def _flush_write_batch(batch: _WriteBatch) -> None:
    global _PENDING_BATCH
    # 让出一个事件循环回合，以便把这一瞬间到达的写入全部收进同一批
    await asyncio.sleep(0)
    # 以下两步之间**不得出现 await**：先摘除当前批（此后到达的写入另开新批），
    # 再同步快照；否则在摘除与快照之间加入的行会被静默丢弃
    if _PENDING_BATCH is batch:
        _PENDING_BATCH = None
    rows = [
        (day, bot_id, group_id, bucket, user_key, value)
        for (day, bot_id, group_id, bucket, user_key), value in batch.rows.items()
    ]
    deletes = list(batch.deletes)
    if deletes or rows:
        await DailyWifeRecord.apply_rows(rows, deletes)


async def flush_pending_writes() -> None:
    """立即提交当前待落库的写入，用于关停与测试。

    通过 gather 等待既有 task，因此提交失败不会在此处抛出：关停流程不应因最后一次
    落库失败而中断。
    """
    batch = _PENDING_BATCH
    if batch is None or batch.task is None:
        return
    await asyncio.gather(batch.task, return_exceptions=True)


def _roll_over_context_day(day: str) -> int:
    """日期翻转时立即回收前一日的上下文快照，返回回收数量。

    若不回收，零点至凌晨 1 点之间内存中会同时驻留两天的全部活跃群上下文，对数千群的
    bot 意味着数百 MB 级额外占用与 GC 压力。

    以最近一次见到的日期作为翻转判据：同一日期内重复调用直接返回，避免每次读取上下文
    都触发一轮全量扫描。首次调用（无历史日期）只登记日期而不回收，防止把本次启动前的
    存量数据误判为过期。
    """
    global _LAST_CONTEXT_DAY
    if _LAST_CONTEXT_DAY == day:
        return 0
    previous = _LAST_CONTEXT_DAY
    _LAST_CONTEXT_DAY = day
    if previous is None:
        return 0

    dropped = _CONTEXT_REGISTRY.drop_stale_days(day)
    stale_keys = [key for key in _DAILY_CONTEXT_CACHE if not key.startswith(f'{day}:')]
    for key in stale_keys:
        _DAILY_CONTEXT_CACHE.pop(key, None)
    logger.info(
        f'{LOG_PREFIX} 日期从 {previous} 翻转到 {day}，已回收 {dropped} 个上下文快照'
        f'（兼容缓存另有 {len(stale_keys)} 条）'
    )
    return dropped


async def _load_daily_context(ev: Event) -> DailyContext:
    """按上下文加载每日快照，同一 key 的并发调用共享同一次加载。

    数据库异常直接向调用方传播，不做降级：调用方对空快照的容错会掩盖真实故障，且后续
    基于空快照的写入可能覆盖库中已有记录。

    单飞机制的必要性在于零点高峰同一群通常被并发命中；若每个调用方各自查询，会重复
    读取同一分片，并且可能以不同代际的结果互相覆盖。加载完成前先取 generation，写入
    时校验代际，使并发翻转期间产生的过期结果被拒绝而非写入注册表。
    """
    key = _daily_context_key(ev)
    _roll_over_context_day(key.day)
    cached = _CONTEXT_REGISTRY.get(key)
    if cached is not None:
        return cached
    task = _CONTEXT_REGISTRY.inflight.get(key)
    if task is None:
        generation = _CONTEXT_REGISTRY.generation(key)

        async def hydrate() -> DailyContext:
            context = await DailyWifeRecord.get_context(
                key.day,
                key.bot_id,
                key.group_id,
            )
            data = {'days': {key.day: {_context_key(ev): context}}}
            result = _get_today_context(data, ev)
            if _CONTEXT_REGISTRY.put(key, result, generation):
                _DAILY_CONTEXT_CACHE[key.cache_key] = (key.day, result)
            return result

        task = asyncio.create_task(hydrate())
        _CONTEXT_REGISTRY.inflight[key] = task
    try:
        return await task
    finally:
        if task.done() and _CONTEXT_REGISTRY.inflight.get(key) is task:
            _CONTEXT_REGISTRY.inflight.pop(key, None)


async def _submit_writes(
    ev: Event,
    records: list[tuple[str, str, RoleRecordValue | bool | None]] | None = None,
    deletes: list[tuple[str, str]] | None = None,
) -> None:
    """把写入并入当前批次，并等待提交完成。

    `_current_batch()` / `add()` / `ensure_task()` 三步之间**没有 await**，在本事件循环
    回合内构成原子区段：不会出现「行已加入但未进入该批次」的丢失。一旦中间插入 await，
    另一协程可能摘除并提交当前批次，使本次写入落入已被落库的旧批次。
    """
    key = _daily_context_key(ev)
    batch = _current_batch()
    for bucket, user_key, value in records or ():
        batch.add((key.day, key.bot_id, key.group_id, bucket, str(user_key)), value)
    for bucket, user_key in deletes or ():
        batch.drop((key.day, key.bot_id, key.group_id, bucket, str(user_key)))
    await batch.ensure_task()


async def _save_daily_records(
    ev: Event,
    records: list[tuple[str, str, RoleRecordValue | bool | None]],
    deletes: list[tuple[str, str]] | None = None,
) -> None:
    """提交批量记录（与同一瞬间的其它写入合并为一次事务），随后同步内存快照。

    内存更新必须在提交返回之后进行：提前发布会让并发读取方看到未落盘的状态，
    进程若在提交完成前中断，即造成「已生效但已丢失」的不一致。
    """
    key = _daily_context_key(ev)
    await _submit_writes(ev, records, deletes)
    context = await _load_daily_context(ev)
    for bucket, user_key, value in records:
        context.setdefault(bucket, {})[str(user_key)] = value
    for bucket, user_key in deletes or ():
        bucket_data = context.get(bucket)
        if isinstance(bucket_data, dict):
            bucket_data.pop(str(user_key), None)
    _CONTEXT_REGISTRY.put(key, context, _CONTEXT_REGISTRY.generation(key))
    _invalidate_status_cache()


async def _save_daily_record(ev: Event, bucket: str, user_key: str, value: RoleRecordValue | bool | None) -> None:
    """提交单条记录（与同一瞬间的其它写入合并为一次事务），成功后才更新内存快照。"""
    await _submit_writes(ev, [(bucket, user_key, value)])
    context = await _load_daily_context(ev)
    context.setdefault(bucket, {})[user_key] = value
    _CONTEXT_REGISTRY.put(_daily_context_key(ev), context, _CONTEXT_REGISTRY.generation(_daily_context_key(ev)))
    _invalidate_status_cache()


async def _delete_daily_record(ev: Event, bucket: str, user_key: str) -> None:
    """删除单条记录（与同一瞬间的其它写入合并为一次事务），成功后才同步内存快照。"""
    await _submit_writes(ev, deletes=[(bucket, user_key)])
    context = await _load_daily_context(ev)
    bucket_data = context.get(bucket)
    if isinstance(bucket_data, dict):
        bucket_data.pop(user_key, None)
    _CONTEXT_REGISTRY.put(_daily_context_key(ev), context, _CONTEXT_REGISTRY.generation(_daily_context_key(ev)))
    _invalidate_status_cache()


async def _save_daily_context(ev: Event, context: DailyContext) -> None:
    """以兼容路径整体提交上下文，成功后才发布新的内存快照。

    写入前对上下文做深拷贝：调用方持有的字典随后仍可能被就地修改，若不隔离，未落盘的
    改动会经共享引用进入内存快照，使内存状态超前于数据库。
    """
    key = _daily_context_key(ev)
    snapshot = copy.deepcopy(context)
    await DailyWifeRecord.upsert_context(key.day, key.bot_id, key.group_id, snapshot)
    _CONTEXT_REGISTRY.put(key, snapshot)
    _DAILY_CONTEXT_CACHE[key.cache_key] = (key.day, snapshot)
    _invalidate_status_cache()


async def _load_wife_data() -> WifeData:
    """加载当日全部记录，返回与旧 JSON 完全相同的 {'days': {today: {context: ...}}} 结构。

    保持该结构是为了让历史 JSON 读写路径与数据库路径共用同一套调用方代码。
    """
    today = _today_key()
    contexts = await DailyWifeRecord.load_day(today)
    return {'days': {today: contexts}}


async def _save_wife_data(data: WifeData) -> None:
    """把 {'days': {day: {context_key: context}}} 结构整体写回数据库（按 context 先删后插）。

    单个 context 写失败仅记录日志后继续：整体回滚会连带丢弃已经成功的分片，代价高于
    少量数据缺失；缺少 group 段时以 'direct' 兜底，与私聊上下文的键格式保持一致。
    """
    days = data.get('days') if isinstance(data, dict) else None
    if not isinstance(days, dict):
        return
    for day, contexts in days.items():
        if not isinstance(contexts, dict):
            continue
        for context_key, context in contexts.items():
            if not isinstance(context, dict):
                continue
            bot_id, _, group_id = str(context_key).partition(':')
            try:
                await DailyWifeRecord.save_context(day, bot_id, group_id or 'direct', context)
            except SQLAlchemyError as exc:
                logger.error(f'{LOG_PREFIX} 保存每日记录到数据库失败: {exc}')


def _get_today_context(data: WifeData, ev: Event) -> DailyContext:
    day = data.setdefault('days', {}).setdefault(_today_key(), {})
    context = day.setdefault(_context_key(ev), {})
    # 记录桶由 ALL_DAILY_RECORD_KINDS 派生，而非逐行硬编码：该表是新增模式（如
    # normal）时唯一的真相源；漏建一个桶会使该模式的直接下标读取抛出 KeyError，
    # 异常被命令包装器吞掉后表现为「用户发命令没有任何回复」
    for kind in ALL_DAILY_RECORD_KINDS:
        context.setdefault(_daily_bucket_name(kind), {})
    context.setdefault('marry_members', {})
    context.setdefault('rob_attempts', {})
    context.setdefault('safe_wives', {})
    return context


async def _get_other_daily_wife_name(ev: Event, requested_kind: str) -> str | None:
    """返回用户当日在其它老婆池中已持有的角色名，用于互斥校验。

    返回 None 有两种含义：请求的模式不属于老婆池，或用户在其它的池中均无有效记录；
    调用方据此只需区分「存在冲突」与「无冲突」。
    """
    if requested_kind not in DAILY_WIFE_KINDS:
        return None
    context = await _load_daily_context(ev)
    user_key = _user_key(ev)
    for kind in DAILY_WIFE_KINDS:
        if kind == requested_kind:
            continue
        raw = context[_daily_bucket_name(kind)].get(user_key)
        if not _has_active_wife(raw):
            continue
        name = str(raw.get('name') or '').strip()
        if name:
            return name
    return None


def _record_to_dict(
    record: WifeRecord,
    ev: Event | None = None,
    user_id: str | int | None = None,
) -> RoleRecordValue:
    """将领域对象转换为可持久化的记录字典。

    未提供事件时只输出记录自身字段，用于不绑定会话上下文的场景；提供事件时补充归属与
    时间戳，其中日期取当前值：跨日后的写入必须落到新桶，否则会被次日的回收流程清除。
    """
    data: RoleRecordValue = {
        'name': record.name,
        'role_ids': list(record.role_ids),
        'image': record.image,
        'record_type': record.record_type,
    }
    if record.target_user_id:
        data['target_user_id'] = record.target_user_id
    if ev is not None:
        data.update(
            {
                'user_id': _user_key(ev, user_id),
                'display_name': name_from_event(ev, user_id),
                'group_id': str(ev.group_id or 'direct'),
                'bot_id': str(ev.bot_id),
                'day': _today_key(),
                'updated_at': int(time.time()),
            }
        )
    return data


def _record_from_dict(data: RoleRecordValue) -> WifeRecord | None:
    """从记录字典还原领域对象，结构非法、字段缺失或图片引用失效时返回 None。

    返回 None 而非抛出异常：历史数据可能残留已删除的图片路径，此处降级为「该记录不可
    用」，使单条损坏数据不至于让整轮抽签失败。群友头像与角色图片的判定标准不同——前者
    允许空值，后者必须有有效引用——因此两条分支必须分别校验。
    """
    try:
        record = WifeRecord(
            name=str(data['name']),
            role_ids=tuple(str(item) for item in data.get('role_ids', ())),
            image=str(data['image']),
            record_type=str(data.get('record_type') or 'role'),
            target_user_id=str(data.get('target_user_id') or ''),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.error(f'{LOG_PREFIX} 解析 Record 字典异常: {exc}')
        return None
    if not record.name:
        return None
    if record.record_type == 'member':
        if record.image and not _is_valid_image_ref(record.image):
            logger.debug(f'{LOG_PREFIX} 群友头像路径已失效: {record.image}')
            return None
        return record
    if not _is_valid_image_ref(record.image):
        logger.debug(f'{LOG_PREFIX} 角色图片路径已失效: {record.image}')
        return None
    return record


def _wife_state(raw: object) -> str:
    """判定记录的持有状态：owned 正常持有 / lost_stolen 被抢走 / lost_gifted 送出 / divorced 主动离婚。

    判定按固定优先级短路：离婚是用户的显式终局操作，优先级最高；被抢与送出均为被动
    失去，二者互斥，先判被抢。非字典输入按「正常持有」处理，以免历史脏数据被误判为
    已失去。
    """
    if not isinstance(raw, dict):
        return 'owned'
    if raw.get('divorced'):
        return 'divorced'
    if raw.get('stolen_by'):
        return 'lost_stolen'
    if raw.get('gifted_to'):
        return 'lost_gifted'
    return 'owned'


def _wife_origin(raw: object) -> str:
    """判定记录的来源：self 自己抽到 / robbed 抢来的 / gifted 别人送的 / safe 补偿发放。

    优先级对应字段的写入时序：抢来与送来必然同时带上来源标记，补偿记录则只带 safe
    标记，因此先判前两者不会误分类。
    """
    if not isinstance(raw, dict):
        return 'self'
    if raw.get('stolen_from'):
        return 'robbed'
    if raw.get('gifted_from'):
        return 'gifted'
    if raw.get('safe'):
        return 'safe'
    return 'self'


def _is_secondhand_wife(raw: object) -> bool:
    """判定是否为二手老婆：抢来的、别人送的或补偿发放的（到手即终结，不能再流转）。"""
    return _wife_origin(raw) in ('robbed', 'gifted', 'safe')


def _has_active_wife(raw: object) -> bool:
    """判定记录是否仍持有有效老婆（名字非空且状态为正常持有）。

    仅当名字非空且状态为 owned 时成立：被抢走、已送出或已离婚的记录仍留在桶中作为
    历史留痕，不能计入持有。
    """
    return isinstance(raw, dict) and bool(raw.get('name')) and _wife_state(raw) == 'owned'


def _mark_all_daily_records_divorced(
    context: DailyContext,
    user_key: str,
    divorced_at: int,
) -> list[tuple[str, str]]:
    """就地终止用户当日在全部模式中的婚姻记录，返回 (模式, 角色名) 列表。

    遍历所有已终结的模式而非仅当前模式，是为保证「离婚」的语义在跨模式间一致，否则
    用户可在离婚后继续以另一模式保有同一角色。无名字的记录直接跳过，已离婚的记录不
    重复计入返回列表，使调用方据此得到的通知条数与实际状态变更次数一致。
    """
    divorced: list[tuple[str, str]] = []
    for kind in ALL_DAILY_RECORD_KINDS:
        bucket = context[_daily_bucket_name(kind)]
        raw = bucket.get(user_key)
        if not isinstance(raw, dict) or not str(raw.get('name') or '').strip():
            continue
        if raw.get('divorced'):
            continue
        raw['divorced'] = True
        raw['divorced_at'] = divorced_at
        divorced.append((kind, str(raw['name'])))

    safe_record = context['safe_wives'].get(user_key)
    if isinstance(safe_record, dict) and str(safe_record.get('name') or '').strip() and not safe_record.get('divorced'):
        safe_record['divorced'] = True
        safe_record['divorced_at'] = divorced_at
        divorced.append(('safe_wife', str(safe_record['name'])))

    marry_record = context.get('marry_members', {}).get(user_key)
    if (
        isinstance(marry_record, dict)
        and str(marry_record.get('name') or '').strip()
        and not marry_record.get('divorced')
    ):
        marry_record['divorced'] = True
        marry_record['divorced_at'] = divorced_at
        divorced.append(('marry_member', str(marry_record['name'])))
    return divorced


async def _get_existing_daily_record(ev: Event, user_id: str | int, kind: str = 'wife') -> WifeRecord | None:
    """读取用户当日在指定模式下的记录，无记录或记录不可用时返回 None。"""
    context = await _load_daily_context(ev)
    current = context[_daily_bucket_name(kind)].get(_user_key(ev, user_id))
    if isinstance(current, dict):
        return _record_from_dict(current)
    return None


async def _get_existing_daily_wife_record(ev: Event, user_id: str | int) -> WifeRecord | None:
    """读取用户当日在默认老婆池中的记录，语义等价于以 'wife' 模式调用通用读取。"""
    return await _get_existing_daily_record(ev, user_id, 'wife')


def pending_write_count() -> int:
    """返回当前批次中待提交的写入条数（含删除），用于可观测性。

    该值只反映尚未提交的合并批次；已进入提交流程的批次不计入，因此不能作为落库进度
    的精确指标。
    """
    batch = _PENDING_BATCH
    if batch is None:
        return 0
    return len(batch.rows) + len(batch.deletes)
