"""TodayWaifu 的结果图片发送。"""
from __future__ import annotations

import time
import asyncio
from base64 import b64encode
from pathlib import Path
from dataclasses import dataclass

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Message
from gsuid_core.segment import IS_UPLOAD, MessageSegment
from gsuid_core.ai_core.trigger_bridge import ai_return

from .paths import _gallery_image_cache_root
from .roles import _load_local_candidates
from .domain import RoleCandidate
from .gallery import _download_image
from .delivery import _safe_send, _send_loli_text
from .executor import run_blocking
from .constants import (
    LOG_PREFIX,
    IMAGE_ACQUIRE_TIMEOUT_SECONDS,
    _cfg,
    _daily_item_title,
)
from .file_cache import read_file_bytes_cached
from .image_shrink import shrink_image_cached


def _ai_return_draw(kind: str, name: str, text: str | None) -> None:
    """将本次抽取结果注入为 AI 可读摘要。

    该摘要是 AI 工具调用唯一的语义出口：图片段只携带资源标识，无法回答"抽到了谁"，
    因此角色名与文案必须在返回前以文本形式一并回传。用户直接触发时 `ai_return` 为空
    操作，该调用的代价可忽略。

    摘要按可用字段降级拼接，任一字段缺失都不构成错误。异常在此处吞掉而不外抛：观测性
    写入失败属于旁路故障，其后果（AI 答复缺少文字摘要）远轻于因此中断图片生成与投递。
    按 skill §17.3，观测性代码允许 try/except。
    """
    try:
        title = _daily_item_title(kind)
        summary = (text or '').strip()
        if name and summary:
            ai_return(f'【今日{title}】{name}\n{summary}')
        elif name:
            ai_return(f'【今日{title}】{name}')
        elif summary:
            ai_return(f'【今日{title}】{summary}')
        else:
            ai_return(f'【今日{title}】')
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} ai_return 数据提取失败: {exc}')

_VALID_IMAGE_REF_TTL_SECONDS = 60.0
_VALID_IMAGE_REF_CACHE: dict[str, float] = {}


def _is_valid_image_ref(image: str) -> bool:
    if not image:
        return False
    # 图库模式下列取自远程 URL，本地无从判断其可用性，故一律先判为有效，
    # 由发送阶段的实际下载负责校验并触发回退
    if image.startswith(('http://', 'https://')):
        return True
    # 该判断位于事件循环的同步路径上，且列表类命令会按记录数逐条调用（单次可达全群规模），
    # 因此以短 TTL 缓存规避重复 stat。缓存只记录「存在」这一结论：路径被删除后至多延迟
    # TTL 才被发现，而发送前还会再次校验，故不会据此发出失效路径；反向的否定结论不缓存，
    # 使新增文件立即可见，不必等待 TTL 过期。容量达上限时整体清空而非逐条淘汰，因该缓存
    # 仅用于省去系统调用，未命中只会退化为一次探测，丢失全部条目的代价可忽略。
    now = time.monotonic()
    checked_at = _VALID_IMAGE_REF_CACHE.get(image)
    if checked_at is not None and now - checked_at < _VALID_IMAGE_REF_TTL_SECONDS:
        return True
    try:
        exists = Path(image).is_file()
    except (OSError, ValueError):
        return False
    if exists:
        if len(_VALID_IMAGE_REF_CACHE) >= 4096:
            _VALID_IMAGE_REF_CACHE.clear()
        _VALID_IMAGE_REF_CACHE[image] = now
    else:
        _VALID_IMAGE_REF_CACHE.pop(image, None)
    return exists


