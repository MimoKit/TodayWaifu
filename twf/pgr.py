"""战双帕弥什本地图库抽取。"""
from __future__ import annotations

from .shared import *  # noqa: F403


def _load_pgr_wife_candidates() -> tuple[RoleCandidate, ...]:
    return _load_pgr_local_candidates()


def _stored_pgr_record(raw: Any) -> WifeRecord | None:
    if not isinstance(raw, dict):
        return None
    record = _record_from_dict(raw)
    if record is None or not Path(record.image).is_file():
        return None
    return record


async def _ensure_daily_pgr_wife_record(ev: Event) -> WifeRecord | None:
    key = _user_key(ev)
    bucket = _daily_bucket_name('pgr')
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = _stored_pgr_record(context[bucket].get(key))
    if current is not None:
        if await _nsfw_record_passes(current):
            return current
        context[bucket].pop(key, None)
        _save_wife_data(data)

    candidates = _load_pgr_wife_candidates()
    if not candidates:
        return None
    chosen = await _pick_nsfw_checked_role_record(
        candidates,
        _daily_rng(ev, key, 'pgr_wife'),
        'pgr',
    )
    if chosen is None:
        return None

    # await 后重新读取，避免并发命令覆盖已经生成的结果。
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    existing = _stored_pgr_record(context[bucket].get(key))
    if existing is not None:
        return existing
    context[bucket][key] = _record_to_dict(chosen, ev, key)
    _save_wife_data(data)
    logger.info(f'{LOG_PREFIX} 为用户 {key} 生成新的战双老婆: {chosen.name}')
    return chosen


def _pgr_result_text(record: WifeRecord) -> str | None:
    if not _cfg_bool('DailyWifeSendText', True):
        return None
    metadata = _daily_kind_metadata('pgr')
    template = str(_cfg(metadata.text_template_key) or metadata.text_template_default)
    return template.format(name=record.name, role_id='/'.join(record.role_ids))


async def _send_daily_pgr_wife(bot: Bot, ev: Event) -> None:
    if not _cfg_bool('DailyWifePgrEnabled', True):
        return await _send_prefixed(bot, '今日战双老婆功能当前已关闭。', kind='pgr')

    other_wife_name = _get_other_daily_wife_name(ev, 'pgr')
    if other_wife_name:
        return await _send_prefixed(
            bot,
            f'你今天已经有{other_wife_name}了，不要贪心！',
            kind='pgr',
        )

    record = await _ensure_daily_pgr_wife_record(ev)
    if record is None:
        root = _pgr_wife_root()
        return await _send_prefixed(
            bot,
            '战双老婆图库里还没有可用图片。\n'
            f'把图片放成“{root}\\角色名\\图片文件”即可。',
            kind='pgr',
        )

    await _send_local_image(
        bot,
        record.image,
        '这张战双老婆图片已经不存在，请重新发送命令。',
        _pgr_result_text(record),
        ev.user_id,
        ev.group_id is not None,
        'pgr',
    )


@pgr_wife_sv.on_fullmatch(
    ('今日战双老婆', 'jrzslp'),
    block=True,
    to_ai="""随机抽取当前用户今天的战双老婆。
    当用户说“今日战双老婆”“jrzslp”“我今天的战双老婆是谁”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_pgr_wife(bot: Bot, ev: Event) -> None:
    await _send_daily_pgr_wife(bot, ev)
