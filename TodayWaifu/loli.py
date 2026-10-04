"""萝莉图片的每日抽取：远程图库与本地目录二选一，并为抢/送/离婚保留稳定记录。

图片来源由 `_image_source('loli')` 决定：gallery 请求远程接口并回退本地，local 完全不
发起远程请求。两条路径产出的记录都写入每日上下文的 lolis 分桶，因此抢、送、离婚等交互
读到的始终是同一张图片；记录随日期翻转失效，构成「同一用户当天只有一张萝莉」的约束。
"""
from __future__ import annotations

from .shared import (
    LOG_PREFIX,
    IMAGE_EXTENSIONS,
    CACHE_TTL_SECONDS,
    UPLOAD_IMAGE_MAX_BYTES,
    DEFAULT_GALLERY_BASE_URL,
    GALLERY_HTTP_TIMEOUT_SECONDS,
    Bot,
    Path,
    Event,
    Message,
    URLError,
    HTTPError,
    WifeRecord,
    GalleryPayload,
    MessageSegment,
    RoleRecordValue,
    re,
    _cfg,
    json,
    time,
    logger,
    shutil,
    loli_sv,
    _user_key,
    _daily_rng,
    _safe_send,
    _wife_state,
    _apply_no_r18,
    _image_source,
    _loli_enabled,
    loli_manage_sv,
    _record_to_dict,
    _send_loli_text,
    image_upload_sv,
    _loli_image_root,
    _record_from_dict,
    _auth_error_reason,
    _can_upload_images,
    _daily_context_lock,
    _load_daily_context,
    _save_daily_records,
    _http_get_with_retry,
    _send_loli_result_image,
    _image_message_from_path,
)
from .executor import run_blocking
from .image_input import (
    image_hash_id,
    read_image_bytes,
    collect_image_refs,
    detect_image_suffix,
    image_suffix_from_source,
)
from .source_cache import AsyncSourceCache

_LOLI_SOURCE_CACHE = AsyncSourceCache[tuple[str, ...]](CACHE_TTL_SECONDS, max_entries=4)


# ── 本地图片目录读取 ─────────────────────────────────────────────────────────

# 萝莉图片列表缓存：rglob 全量扫描的开销随图片数量线性增长，零点高峰每条指令都触发会占满
# 核心的执行额度。以 300 秒 TTL 换取扫描次数下降；上传与删除路径显式失效缓存，使新图片的
# 可见延迟只取决于该次操作，而不必等待 TTL 到期。
_LOLI_PATHS_CACHE_TTL = 300.0
_loli_paths_cache: tuple[float, tuple[Path, ...]] | None = None


def _invalidate_loli_paths_cache() -> None:
    # 置空而非清空内容：下次读取时整体重扫，避免失效与写入并发时读到半更新状态
    global _loli_paths_cache
    _loli_paths_cache = None


def _loli_image_paths() -> tuple[Path, ...]:
    global _loli_paths_cache
    now = time.time()
    # 命中期内直接复用元组：返回值不可变，调用方无法破坏缓存内容
    if _loli_paths_cache is not None and now - _loli_paths_cache[0] < _LOLI_PATHS_CACHE_TTL:
        return _loli_paths_cache[1]
    root = _loli_image_root()
    # 目录缺失按空图库处理而非抛错：首次使用、目录被外部清理都属正常状态，由调用方提示
    if not root.is_dir():
        paths: tuple[Path, ...] = ()
    else:
        # 按小写路径排序，使列表序号与文件系统的枚举顺序无关。抽取按列表下标进行，顺序一旦
        # 漂移，同一用户当天重复调用就可能得到不同图片，破坏「当天固定」的承诺。
        paths = tuple(
            sorted(
                (
                    path
                    for path in root.rglob('*')
                    if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
                ),
                key=lambda p: str(p).lower(),
            )
        )
    _loli_paths_cache = (now, paths)
    return paths