async def _find_local_role_image(role: RoleCandidate, kind: str) -> str | None:
    """图库图片不可用时，从本地图片目录为该角色挑选一张替代图。

    匹配以名称相同或角色 ID 相交为准：同一角色在图库与本地目录中的名称写法未必一致
    （别名、异体字等），仅凭名称会漏匹配；ID 相交则能在名称缺失或改写时仍定位到同一
    角色。两级判据取并集，宁可匹配到名称相近的角色，也不返回空而使整条发送失败。
    """
    try:
        candidates, error = await run_blocking(_load_local_candidates, kind)
    except (OSError, ValueError) as exc:
        logger.warning(f'{LOG_PREFIX} 回退本地图片失败: {exc}')
        return None
    if error or not candidates:
        logger.warning(f'{LOG_PREFIX} 回退本地图片失败: {error}')
        return None
    role_ids = set(role.role_ids)
    for candidate in candidates:
        if candidate.name == role.name or (role_ids & set(candidate.role_ids)):
            if candidate.images:
                return candidate.images[0]
    return None


class _ImageAcquireTimeout(RuntimeError):
    """图库图片获取超时；继承 RuntimeError 以复用既有的回退分支。"""


def _encode_base64_ref(data: bytes) -> str:
    """将图片字节编码为 `base64://` 引用；仅供插件线程池调用。

    编码为纯 CPU 操作，耗时随图片体积线性增长，必须与事件循环隔离。调用方有义务通过
    线程池投递本函数，不得在协程内直接调用。
    """
    return f'base64://{b64encode(data).decode()}'


def _shrink_limit_bytes() -> int:
    return int(_cfg('DailyWifeImageMaxSizeMB') or 0) * 1024 * 1024


def _shrink_image_sync(image: bytes | bytearray) -> bytes:
    """超过配置阈值的图片转压为 WebP（不可用时退化为 JPEG），结果按原图内容落盘缓存。

    图片压缩是零点高峰最重的 CPU 开销，单张大图可达秒级；以原图内容为缓存键，可使同一
    张图片在多次发送之间只压缩一次，后续直接读缓存。阈值取 0 表示关闭压缩，此时原样
    返回，避免用户关闭该功能后仍承担一次无收益的哈希与查盘开销。
    细节见 `image_shrink` 模块。
    """
    raw = bytes(image)
    limit = _shrink_limit_bytes()
    if limit <= 0 or len(raw) <= limit:
        return raw
    data = shrink_image_cached(raw, limit, _gallery_image_cache_root())
    if data is not raw:
        logger.debug(
            f'{LOG_PREFIX} 图片 {len(raw) / 1048576:.1f}MB 超过阈值，'
            f'已压缩至 {len(data) / 1048576:.2f}MB 后发送'
        )
    return data


async def _image_message(data: bytes) -> Message:
    """构造图片消息段，base64 编码下沉至插件线程池执行。

    框架的 `MessageSegment.image(bytes)` 会在事件循环上同步执行
    `b64encode(...).decode()`：实测 2MB 图约 8.8ms、10MB 图约 47.7ms。该开销不会并行
    摊薄，而是在事件循环上串行累加，25 个命令并发即为数百毫秒的全局停顿，波及 Core 内
    所有插件。预先编码为 `base64://` 后传入，框架（`IS_UPLOAD` 为假时）原样透传，
    事件循环不再承担任何编码开销。

    `EnablePicSrv` 打开时框架需取得原始字节以完成图床上传，预先编码会破坏该链路，
    因此该分支必须保留原始字节传递。
    """
    if IS_UPLOAD:
        return MessageSegment.image(data)
    return MessageSegment.image(await run_blocking(_encode_base64_ref, data))


async def _image_message_from_path(path: Path) -> Message:
    """由本地文件构造图片消息段；读盘与编码均在插件线程池内完成。

    列图类命令会连续构造大量此类消息段，任一环节留在事件循环上，都会按图片数量累积成
    可感知的卡顿，故此处不接受任何在循环内完成的简化实现。
    """
    return await _image_message(await run_blocking(read_file_bytes_cached, path))


