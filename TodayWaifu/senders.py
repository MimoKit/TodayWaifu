"""TodayWaifu 的结果图片发送。"""
from __future__ import annotations

import asyncio
from base64 import b64encode
from pathlib import Path
from dataclasses import dataclass

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Message
from gsuid_core.segment import IS_UPLOAD, MessageSegment
from gsuid_core.ai_core.trigger_bridge import ai_return

from .qqbot import (
    QQBotImageError,
    upload_image,
    qqbot_enabled,
    build_image_markdown,
    qqbot_image_host_ready,
    build_marry_member_keyboard,
)
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


def _ai_return_draw(kind: str, name: str, text: str | None) -> None:
    """把本次抽取/流转结果作为 AI 可读摘要注入。

    用户直接触发时 `ai_return` 是空操作；AI 调用时这段文字会成为工具返回值，
    让 AI 知道"抽到了谁"。按 skill §17.3，观测性代码允许 try/except：提取失败
    绝不能影响图片生成与发送。
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

def _is_valid_image_ref(image: str) -> bool:
    if not image:
        return False
    # 图库模式下 image 是 http(s) URL，不是本地文件，发送时再下载校验
    if image.startswith(('http://', 'https://')):
        return True
    try:
        return Path(image).is_file()
    except (OSError, ValueError):
        return False


async def _find_local_role_image(role: RoleCandidate, kind: str) -> str | None:
    """图库图片下载失败时，尝试从本地图片目录为该角色找一张图。"""
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
    """把图片字节编码成 `base64://` 引用（在线程池里调用）。"""
    return f'base64://{b64encode(data).decode()}'


async def _image_message(data: bytes) -> Message:
    """把图片字节转成消息段，base64 编码在插件线程池里完成。

    框架的 `MessageSegment.image(bytes)` 会**在事件循环上**同步执行
    `b64encode(...).decode()`：实测 2MB 图约 8.8ms、10MB 图约 47.7ms，
    25 个命令并发时就是几百毫秒的串行阻塞，整个 Core 一起卡。
    预先编码成 `base64://` 再传入，框架（`IS_UPLOAD` 为假时）会原样透传，
    事件循环上不再有任何编码开销。

    `EnablePicSrv` 打开时框架需要原始字节做图床上传，这时只能把字节交给框架。
    """
    if IS_UPLOAD:
        return MessageSegment.image(data)
    return MessageSegment.image(await run_blocking(_encode_base64_ref, data))


async def _image_message_from_path(path: Path) -> Message:
    """从本地文件构造图片消息段；读盘与编码都在线程池里完成。"""
    return await _image_message(await run_blocking(read_file_bytes_cached, path))


async def _acquire_gallery_image(image_url: str) -> bytes:
    """获取图库图片字节，超时即放弃等待。

    超时只放弃「等待」，**不取消底层下载任务**：`asyncio.shield` 让
    `_download_image` 的内部任务继续在插件线程池里跑完并写入磁盘缓存，
    下一个请求直接命中。若直接用 `wait_for` 包住，取消会顺着 `await task`
    传递下去把下载也掐断，缓存永远暖不起来。

    这样命令协程最多占用 Core 的命令并发额度 `IMAGE_ACQUIRE_TIMEOUT_SECONDS` 秒，
    而不是被重试链拖到几十秒 —— 后者会让 bot 的 `_process` 停止消费队列，
    导致**整个 Core 所有命令**一起卡住。
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
        # 本地图片按 (路径, mtime) 缓存字节，避免高峰期核心反复读盘转 base64
        image = await run_blocking(read_file_bytes_cached, Path(image_url))

    if await _try_qqbot_markdown_image(bot, image, text, user_id, is_group):
        return

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