def _delete_loli_images() -> int:
    # 先统计再删除：顺序颠倒后无法再计数，而返回值要用于提示用户删除数量
    root = _loli_image_root()
    count = len(_loli_image_paths())
    if root.exists():
        shutil.rmtree(root) if root.is_dir() else root.unlink()
    # 路径缓存与远程源缓存都必须失效：前者决定本地候选，后者决定图库候选，任一残留都会让
    # 已删除的图片在缓存期内继续被抽中
    _invalidate_loli_paths_cache()
    _LOLI_SOURCE_CACHE.invalidate()
    return count


# ── 上传辅助 ─────────────────────────────────────────────────────────────────

def _loli_image_hash_id(path: Path | str) -> str:
    # 转发而非直接引用：本模块对外暴露固定的函数名，标识算法集中在 image_input 中演进
    return image_hash_id(path)


def _loli_upload_refs(ev: Event) -> tuple[str, ...]:
    # 转发：图片引用来源可能随平台协议扩展，收口在 image_input 中处理
    return collect_image_refs(ev)


def _image_suffix_from_source(source: str) -> str:
    # 转发：后缀推断需覆盖 URL、base64 与本地路径三类来源，实现留在 image_input
    return image_suffix_from_source(source)


def _detect_image_suffix(data: bytes, source: str) -> str:
    # 转发：以字节魔数为准而非信任来源声明，避免错误后缀写出无法解码的图片
    return detect_image_suffix(data, source)


def _read_loli_image_bytes(source: str) -> tuple[bytes, str] | None:
    # 绑定本功能的上传体积上限：超限时返回 None，由调用方计入失败而不中断整批上传
    return read_image_bytes(source, UPLOAD_IMAGE_MAX_BYTES)


def _unique_loli_path(root: Path, suffix: str, index: int) -> Path:
    # 毫秒时间戳在同一批次内可能重复，用自增尾号消解冲突，避免后写覆盖先写的图片
    stamp = int(time.time() * 1000)
    counter = 0
    while True:
        tail = f'_{counter}' if counter else ''
        path = root / f'loli_{stamp}_{index}{tail}{suffix}'
        if not path.exists():
            return path
        counter += 1


def _save_loli_image(source: str, index: int) -> Path | None:
    result = _read_loli_image_bytes(source)
    if result is None:
        return None
    data, suffix = result
    root = _loli_image_root()
    root.mkdir(parents=True, exist_ok=True)
    path = _unique_loli_path(root, suffix, index)
    path.write_bytes(data)
    # 落盘后立即失效路径缓存：否则新图片在 TTL 内不可见，用户会误判上传失败
    _invalidate_loli_paths_cache()
    return path


def _loli_image_map() -> dict[str, Path]:
    result: dict[str, Path] = {}
    # 以内容标识为键：删除与去重都按标识寻址，不依赖可能被移动或改名的本机路径
    for path in _loli_image_paths():
        result[_loli_image_hash_id(path)] = path
    return result


# ── 命令处理 ─────────────────────────────────────────────────────────────────

def _loli_record_name(image: str) -> str:
    # 名称内嵌内容标识：同一张图在不同用户、不同日期下命名一致，便于去重与问题排查
    return f'萝莉图{_loli_image_hash_id(image)}'