async def _acquire_gallery_image(image_url: str) -> bytes:
    """获取图库图片字节，超时仅放弃等待。

    超时不得取消底层下载任务：`asyncio.shield` 使 `_download_image` 的内部任务脱离
    等待方独立运行，继续在插件线程池中完成下载并写入磁盘缓存，后续请求可直接命中。
    若改用 `wait_for` 直接包裹，取消会沿 `await task` 向下传播并中断下载，既令已付出的
    网络开销失效，也使缓存始终无法预热。

    该上限约束的是命令协程占用 Core 命令并发额度的时间：命令协程返回前额度不予归还，
    而重试链的最坏耗时可达数十秒；一旦额度被占满，bot 的 `_process` 将停止消费队列，
    Core 内所有插件的命令会一并停滞。
    """
    try:
        return await asyncio.wait_for(
            asyncio.shield(_download_image(image_url)),
            timeout=IMAGE_ACQUIRE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise _ImageAcquireTimeout(
            f'图库响应超时（超过 {IMAGE_ACQUIRE_TIMEOUT_SECONDS:.0f} 秒），请稍后再试。'
        ) from exc


async def _deliver_role_image(
    bot: Bot,
    role: RoleCandidate,
    image_url: str,
    text: str | None = None,
    user_id: str | int | None = None,
    is_group: bool = True,
    kind: str = 'wife',
) -> None:
    is_gallery_image = image_url.startswith(('http://', 'https://'))
    if is_gallery_image:
        try:
            image: bytes = await _acquire_gallery_image(image_url)
        except RuntimeError as exc:
            logger.warning(f'{LOG_PREFIX} 下载图库图片失败: {exc}')
            local_image = await _find_local_role_image(role, kind)
            if local_image is not None:
                logger.warning(f'{LOG_PREFIX} 已回退本地图片: {local_image}')
                image = await run_blocking(read_file_bytes_cached, Path(local_image))
            else:
                await _safe_send(bot, str(exc))
                return
    else:
        if not Path(image_url).is_file():
            logger.warning(f'{LOG_PREFIX} 本地图片不存在: {image_url}')
            await _safe_send(bot, '本地图片文件不存在，请检查 custom_role_pile 目录。')
            return
        # 本地图片以 (路径, mtime_ns, 大小) 为键缓存字节：零点高峰同一路径会被反复读取，
        # 逐次读盘与编码开销显著，缓存命中即可省去
        image = await run_blocking(read_file_bytes_cached, Path(image_url))

    image = await run_blocking(_shrink_image_sync, image)
    messages: list[Message | str] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    messages.append(await _image_message(image))
    await _safe_send(bot, messages if len(messages) > 1 else messages[0])


async def _deliver_daily_result_image(
    bot: Bot,
    role: RoleCandidate,
    image: str,
    text: str,
    user_id: str,
    is_group: bool,
    kind: str,
) -> None:
    if kind == 'shota':
        await _deliver_shota_result_image(bot, image, text, user_id, is_group, kind)
        return
    if kind != 'loli':
        await _deliver_role_image(bot, role, image, text, user_id, is_group, kind)
        return

    await _deliver_loli_result_image(bot, image, text, user_id, is_group, kind)


async def _deliver_loli_result_image(
    bot: Bot,
    image: str | bytes,
    text: str,
    user_id: str | int | None,
    is_group: bool,
    kind: str = 'loli',
) -> None:
    messages: list[Message | str] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    messages.append(text)
    if isinstance(image, str):
        if image.startswith(('http://', 'https://')):
            try:
                image_ref = await _acquire_gallery_image(image)
            except RuntimeError as exc:
                logger.warning(f'{LOG_PREFIX} 下载萝莉图片失败: {exc}')
                await _send_loli_text(bot, str(exc))
                return
        else:
            # 本地图片同样经字节缓存读取，避免同一文件在高峰期内重复读盘
            image_ref = await run_blocking(read_file_bytes_cached, Path(image))
    else:
        image_ref = image
    if isinstance(image_ref, (bytes, bytearray)):
        image_ref = await run_blocking(_shrink_image_sync, image_ref)
    messages.append(await _image_message(image_ref))
    await _safe_send(bot, messages)


_deliver_shota_result_image = _deliver_loli_result_image


# ── 图片投递队列 ──────────────────────────────────────────────────────────────
# 框架 `bot.py` 的 `_process` 先取得命令并发额度、再执行协程，额度要到协程返回时才归还。
# 命令协程只要仍在等待图库下载，就始终占用 Core 的 `CommandSemaphore` 名额；名额耗尽后
# `_process` 停止消费队列，该 bot 上所有插件的命令一并停滞。
#
# 因此将「下载 + 编码 + 发送」整段迁入插件自有的有界队列，命令协程仅完成入队即返回
# （微秒级），Core 的命令额度随即归还。代价是图片到达时刻略有延后，收益是单个插件的
# 网络等待不再波及整个 Core 的命令处理能力。
#
# 队列有界且写入非阻塞：积压代表下游投递能力已达上限，此时须立即降级而非排队等待，
# 否则等待会重新占用命令额度，本次改造即失去意义。
IMAGE_DELIVERY_QUEUE_MAX = 512


IMAGE_DELIVERY_WORKERS = 8


@dataclass(frozen=True)
class _ImageJob:
    bot: Bot
    role: RoleCandidate
    image: str | bytes
    text: str | None
    user_id: str | int | None
    is_group: bool
    kind: str
    loli_style: bool


_IMAGE_DELIVERY_QUEUE: asyncio.Queue[_ImageJob] = asyncio.Queue(maxsize=IMAGE_DELIVERY_QUEUE_MAX)


_IMAGE_DELIVERY_TASKS: list[asyncio.Task[None]] = []


async def _image_delivery_worker() -> None:
    while True:
        job = await _IMAGE_DELIVERY_QUEUE.get()
        try:
            if job.loli_style:
                await _deliver_loli_result_image(
                    job.bot, job.image, job.text or '', job.user_id, job.is_group, job.kind
                )
            else:
                await _deliver_role_image(
                    job.bot, job.role, str(job.image), job.text, job.user_id, job.is_group, job.kind
                )
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError, TimeoutError, ValueError, TypeError) as exc:
            logger.warning(f'{LOG_PREFIX} 图片投递失败({job.kind}): {exc}')
        finally:
            _IMAGE_DELIVERY_QUEUE.task_done()


