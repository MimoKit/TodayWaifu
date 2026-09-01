"""Independent random-wife gallery command."""
from __future__ import annotations

from .shared import *
from .source_cache import AsyncSourceCache


_RANDOM_GALLERY_CACHE = AsyncSourceCache[dict[str, Any]](CACHE_TTL_SECONDS, max_entries=4)


def prune_random_gallery_cache() -> None:
    _RANDOM_GALLERY_CACHE.prune()


def invalidate_random_gallery_cache() -> None:
    _RANDOM_GALLERY_CACHE.invalidate()


def _random_gallery_api_url() -> str:
    return str(_cfg('DailyWifeRandomGalleryApiUrl') or '').strip()


def _parse_random_gallery_candidates(
    payload: dict[str, Any],
) -> tuple[RoleCandidate, ...]:
    roles_data = payload.get('roles')
    if not isinstance(roles_data, list) or not roles_data:
        raise RuntimeError('来点老婆图库没有返回可用角色。')

    candidates: list[RoleCandidate] = []
    for item in roles_data:
        if not isinstance(item, dict):
            continue
        role_ids_data = item.get('role_ids')
        if not isinstance(role_ids_data, list):
            continue
        role_ids = tuple(
            str(role_id).strip()
            for role_id in role_ids_data
            if str(role_id).strip()
        )
        if not role_ids:
            continue

        name = str(item.get('name') or role_ids[0]).strip()
        images_data = item.get('images')
        if not name or not isinstance(images_data, list):
            continue
        images: list[str] = []
        for image_item in images_data:
            if not isinstance(image_item, dict):
                continue
            image_url = str(image_item.get('url') or '').strip()
            parsed = urlparse(image_url)
            if parsed.scheme == 'https' and parsed.netloc and image_url not in images:
                images.append(image_url)
        if images:
            candidates.append(RoleCandidate(name, role_ids, tuple(images)))

    if not candidates:
        raise RuntimeError('来点老婆图库没有返回有效的 HTTPS 图片。')
    return tuple(candidates)


async def _send_random_wife(bot: Bot, ev: Event) -> None:
    api_url = _random_gallery_api_url()
    if not api_url:
        await _safe_send(bot, '未配置来点老婆图库接口。')
        return

    allowed, used, limit = await _consume_random_wife_quota(ev)
    if not allowed:
        logger.info(
            f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} '
            f'今天来点老婆次数已用尽({used}/{limit})'
        )
        await _safe_send(bot, f'今天的来点老婆次数已经用完啦（{limit}/{limit}），明天再来吧！')
        return

    try:
        payload = await _RANDOM_GALLERY_CACHE.get(
            api_url,
            lambda: asyncio.to_thread(_fetch_gallery_payload_from_url_sync, api_url),
        )
        candidates = _parse_random_gallery_candidates(payload)
        role = random.choice(candidates)
        image_url = random.choice(role.images)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            message = '图库访问令牌无效或未配置。'
        else:
            message = f'请求来点老婆图库失败，HTTP {exc.code}。'
        logger.warning(f'{LOG_PREFIX} {message}')
        await _refund_random_wife_quota(ev)
        await _safe_send(bot, message)
        return
    except (RuntimeError, URLError, TimeoutError, OSError) as exc:
        message = str(exc) or '读取来点老婆图库失败。'
        logger.warning(f'{LOG_PREFIX} 读取来点老婆图库失败: {message}')
        await _refund_random_wife_quota(ev)
        await _safe_send(bot, message)
        return

    if limit > 0:
        logger.info(
            f'{LOG_PREFIX} 用户 {ev.user_id} 在群 {ev.group_id or "direct"} '
            f'使用来点老婆({used}/{limit})'
        )

    await _send_role_image(
        bot,
        role,
        image_url,
        '你的老婆来啦！',
        ev.user_id,
        bool(ev.group_id),
    )


@random_wife_sv.on_fullmatch('来点老婆', block=True)
async def random_wife(bot: Bot, ev: Event) -> None:
    await _send_random_wife(bot, ev)
