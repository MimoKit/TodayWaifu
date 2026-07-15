"""TodayWaifu - Punishing: Gray Raven daily wife gallery."""
from __future__ import annotations

from .folder_gallery import scan_named_role_directories
from .shared import *  # noqa: F403


PGR_WIFE_DIR_NAME = 'pgr_wife'


def _pgr_wife_root() -> Path:
    return get_res_path('TodayWaifu') / PGR_WIFE_DIR_NAME


def _load_pgr_wife_candidates() -> tuple[RoleCandidate, ...]:
    rows = scan_named_role_directories(_pgr_wife_root(), IMAGE_EXTENSIONS)
    return tuple(
        RoleCandidate(name=name, role_ids=(name,), images=images)
        for name, images in rows
    )


async def _ensure_daily_pgr_wife_record(ev: Event) -> WifeRecord | None:
    key = _user_key(ev)
    data = _load_wife_data()
    context = _get_today_context(data, ev)
    current = context['pgr_wives'].get(key)
    if isinstance(current, dict):
        record = _record_from_dict(current)
        if record is not None:
            if await _nsfw_record_passes(record):
                return record
            context['pgr_wives'].pop(key, None)
            _save_wife_data(data)

    candidates = _load_pgr_wife_candidates()
    if not candidates:
        return None
    rng = _daily_rng(ev, key, 'pgr_wife')
    chosen = await _pick_nsfw_checked_role_record(candidates, rng, 'pgr')
    if chosen is None:
        return None

    data = _load_wife_data()
    context = _get_today_context(data, ev)
    existing = context['pgr_wives'].get(key)
    if isinstance(existing, dict):
        existing_record = _record_from_dict(existing)
        if existing_record is not None:
            return existing_record
    context['pgr_wives'][key] = _record_to_dict(chosen, ev, key)
    _save_wife_data(data)
    logger.info(f'{LOG_PREFIX} 为用户 {key} 生成新的战双老婆: {chosen.name}')
    return chosen


async def _send_daily_pgr_wife(bot: Bot, ev: Event) -> None:
    record = await _ensure_daily_pgr_wife_record(ev)
    if record is None:
        root = _pgr_wife_root()
        return await _send_prefixed(
            bot,
            '战双老婆图库暂无可用图片。\n'
            f'请按“{root}/角色中文名/图片文件”的结构放入图片。',
            kind='pgr',
        )

    text = f'你今天的战双老婆是{record.name}' if _cfg_bool('DailyWifeSendText', True) else None
    await _send_local_image(
        bot,
        record.image,
        '战双老婆图片文件不存在，请稍后重试。',
        text,
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