def start_image_delivery_workers() -> None:
    """将投递 worker 补齐至目标数量；可重复调用。

    维护循环与入队路径都会调用本函数，因此必须幂等：仅在数量不足时创建新任务，已存在
    或正在退出的任务不受影响。
    """
    while len(_IMAGE_DELIVERY_TASKS) < IMAGE_DELIVERY_WORKERS:
        _IMAGE_DELIVERY_TASKS.append(asyncio.create_task(_image_delivery_worker()))


async def stop_image_delivery_workers() -> None:
    tasks = list(_IMAGE_DELIVERY_TASKS)
    _IMAGE_DELIVERY_TASKS.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _prune_image_delivery_workers() -> None:
    """移除已结束的 worker 任务并补足数量。

    worker 因未捕获异常退出时，其任务句柄仍留在表中，若只按数量判断便会误认为投递能力
    充足，能力将随每次意外单调下降。先剔除已完成任务再补齐，可使该状态自愈。
    """
    _IMAGE_DELIVERY_TASKS[:] = [task for task in _IMAGE_DELIVERY_TASKS if not task.done()]
    start_image_delivery_workers()


def image_delivery_backlog() -> int:
    """返回当前队列积压量，供可观测性使用；读取不产生副作用。"""
    return _IMAGE_DELIVERY_QUEUE.qsize()