async def _try_qqbot_markdown_image(
    bot: Bot,
    image: bytes,
    text: str | None,
    user_id: str | int | None,
    is_group: bool,
    keyboard: list[list[object]] | None = None,
) -> bool:
    """尝试走 QQ 官方机器人链路（上传图床 + Markdown）。

    返回 True 表示已发送；False 表示调用方应回退到普通图片发送。

    是否启用**只看配置开关**，不做平台自动检测：开关关闭时这里直接返回 False，
    发送路径与普通平台逐字节一致。开关打开但图床未配置/上传失败时同样回退，
    只打 warning，不让一次上传失败吞掉整条命令的结果。
    """
    if not qqbot_enabled():
        return False
    if not qqbot_image_host_ready():
        logger.warning(f'{LOG_PREFIX} 已开启 QQBot 模式但图床未配置完整，回退普通发送')
        return False
    try:
        image_url, size = await upload_image(image)
        markdown = build_image_markdown(image_url, size, text, user_id, is_group)
        message = MessageSegment.markdown(markdown, buttons=keyboard)
        await _safe_send(bot, message)
    except QQBotImageError as exc:
        logger.warning(f'{LOG_PREFIX} QQBot Markdown 发送失败，回退普通图片: {exc}')
        return False
    return True


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
            # 本地图片走 mtime 字节缓存，避免重复读盘
            image_ref = await run_blocking(read_file_bytes_cached, Path(image))
    else:
        image_ref = image
    if await _try_qqbot_markdown_image(bot, image_ref, text, user_id, is_group):
        return
    messages.append(await _image_message(image_ref))
    await _safe_send(bot, messages)


_deliver_shota_result_image = _deliver_loli_result_image


# ── 图片投递队列 ──────────────────────────────────────────────────────────────
# 框架 `bot.py` 的 `_process` 是「先拿命令并发额度、再跑协程」，额度在协程结束
# 时才归还。所以只要命令协程还在等图库下载，它就一直占着 Core 的
# `CommandSemaphore` 名额；25 个名额被占满后 `_process` 直接停止消费队列，
# 该 bot 上**所有插件**的命令一起卡住。
#
# 因此把「下载 + 编码 + 发送」整段搬到插件自己的有界队列里，命令协程只做入队
# 就返回（微秒级），Core 的命令额度立刻归还。用户拿到的图片晚一点点到，
# 但整个 Core 不会被一个插件的网络等待拖死。
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
    """启动投递 worker（幂等）。维护循环也会调用，用于拉起意外退出的 worker。"""
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
    """丢掉已结束的 worker 并补足数量，避免一次意外让投递能力永久下降。"""
    _IMAGE_DELIVERY_TASKS[:] = [task for task in _IMAGE_DELIVERY_TASKS if not task.done()]
    start_image_delivery_workers()


def image_delivery_backlog() -> int:
    """队列积压量（可观测性用）。"""
    return _IMAGE_DELIVERY_QUEUE.qsize()


async def _enqueue_image_job(job: _ImageJob) -> bool:
    """入队并立即返回；队列满时退回「只发文字」，绝不阻塞命令协程。"""
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
    """投递一次角色图发送；入队后立即返回，真正的下载与发送由后台 worker 完成。"""
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
    """投递一次萝莉/正太图发送（loli 与 shota 共用）。"""
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


# 正太与萝莉共用同一套投递逻辑
_send_shota_result_image = _send_loli_result_image


async def _send_local_image(
    bot: Bot,
    image_url: str,
    missing_hint: str,
    text: str | None = None,
    user_id: str | int | None = None,
    is_group: bool = True,
    kind: str = 'wife',
    with_keyboard: bool = False,
) -> None:
    keyboard = build_marry_member_keyboard() if with_keyboard else None
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
            if await _try_qqbot_markdown_image(
                bot, image_bytes, text, user_id, is_group, keyboard
            ):
                return
            messages.append(await _image_message(image_bytes))

    if not messages:
        await _safe_send(bot, missing_hint)
        return
    await _safe_send(bot, messages if len(messages) > 1 else messages[0])
