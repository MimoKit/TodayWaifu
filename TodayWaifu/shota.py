"""正太图片的每日抽取：仅走远程图库，并复用萝莉的候选缓存与投递通道。

与萝莉不同，正太没有本地图片来源，因此图库不可用时没有回退路径，只能把失败原因上报给
用户。记录写入每日上下文的 shotas 分桶并随日期翻转失效，保证同一用户当天只有一张正太。
"""
from __future__ import annotations

from .shared import (
    LOG_PREFIX,
    CACHE_TTL_SECONDS,
    DEFAULT_GALLERY_BASE_URL,
    GALLERY_HTTP_TIMEOUT_SECONDS,
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
    shota_sv,
    _user_key,
    _daily_rng,
    _wife_state,
    _apply_no_r18,
    _shota_enabled,
    _record_to_dict,
    _send_shota_text,
    _record_from_dict,
    _auth_error_reason,
    _daily_context_lock,
    _load_daily_context,
    _save_daily_records,
    _http_get_with_retry,
    _send_shota_result_image,
)
from .executor import run_blocking
from .image_input import image_hash_id
from .source_cache import AsyncSourceCache

_SHOTA_SOURCE_CACHE = AsyncSourceCache[tuple[str, ...]](CACHE_TTL_SECONDS, max_entries=4)


def _shota_image_hash_id(source: str) -> str:
    # 转发：内容标识算法集中实现，正太与萝莉共用同一套，避免两边对同一图片算出不同标识
    return image_hash_id(source)


def _shota_record_name(image: str) -> str:
    # 名称内嵌内容标识：抽取与抢/送流程共享同一命名，因此按名称即可核对是否为同一张图
    return f'正太图{_shota_image_hash_id(image)}'


def _normalize_shota_api_url(url: str) -> str:
    clean = url.strip()
    # 空串在此保留原样，由调用方转成「未配置接口地址」提示，而不是替用户猜测一个默认主机
    if not clean:
        return ''
    # 容忍用户只填写主机名：补全为 HTTPS，避免把裸主机名交给 URL 解析器而得到畸形请求
    if not clean.startswith(('http://', 'https://')):
        return f'https://{clean}'
    return clean


def _parse_shota_image_urls(payload: GalleryPayload) -> tuple[str, ...]:
    # roles 结构缺失属于接口契约破裂，直接抛错：正太没有本地回退，静默返回空集只会让用户
    # 看到「没有可用图片」这一误导性提示
    if 'roles' not in payload or not isinstance(payload['roles'], list):
        raise RuntimeError('正太图库接口缺少 roles 列表。')

    # 与萝莉的严格校验不同，此处对单项格式错误采取跳过而非中止：图库可能混有其他作品的
    # 角色条目，逐条丢弃可以保住正常部分；候选按是否带 shota 标签分为两档
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

        # 缺少 role_ids 的条目按正太处理：图库的历史数据中存在未标注标签的条目，若视为非正太
        # 会凭空丢图
        is_shota_role = 'shota' in role_ids or not role_ids
        for image_data in images:
            if not isinstance(image_data, dict):
                continue
            url = image_data.get('url')
            if not isinstance(url, str):
                continue
            clean_url = url.strip()
            # 仅接受绝对 HTTP(S) 地址：其它形态无法在发送阶段下载，提前丢弃以免抽中即失败
            if not clean_url.startswith(('http://', 'https://')):
                continue
            if clean_url in seen_urls:
                continue
            seen_urls.add(clean_url)
            if is_shota_role:
                matched_urls.append(clean_url)
            else:
                fallback_urls.append(clean_url)

    # 优先使用带标签的候选，仅在其为空时才退到混合池：这样正常数据下不会抽到其它作品角色，
    # 同时又能在标签整体缺失时继续服务
    final_urls = matched_urls or fallback_urls
    if not final_urls:
        raise RuntimeError('正太图库接口没有可用图片。')
    return tuple(final_urls)