async def _enqueue_image_job(job: _ImageJob) -> bool:
    """入队后立即返回；队列满时降级为仅发送文字。

    入队必须采用非阻塞写入：一旦在此等待空位，命令协程就会重新占用 Core 的命令并发
    额度，本次改造的意义随之丧失。队列满意味着下游投递能力已达上限，此时牺牲图片、
    保证文字可达，是可接受的降级路径。
    """
    # 插件重载不会执行 on_core_start_before，队列可能没有消费者而持续积压，
    # 故在每次入队前自愈式补齐 worker
    _prune_image_delivery_workers()
    try:
        _IMAGE_DELIVERY_QUEUE.put_nowait(job)
        return True
    except asyncio.QueueFull:
        logger.warning(f'{LOG_PREFIX} 图片投递队列已满({IMAGE_DELIVERY_QUEUE_MAX})，本次只发送文字')
        if job.loli_style:
            await _send_loli_text(job.bot, job.text or '')
        else:
            await _safe_send(job.bot, job.text or '当前请求过多，请稍后再试。')
        return False


async def _send_role_image(
    bot: Bot,
    role: RoleCandidate,
    image_url: str,
    text: str | None = None,
    user_id: str | int | None = None,
    is_group: bool = True,
    kind: str = 'wife',
) -> None:
    """投递一次角色图发送：仅完成摘要注入与入队。

    AI 摘要必须在此刻生成：入队后请求上下文即被释放，后台 worker 执行时已无法取得本次
    抽取的角色与文案。
    """
    _ai_return_draw(kind, role.name, text)
    await _enqueue_image_job(
        _ImageJob(
            bot=bot,
            role=role,
            image=image_url,
            text=text,
            user_id=user_id,
            is_group=is_group,
            kind=kind,
            loli_style=False,
        )
    )


async def _send_daily_result_image(
    bot: Bot,
    role: RoleCandidate,
    image: str,
    text: str,
    user_id: str,
    is_group: bool,
    kind: str,
) -> None:
    if kind in ('loli', 'shota'):
        await _send_loli_result_image(bot, image, text, user_id, is_group, kind)
        return
    await _send_role_image(bot, role, image, text, user_id, is_group, kind)


async def _send_loli_result_image(
    bot: Bot,
    image: str | bytes,
    text: str,
    user_id: str | int | None,
    is_group: bool,
    kind: str = 'loli',
) -> None:
    """投递一次萝莉／正太图发送；两类角色共用同一实现。

    此处以空 `RoleCandidate` 占位：该路径走 `loli_style` 投递分支，不读取角色信息。
    """
    _ai_return_draw(kind, '', text)
    await _enqueue_image_job(
        _ImageJob(
            bot=bot,
            role=RoleCandidate(name='', role_ids=(), images=()),
            image=image,
            text=text,
            user_id=user_id,
            is_group=is_group,
            kind=kind,
            loli_style=True,
        )
    )


# 正太与萝莉共用同一套投递逻辑：两者仅取图来源不同，投递路径完全一致
_send_shota_result_image = _send_loli_result_image


async def _send_local_image(
    bot: Bot,
    image_url: str,
    missing_hint: str,
    text: str | None = None,
    user_id: str | int | None = None,
    is_group: bool = True,
    kind: str = 'wife',
) -> None:
    messages: list[Message | str] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    if image_url:
        if not Path(image_url).is_file():
            logger.warning(f'{LOG_PREFIX} 本地图片不存在: {image_url}')
            if not text:
                await _safe_send(bot, missing_hint)
                return
        else:
            image_bytes = await run_blocking(read_file_bytes_cached, Path(image_url))
            image_bytes = await run_blocking(_shrink_image_sync, image_bytes)
            messages.append(await _image_message(image_bytes))

    if not messages:
        await _safe_send(bot, missing_hint)
        return
    await _safe_send(bot, messages if len(messages) > 1 else messages[0])