def _parse_loli_image_urls(payload: GalleryPayload) -> tuple[str, ...]:
    # 校验失败一律抛 RuntimeError 而不静默跳过：接口契约一旦漂移，宁可当天明确报错，也不把
    # 半成品记录写入每日上下文，否则错误会在整天内被反复读取
    if 'roles' not in payload or not isinstance(payload['roles'], list):
        raise RuntimeError('萝莉图库接口缺少 roles 列表。')

    loli_role_found = False
    image_urls: list[str] = []
    seen_urls: set[str] = set()
    for role_data in payload['roles']:
        if not isinstance(role_data, dict):
            raise RuntimeError('萝莉图库接口的 roles 项必须是对象。')
        if 'role_ids' not in role_data or not isinstance(role_data['role_ids'], list):
            raise RuntimeError('萝莉图库接口的 role_ids 必须是列表。')

        role_ids = tuple(str(role_id).strip() for role_id in role_data['role_ids'])
        if 'loli' not in role_ids:
            continue
        loli_role_found = True

        if 'images' not in role_data or not isinstance(role_data['images'], list):
            raise RuntimeError('萝莉图库接口的 images 必须是列表。')
        for image_data in role_data['images']:
            if not isinstance(image_data, dict):
                raise RuntimeError('萝莉图库接口的 images 项必须是对象。')
            if 'url' not in image_data or not isinstance(image_data['url'], str):
                raise RuntimeError('萝莉图库接口的图片缺少 url 字符串。')
            image_url = image_data['url'].strip()
            # 仅接受绝对 HTTP(S) 地址：相对路径与其它协议无法被发送器下载，会在发送阶段才暴露
            if not image_url.startswith(('http://', 'https://')):
                raise RuntimeError('萝莉图库接口返回了无效的图片 URL。')
            # 去重保留首次出现顺序：重复项若原样保留，会让同一张图按出现次数获得更高抽取权重
            if image_url not in seen_urls:
                seen_urls.add(image_url)
                image_urls.append(image_url)

    # 必须存在 role_ids 含 loli 的条目：否则会把整库图片（可能含非萝莉内容）当作萝莉候选
    if not loli_role_found:
        raise RuntimeError('萝莉图库接口缺少 role_ids=["loli"] 的角色项。')
    if not image_urls:
        raise RuntimeError('萝莉图库接口没有可用图片。')
    return tuple(image_urls)