def _fetch_shota_image_urls_sync(api_url: str) -> tuple[str, ...]:
    normalized_url = _normalize_shota_api_url(api_url)
    if not normalized_url:
        raise RuntimeError('未配置正太图库接口地址。')
    try:
        body = _http_get_with_retry(normalized_url, timeout=GALLERY_HTTP_TIMEOUT_SECONDS)
    except HTTPError as exc:
        # 401/403/429 由统一映射转成可操作提示（令牌失效 / IP 封禁 / 限流），单看状态码无法
        # 区分该改配置还是稍后重试
        if exc.code in {401, 403, 429}:
            raise RuntimeError(_auth_error_reason(exc, what='请求正太图库接口')) from exc
        raise RuntimeError(f'请求正太图库接口失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'请求正太图库接口失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('请求正太图库接口超时。') from exc
    except OSError as exc:
        raise RuntimeError(f'请求正太图库接口失败：{exc}') from exc

    # 传输层与解析层错误统一收敛为 RuntimeError，便于调用方原样上报失败原因
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
        # 昵称优先于 ID：ID 是昵称缺失时的保底，始终取到非空值以免文案出现空主语
        robber = record_data.get('stolen_by_name') or record_data.get('stolen_by') or ''
        return f'你的正太已经被{robber}抢走了，今天就先忍忍吧~'
    if state == 'lost_gifted':
        receiver = record_data.get('gifted_to_name') or record_data.get('gifted_to') or ''
        return f'你的正太已经送给{receiver}了，今天就先忍忍吧~'
    if state == 'divorced':
        return '你今天已经和正太离婚了，明天再来吧~'
    return None


def _shota_api_url() -> str:
    base = str(_cfg('DailyWifeApiUrl') or DEFAULT_GALLERY_BASE_URL).strip().rstrip('/')
    # 兼容用户按旧文档把统一地址直接填到 /shota 或 /zt 的情况；其余地址补 /shota，避免拼出
    # /shota/shota 这类无效路径
    if base.endswith('/shota') or base.endswith('/zt'):
        return base
    return _apply_no_r18(f'{base}/shota')


async def _roll_shota_record(
    ev: Event,
    user_key: str,
) -> tuple[WifeRecord | None, str | None]:
    custom_url = _shota_api_url()
    # 正太没有本地图库，空地址意味着无候选可用，只能以明确原因返回
    if not custom_url:
        return None, '未配置正太图库接口地址。'

    logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日正太列表，接口: {custom_url}')
    try:
        image_urls = await _SHOTA_SOURCE_CACHE.get(
            custom_url,
            lambda: run_blocking(_fetch_shota_image_urls_sync, custom_url),
        )
    except RuntimeError as exc:
        # 不吞掉异常原因：远程失败是用户唯一能看到的线索，直接向上传递
        logger.warning(f'{LOG_PREFIX} 远程正太接口失败: {exc}')
        return None, str(exc)

    # 以「日期 + 用户 + 群」为种子的确定性随机：当天重复调用必然取到同一张图
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
    # 配置为空或仅含空白时使用内置文案：空模板会让图片缺少文字说明
    return template if template else '你今天的正太来啦！'


async def _send_shota_record(
    bot: Bot,
    ev: Event,
    record: WifeRecord,
    text: str | None = None,
) -> None:
    # text 为 None 表示调用方未指定文案，此时才读取配置模板；空字符串是合法值，不应被替换
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

    # 抽签包含远程请求，期间其它协程可能已为同一用户写入记录。锁内重新加载并复用既有记录，
    # 「同一用户当天只有一张」在并发下才依然成立。
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
                    # 只替换记录本体字段：来源状态由抢与赠送流程写入，整体覆盖会丢失「二手」
                    # 标记，使已易手的正太重新变得可抢
                    replacement = _record_to_dict(record, ev, user_key)
                    updated_existing = dict(existing)
                    for key in ('name', 'role_ids', 'image', 'record_type', 'updated_at'):
                        updated_existing[key] = replacement[key]
                    await _save_daily_records(ev, [('shotas', user_key, updated_existing)])
        else:
            # 首次抽取必须落库：离婚、被抢、赠送等交互都以每日记录为唯一入口，缺少记录时它们
            # 无法判定状态，只能回复「今天还没有正太」
            await _save_daily_records(
                ev,
                [('shotas', user_key, _record_to_dict(record, ev, user_key))],
            )

    if response_text is not None:
        return await _send_shota_text(bot, response_text)
    await _send_shota_record(bot, ev, selected_record)


# ── 触发器注册 ────────────────────────────────────────────────────────────────
# block=True：命令命中后不再交给后续处理器，避免同一条消息被多个功能重复消费。
# to_ai 文本是 AI 工具的对外描述（调用时机与参数语义），与命令文案同属兼容性契约。


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
    # 开关关闭时静默返回：不回复任何内容，避免关闭功能后仍产生噪音
    if not _shota_enabled():
        return
    await _send_shota_image(bot, ev)
