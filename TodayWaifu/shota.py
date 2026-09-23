"""TodayWaifu - shota module (今日正太)."""
from __future__ import annotations

from .shared import (
    LOG_PREFIX,
    CACHE_TTL_SECONDS,
    Bot,
    Event,
    URLError,
    HTTPError,
    WifeRecord,
    GalleryPayload,
    RoleRecordValue,
    _cfg,
    json,
    logger,
    asyncio,
    shota_sv,
    _user_key,
    _daily_rng,
    _wife_state,
    _shota_enabled,
    _record_to_dict,
    _send_shota_text,
    _record_from_dict,
    _daily_context_lock,
    _load_daily_context,
    _save_daily_records,
    _http_get_with_retry,
    _send_shota_result_image,
)
from .image_input import image_hash_id
from .source_cache import AsyncSourceCache
from .disabled_card import send_feature_disabled_notice

_SHOTA_SOURCE_CACHE = AsyncSourceCache[tuple[str, ...]](CACHE_TTL_SECONDS, max_entries=4)


def _shota_image_hash_id(source: str) -> str:
    return image_hash_id(source)


def _shota_record_name(image: str) -> str:
    return f'正太图{_shota_image_hash_id(image)}'


def _normalize_shota_api_url(url: str) -> str:
    clean = url.strip()
    if not clean:
        return ''
    if not clean.startswith(('http://', 'https://')):
        return f'https://{clean}'
    return clean


def _parse_shota_image_urls(payload: GalleryPayload) -> tuple[str, ...]:
    if 'roles' not in payload or not isinstance(payload['roles'], list):
        raise RuntimeError('正太图库接口缺少 roles 列表。')

    matched_urls: list[str] = []
    fallback_urls: list[str] = []
    seen_urls: set[str] = set()

    for role_data in payload['roles']:
        if not isinstance(role_data, dict):
            continue
        role_ids = tuple(
            str(role_id).strip()
            for role_id in role_data.get('role_ids', [])
            if isinstance(role_id, (str, int))
        )
        images = role_data.get('images')
        if not isinstance(images, list):
            continue

        is_shota_role = 'shota' in role_ids or not role_ids
        for image_data in images:
            if not isinstance(image_data, dict):
                continue
            url = image_data.get('url')
            if not isinstance(url, str):
                continue
            clean_url = url.strip()
            if not clean_url.startswith(('http://', 'https://')):
                continue
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)
            if is_shota_role:
                matched_urls.append(clean_url)
            else:
                fallback_urls.append(clean_url)

    final_urls = matched_urls or fallback_urls
    if not final_urls:
        raise RuntimeError('正太图库接口没有可用图片。')
    return tuple(final_urls)