def _fetch_loli_image_urls_sync(api_url: str) -> tuple[str, ...]:
    try:
        body = _http_get_with_retry(api_url, timeout=GALLERY_HTTP_TIMEOUT_SECONDS)
    except HTTPError as exc:
        # 401/403/429 分别对应令牌失效、IP 封禁与限流，只有统一映射能给出可操作的处置建议；
        # 其余状态码没有额外语义，直接带上状态码回报即可
        if exc.code in {401, 403, 429}:
            raise RuntimeError(_auth_error_reason(exc, what='请求萝莉图库接口')) from exc
        raise RuntimeError(f'请求萝莉图库接口失败，HTTP {exc.code}。') from exc
    except URLError as exc:
        raise RuntimeError(f'请求萝莉图库接口失败：{exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('请求萝莉图库接口超时。') from exc
    except OSError as exc:
        raise RuntimeError(f'请求萝莉图库接口失败：{exc}') from exc

    # 解析与网络错误统一收敛为 RuntimeError：调用方据此区分「远程失败」与「本地无图」，
    # 只有两者都不可用时才把远程原因上报给用户
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('萝莉图库接口返回内容不是有效 JSON。') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('萝莉图库接口返回格式不正确。')
    return _parse_loli_image_urls(payload)


def _is_legacy_remote_loli_record(record: WifeRecord) -> bool:
    # 旧版把随机接口地址写入 role_ids=('接口',)，其 image 是接口而非具体图片，每次读取都会
    # 跳到新图，与「当天固定」相冲突，必须识别出来并迁移为固定 URL
    return record.record_type == 'loli' and record.role_ids == ('接口',)


def _loli_unavailable_text(record_data: RoleRecordValue) -> str | None:
    state = _wife_state(record_data)
    if state == 'lost_stolen':
        # 昵称对用户可读、ID 是保底信息，因此优先取昵称；两者皆缺时保持空串，不抛异常
        robber = record_data['stolen_by'] if 'stolen_by' in record_data else ''
        if 'stolen_by_name' in record_data and record_data['stolen_by_name']:
            robber = record_data['stolen_by_name']
        return f'你的萝莉已经被{robber}抢走了，今天就先忍忍吧~'
    if state == 'lost_gifted':
        receiver = record_data['gifted_to'] if 'gifted_to' in record_data else ''
        if 'gifted_to_name' in record_data and record_data['gifted_to_name']:
            receiver = record_data['gifted_to_name']
        return f'你的萝莉已经送给{receiver}了，今天就先忍忍吧~'
    if state == 'divorced':
        return '你今天已经和萝莉离婚了，明天再来吧~'
    return None


def _loli_api_url() -> str:
    base = str(_cfg('DailyWifeApiUrl') or DEFAULT_GALLERY_BASE_URL).strip().rstrip('/')
    # 兼容用户按旧文档把统一地址直接填到 /loli 或 /nor18 的情况；其余地址一律补 /loli，
    # 避免拼接出 /loli/loli 这类无效路径
    if base.endswith('/loli') or base.endswith('/nor18'):
        return base
    return _apply_no_r18(f'{base}/loli')


async def _roll_loli_record(
    ev: Event,
    user_key: str,
) -> tuple[WifeRecord | None, str | None]:
    # local 模式不构造接口地址，因此不会发起任何远程请求——这既是「纯本地」语义的硬保证，
    # 也是内网与离线部署可用的前提
    custom_url = _loli_api_url() if _image_source('loli') == 'gallery' else ''
    remote_error: str | None = None
    if custom_url:
        logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日萝莉列表，接口: {custom_url}')
        try:
            image_urls = await _LOLI_SOURCE_CACHE.get(
                custom_url,
                lambda: run_blocking(_fetch_loli_image_urls_sync, custom_url),
            )
        except RuntimeError as exc:
            # 图库不可用不应让当天抽签整体失败：记录原因后继续走本地候选
            remote_error = str(exc)
            logger.warning(f'{LOG_PREFIX} 远程萝莉接口失败，回退本地图片: {exc}')
        else:
            # 以「日期 + 用户 + 群」为种子的确定性随机：当天重复调用必然取到同一张图
            image_url = _daily_rng(ev, user_key, 'loli').choice(image_urls)
            return (
                WifeRecord(
                    name=_loli_record_name(image_url),
                    role_ids=('loli',),
                    image=image_url,
                    record_type='loli',
                ),
                None,
            )

    # 仅当本地同样无图时才把远程错误作为原因上报：本地可用却提示远程故障会把排查方向带偏
    images = await run_blocking(_loli_image_paths)
    if not images:
        return None, remote_error or '暂无图片'
    image = _daily_rng(ev, user_key, 'loli').choice(images)
    logger.debug(f'{LOG_PREFIX} 用户 {ev.user_id} 请求今日萝莉，选中本地图片: {image}')
    return (
        WifeRecord(
            name=_loli_record_name(str(image)),
            role_ids=(_loli_image_hash_id(image),),
            image=str(image),
            record_type='loli',
        ),
        None,
    )


async def _send_loli_record(
    bot: Bot,
    ev: Event,
    record: WifeRecord,
    text: str = '你今天的萝莉来啦！',
) -> None:
    await _send_loli_result_image(
        bot,
        record.image,
        text,
        ev.user_id,
        ev.group_id is not None,
    )


async def _send_loli_image(bot: Bot, ev: Event) -> None:
    context = await _load_daily_context(ev)
    user_key = _user_key(ev)
    current = context['lolis'][user_key] if user_key in context['lolis'] else None
    if isinstance(current, dict):
        unavailable_text = _loli_unavailable_text(current)
        if unavailable_text is not None:
            return await _send_loli_text(bot, unavailable_text)
        record = _record_from_dict(current)
        if record is not None and not _is_legacy_remote_loli_record(record):
            return await _send_loli_record(bot, ev, record)
        if record is not None:
            logger.warning(
                f'{LOG_PREFIX} 用户 {user_key} 的旧版萝莉记录只保存了随机接口地址，'
                '将迁移为当天固定图片 URL'
            )

    record, error = await _roll_loli_record(ev, user_key)
    if record is None:
        return await _send_loli_text(bot, error or '暂无图片')

    # 抽签包含远程请求，期间其它协程可能已为同一用户写入记录。锁内重新加载并复用既有记录，
    # 「同一用户当天只有一张」在并发下才依然成立。
    response_text: str | None = None
    selected_record = record
    async with _daily_context_lock(ev):
        save_context = await _load_daily_context(ev)
        existing = (
            save_context['lolis'][user_key]
            if user_key in save_context['lolis']
            else None
        )
        if isinstance(existing, dict):
            unavailable_text = _loli_unavailable_text(existing)
            if unavailable_text is not None:
                response_text = unavailable_text
            else:
                existing_record = _record_from_dict(existing)
                if (
                    existing_record is not None
                    and not _is_legacy_remote_loli_record(existing_record)
                ):
                    selected_record = existing_record
                else:
                    if existing_record is not None:
                        # 只替换记录本体字段：来源状态（stolen_from/gifted_from）由抢与赠送流程
                        # 写入，整体覆盖会丢失「二手」标记，使已易手的萝莉重新变得可抢
                        replacement = _record_to_dict(record, ev, user_key)
                        updated_existing = dict(existing)
                        for key in ('name', 'role_ids', 'image', 'record_type', 'updated_at'):
                            updated_existing[key] = replacement[key]
                        await _save_daily_records(ev, [('lolis', user_key, updated_existing)])
                    else:
                        await _save_daily_records(
                            ev,
                            [('lolis', user_key, _record_to_dict(record, ev, user_key))],
                        )
        else:
            # 首次抽取必须落库：离婚、被抢、赠送等交互都以每日记录为唯一入口，缺少记录时它们
            # 无法判定状态，只能回复「今天还没有萝莉」
            await _save_daily_records(
                ev,
                [('lolis', user_key, _record_to_dict(record, ev, user_key))],
            )

    if response_text is not None:
        return await _send_loli_text(bot, response_text)
    await _send_loli_record(bot, ev, selected_record)


async def _send_upload_loli(bot: Bot, ev: Event) -> None:
    if not _can_upload_images(ev):
        return await _send_loli_text(bot, '你不在图片上传白名单中。')

    refs = _loli_upload_refs(ev)
    if not refs:
        return await _send_loli_text(bot, '请同时发送图片和命令，例如：上传萝莉图片 [图片]')

    saved: list[Path] = []
    failed = 0
    # 单张失败不中断整批：附件中可能混有非图片内容或超出体积上限的图片，已成功的部分仍应
    # 落盘并回报，失败数量单独统计后附在结果里
    for i, ref in enumerate(refs, 1):
        path = await run_blocking(_save_loli_image, ref, i)
        if path is None:
            failed += 1
        else:
            saved.append(path)

    if not saved:
        return await _send_loli_text(bot, '上传失败，请确认消息里附带的是图片。')

    ids = [_loli_image_hash_id(p) for p in saved]
    lines = [f'萝莉图片上传成功，共 {len(saved)} 张', f'图片ID：{", ".join(ids)}']
    if failed:
        lines.append(f'失败：{failed} 张')
    await _send_loli_text(bot, '\n'.join(lines))


async def _send_loli_image_list(bot: Bot, ev: Event) -> None:
    image_map = await run_blocking(_loli_image_map)
    if not image_map:
        return await _send_loli_text(bot, '本地还没有萝莉图片，使用「上传萝莉图片」添加图片。')
    nodes: list[Message | str] = []
    # 经合并转发承载列表以避免逐张刷屏；图片段必须走 _image_message_from_path，它在插件
    # 线程池内完成读取与 base64 编码，避免在事件循环上同步编码大图而阻塞其它消息
    for hash_id, path in image_map.items():
        nodes.append(f'萝莉图片ID：{hash_id}')
        nodes.append(await _image_message_from_path(path))
    await _safe_send(bot, MessageSegment.node(nodes))


async def _send_delete_loli(bot: Bot, ev: Event) -> None:
    hash_id = str(ev.text or '').strip().lower()
    if not hash_id:
        # 无参数即清空整个目录，该操作不可撤销，因此只在明确未提供标识时进入
        logger.info(f'{LOG_PREFIX} 用户 {ev.user_id} 触发删除全部萝莉图片命令')
        count = await run_blocking(_delete_loli_images)
        return await _send_loli_text(bot, f'已删除全部萝莉图片，共 {count} 张。')
    # 把输入收窄为 8 位十六进制内容标识：任何路径片段都无法通过校验，从源头排除目录穿越
    # 与误删非图库文件
    if not re.fullmatch(r'[0-9a-f]{8}', hash_id):
        return await _send_loli_text(bot, '请提供 8 位图片ID，例如：删除萝莉图片 abcd1234\n不加ID则删除全部')
    image_map = await run_blocking(_loli_image_map)
    path = image_map.get(hash_id)
    if path is None:
        return await _send_loli_text(bot, f'未找到图片ID：{hash_id}')
    try:
        await run_blocking(path.unlink)
    except OSError as exc:
        # 文件可能已被外部清理或权限不足：回报删除失败即可，不必让命令整体异常
        logger.warning(f'{LOG_PREFIX} 删除萝莉图片失败: {path} -> {exc}')
        return await _send_loli_text(bot, f'删除失败：{hash_id}')
    _invalidate_loli_paths_cache()
    _LOLI_SOURCE_CACHE.invalidate()
    await _send_loli_text(bot, f'已删除萝莉图片：{hash_id}')


# ── 触发器注册 ────────────────────────────────────────────────────────────────
# block=True：命令命中后不再交给后续处理器，避免同一条消息被多个功能重复消费。
# to_ai 文本是 AI 工具的对外描述（调用时机与参数语义），与命令文案同属兼容性契约。


@loli_sv.on_fullmatch(
    '今日萝莉',
    block=True,
    to_ai="""随机抽取当前用户今天的萝莉图片。
    当用户说“今日萝莉”“抽一张萝莉”“我今天的萝莉是谁”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['萝莉图片每日随机抽取（图库或本地图库）'],
    aliases=['今日老婆·抽萝莉', '今日老婆·今日萝莉'],
)
async def daily_loli(bot: Bot, ev: Event) -> None:
    # 开关关闭时静默返回：不回复任何内容，避免关闭功能后仍产生噪音
    if not _loli_enabled():
        return
    await _send_loli_image(bot, ev)


@image_upload_sv.on_command(('上传萝莉图片', '今日萝莉上传', '萝莉上传图片'), block=True)
async def upload_loli(bot: Bot, ev: Event) -> None:
    await _send_upload_loli(bot, ev)


@loli_manage_sv.on_fullmatch(
    ('查看萝莉图片', '今日萝莉列表', '萝莉图片列表'),
    block=True,
    to_ai="""查看今日萝莉图库列表。
    当用户说“查看萝莉图片”“萝莉图片列表”“有哪些萝莉图”时调用。
    Args:
        text: 无需参数，留空。
    """,
    covers=['本地萝莉图库的图片 ID 列表'],
    aliases=['今日老婆·萝莉图片列表', '今日老婆·萝莉图库'],
)
async def list_loli(bot: Bot, ev: Event) -> None:
    await _send_loli_image_list(bot, ev)


@loli_manage_sv.on_command('删除萝莉图片', block=True)
async def delete_loli(bot: Bot, ev: Event) -> None:
    await _send_delete_loli(bot, ev)
