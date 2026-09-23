"""TodayWaifu 的每日记录持久化与记录转换。"""
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
from .members import _user_display_name
from .senders import _is_valid_image_ref
from .payloads import WifeData, DailyContext, RoleRecordValue
from .constants import LOG_PREFIX, DAILY_WIFE_KINDS, ALL_DAILY_RECORD_KINDS, _daily_bucket_name
from .invalidation import _invalidate_status_cache


def _daily_context_lock(ev: Event) -> asyncio.Lock:
    """返回按 bot/group 分片的每日记录锁，避免不同群互相阻塞。"""
    return _CONTEXT_REGISTRY.lock_for(_daily_context_key(ev))


# 最近一次见到的日期，用于在翻转瞬间立即回收上一天的上下文快照
_LAST_CONTEXT_DAY: str | None = None


def _roll_over_context_day(day: str) -> int:
    """日期翻转时立即回收上一天的上下文快照，返回回收数量。

    不这么做的话，零点到凌晨 1 点之间内存里会同时躺着两天的全部活跃群上下文，
    对几千个群的 bot 是几百 MB 级的额外占用与 GC 压力。
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
    """按上下文 hydrate 一次每日快照；数据库失败直接向调用方传播。"""
    key = _daily_context_key(ev)
    _roll_over_context_day(key.day)
    cached = _CONTEXT_REGISTRY.get(key)
    if cached is not None:
        return cached
    task = _CONTEXT_REGISTRY.inflight.get(key)
    if task is None:
        async def hydrate() -> DailyContext:
            context = await DailyWifeRecord.get_context(
                key.day,
                key.bot_id,
                key.group_id,
            )
            data = {'days': {key.day: {_context_key(ev): context}}}
            result = _get_today_context(data, ev)
            _CONTEXT_REGISTRY.put(key, result)
            _DAILY_CONTEXT_CACHE[key.cache_key] = (key.day, result)
            return result
        task = asyncio.create_task(hydrate())
        _CONTEXT_REGISTRY.inflight[key] = task
    try:
        return await task
    finally:
        if task.done() and _CONTEXT_REGISTRY.inflight.get(key) is task:
            _CONTEXT_REGISTRY.inflight.pop(key, None)


async def _save_daily_records(
    ev: Event,
    records: list[tuple[str, str, RoleRecordValue | bool | None]],
    deletes: list[tuple[str, str]] | None = None,
) -> None:
    """在一个事务中定向提交少量记录，并同步当前上下文缓存。"""
    key = _daily_context_key(ev)
    await DailyWifeRecord.upsert_records(
        key.day, key.bot_id, key.group_id, records, deletes
    )
    context = await _load_daily_context(ev)
    for bucket, user_key, value in records:
        context.setdefault(bucket, {})[str(user_key)] = value
    for bucket, user_key in deletes or ():
        bucket_data = context.get(bucket)
        if isinstance(bucket_data, dict):
            bucket_data.pop(str(user_key), None)
    _CONTEXT_REGISTRY.put(key, context, _CONTEXT_REGISTRY.generation(key))
    _invalidate_status_cache()


async def _save_daily_record(
    ev: Event, bucket: str, user_key: str, value: RoleRecordValue | bool | None
) -> None:
    """提交单条记录，成功后才更新内存快照。"""
    day = _today_key()
    bot_id, _, group_id = _context_key(ev).partition(':')
    await DailyWifeRecord.upsert_record(
        day, bot_id, group_id or 'direct', bucket, user_key, value
    )
    context = await _load_daily_context(ev)
    context.setdefault(bucket, {})[user_key] = value
    _CONTEXT_REGISTRY.put(_daily_context_key(ev), context, _CONTEXT_REGISTRY.generation(_daily_context_key(ev)))
    _invalidate_status_cache()


async def _delete_daily_record(ev: Event, bucket: str, user_key: str) -> None:
    """删除单条记录，成功后才同步内存快照。"""
    day = _today_key()
    bot_id, _, group_id = _context_key(ev).partition(':')
    await DailyWifeRecord.delete_record(
        day, bot_id, group_id or 'direct', bucket, user_key
    )
    context = await _load_daily_context(ev)
    bucket_data = context.get(bucket)
    if isinstance(bucket_data, dict):
        bucket_data.pop(user_key, None)
    _CONTEXT_REGISTRY.put(_daily_context_key(ev), context, _CONTEXT_REGISTRY.generation(_daily_context_key(ev)))
    _invalidate_status_cache()


async def _save_daily_context(ev: Event, context: DailyContext) -> None:
    """兼容路径整体提交上下文；成功后才发布新的内存快照。"""
    key = _daily_context_key(ev)
    snapshot = copy.deepcopy(context)
    await DailyWifeRecord.upsert_context(
        key.day, key.bot_id, key.group_id, snapshot
    )
    _CONTEXT_REGISTRY.put(key, snapshot)
    _DAILY_CONTEXT_CACHE[key.cache_key] = (key.day, snapshot)
    _invalidate_status_cache()


async def _load_wife_data() -> WifeData:
    """从数据库加载今天的全部记录，返回与旧 JSON 相同的 {'days': {today: {context: ...}}} 结构。"""
    today = _today_key()
    contexts = await DailyWifeRecord.load_day(today)
    return {'days': {today: contexts}}


async def _save_wife_data(data: WifeData) -> None:
    """把 {'days': {day: {context_key: context}}} 结构整体写回数据库（按 context 先删后插）。"""
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
    context.setdefault('wives', {})
    context.setdefault('husbands', {})
    context.setdefault('nte_wives', {})
    context.setdefault('pgr_wives', {})
    context.setdefault('lolis', {})
    context.setdefault('shotas', {})
    context.setdefault('marry_members', {})
    context.setdefault('rob_attempts', {})
    context.setdefault('safe_wives', {})
    return context


async def _get_other_daily_wife_name(ev: Event, requested_kind: str) -> str | None:
    """返回用户今天在其他老婆池已有的角色名。"""
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
                'display_name': _user_display_name(ev, user_id),
                'group_id': str(ev.group_id or 'direct'),
                'bot_id': str(ev.bot_id),
                'day': _today_key(),
                'updated_at': int(time.time()),
            }
        )
    return data


def _record_from_dict(data: RoleRecordValue) -> WifeRecord | None:
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
    """返回记录持有状态：owned 正常持有 / lost_stolen 被抢走 / lost_gifted 送出去 / divorced 主动离婚。"""
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
    """返回老婆记录的来源：self 自己抽到 / robbed 抢来的 / gifted 别人送的。"""
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
    """二手老婆 = 抢来的/别人送的/补偿抽的（到手即终结，不能再流转）。"""
    return _wife_origin(raw) in ('robbed', 'gifted', 'safe')


def _has_active_wife(raw: object) -> bool:
    """是否仍持有一个有效（未离手）的老婆。"""
    return isinstance(raw, dict) and bool(raw.get('name')) and _wife_state(raw) == 'owned'


def _mark_all_daily_records_divorced(
    context: DailyContext,
    user_key: str,
    divorced_at: int,
) -> list[tuple[str, str]]:
    """一次性终止用户今天在全部模式中的婚姻记录。"""
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
    if (
        isinstance(safe_record, dict)
        and str(safe_record.get('name') or '').strip()
        and not safe_record.get('divorced')
    ):
        safe_record['divorced'] = True
        safe_record['divorced_at'] = divorced_at
        divorced.append(('safe_wife', str(safe_record['name'])))
    return divorced


async def _get_existing_daily_record(ev: Event, user_id: str | int, kind: str = 'wife') -> WifeRecord | None:
    context = await _load_daily_context(ev)
    current = context[_daily_bucket_name(kind)].get(_user_key(ev, user_id))
    if isinstance(current, dict):
        return _record_from_dict(current)
    return None


async def _get_existing_daily_wife_record(ev: Event, user_id: str | int) -> WifeRecord | None:
    return await _get_existing_daily_record(ev, user_id, 'wife')