def _fetch_shota_image_urls_sync(api_url: str) -> tuple[str, ...]:
    normalized_url = _normalize_shota_api_url(api_url)
    if not normalized_url:
        raise RuntimeError('未配置正太图库接口地址。')
    try:
        body = _http_get_with_retry(normalized_url, timeout=15)
    except HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError(
                '请求正太图库接口失败(403)：图库接口需要访问令牌，请在控制台配置「图库访问令牌」(DailyWifeGalleryToken)。'
            ) from exc
        raise RuntimeError(f'请求正太图库接口失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'请求正太图库接口失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('请求正太图库接口超时。') from exc
    except OSError as exc:
        raise RuntimeError(f'请求正太图库接口失败：{exc}') from exc

    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('正太图库接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('正太图库接口返回格式不正确。')
    return _parse_shota_image_urls(payload)


def _shota_unavailable_text(record_data: RoleRecordValue) -> str | None:
    state = _wife_state(record_data)
    if state == 'lost_stolen':
        robber = record_data.get('stolen_by_name') or record_data.get('stolen_by') or ''
        return f'你的正太已经被{robber}抢走了，今天就先忍忍吧~'
    if state == 'lost_gifted':
        receiver = record_data.get('gifted_to_name') or record_data.get('gifted_to') or ''
        return f'你的正太已经送给{receiver}了，今天就先忍忍吧~'
    if state == 'divorced':
        return '你今天已经和正太离婚了，明天再来吧~'
    return None


async def _roll_shota_record(
    ev: Event,
    user_key: str,
) -> tuple[WifeRecord | None, str | None]:
    custom_url = str(_cfg('DailyShotaGalleryApiUrl') or 'https://zt.mimokit.dpdns.org').strip()
    if not custom_url:
        return None, '未配置正太图库接口地址。'

    logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日正太列表，接口: {custom_url}')
    try:
        image_urls = await _SHOTA_SOURCE_CACHE.get(
            custom_url,
            lambda: asyncio.to_thread(_fetch_shota_image_urls_sync, custom_url),
        )
    except RuntimeError as exc:
        logger.warning(f'{LOG_PREFIX} 远程正太接口失败: {exc}')
        return None, str(exc)

    image_url = _daily_rng(ev, user_key, 'shota').choice(image_urls)
    return (
        WifeRecord(
            name=_shota_record_name(image_url),
            role_ids=('shota',),
            image=image_url,
            record_type='shota',
        ),
        None,
    )


def _get_shota_text() -> str:
    template = str(_cfg('DailyShotaTextTemplate') or '').strip()
    return template if template else '你今天的正太来啦！'


async def _send_shota_record(
    bot: Bot,
    ev: Event,
    record: WifeRecord,
    text: str | None = None,
) -> None:
    message_text = text if text is not None else _get_shota_text()
    await _send_shota_result_image(
        bot,
        record.image,
        message_text,
        ev.user_id,
        ev.group_id is not None,
        kind='shota',
    )


async def _send_shota_image(bot: Bot, ev: Event) -> None:
    context = await _load_daily_context(ev)
    user_key = _user_key(ev)
    current = context.get('shotas', {}).get(user_key)
    if isinstance(current, dict):
        unavailable_text = _shota_unavailable_text(current)
        if unavailable_text is not None:
            return await _send_shota_text(bot, unavailable_text)
        record = _record_from_dict(current)
        if record is not None:
            return await _send_shota_record(bot, ev, record)

    record, error = await _roll_shota_record(ev, user_key)
    if record is None:
        return await _send_shota_text(bot, error or '暂无正太图片')

    response_text: str | None = None
    selected_record = record
    async with _daily_context_lock(ev):
        save_context = await _load_daily_context(ev)
        existing = save_context.get('shotas', {}).get(user_key)
        if isinstance(existing, dict):
            unavailable_text = _shota_unavailable_text(existing)
            if unavailable_text is not None:
                response_text = unavailable_text
            else:
                existing_record = _record_from_dict(existing)
                if existing_record is not None:
                    selected_record = existing_record
                else:
                    replacement = _record_to_dict(record, ev, user_key)
                    updated_existing = dict(existing)
                    for key in ('name', 'role_ids', 'image', 'record_type', 'updated_at'):
                        updated_existing[key] = replacement[key]
                    await _save_daily_records(ev, [('shotas', user_key, updated_existing)])
        else:
            await _save_daily_records(
                ev,
                [('shotas', user_key, _record_to_dict(record, ev, user_key))],
            )

    if response_text is not None:
        return await _send_shota_text(bot, response_text)
    await _send_shota_record(bot, ev, selected_record)


# ── 触发器注册 ────────────────────────────────────────────────────────────────

@shota_sv.on_fullmatch(
    '今日正太',
    block=True,
    to_ai="""随机抽取当前用户今天的正太图片。
    当用户说“今日正太”“抽一张正太”“我今天的正太是谁”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['正太图片每日随机抽取（图库或本地图库）'],
    aliases=['今日老婆·抽正太', '今日老婆·今日正太'],
)
async def daily_shota(bot: Bot, ev: Event) -> None:
    if not _shota_enabled():
        return await send_feature_disabled_notice(bot, 'shota')
    await _send_shota_image(bot, ev)
