"""TodayWaifu 的结果图片发送。"""
from __future__ import annotations

from pathlib import Path

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Message
from gsuid_core.segment import MessageSegment
from gsuid_core.ai_core.trigger_bridge import ai_return

from .roles import _load_local_candidates
from .domain import RoleCandidate
from .gallery import _download_image
from .delivery import _safe_send, _send_loli_text
from .executor import run_blocking
from .constants import LOG_PREFIX, _cfg, _daily_item_title
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


async def _send_role_image(
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
            image: bytes = await _download_image(image_url)
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

    # 数据已就绪、图片尚未发送：此处注入 AI 可读摘要
    _ai_return_draw(kind, role.name, text)

    messages: list[Message | str] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    if text:
        messages.append(text)
    messages.append(MessageSegment.image(image))
    await _safe_send(bot, messages if len(messages) > 1 else messages[0])


async def _send_daily_result_image(
    bot: Bot,
    role: RoleCandidate,
    image: str,
    text: str,
    user_id: str,
    is_group: bool,
    kind: str,
) -> None:
    if kind == 'shota':
        await _send_shota_result_image(bot, image, text, user_id, is_group, kind)
        return
    if kind != 'loli':
        await _send_role_image(bot, role, image, text, user_id, is_group, kind)
        return

    await _send_loli_result_image(bot, image, text, user_id, is_group, kind)


async def _send_loli_result_image(
    bot: Bot,
    image: str | bytes,
    text: str,
    user_id: str | int | None,
    is_group: bool,
    kind: str = 'loli',
) -> None:
    # 数据已就绪、图片尚未发送：此处注入 AI 可读摘要（loli 与 shota 共用本函数）
    _ai_return_draw(kind, '', text)

    messages: list[Message | str] = []
    if is_group and user_id is not None and bool(_cfg('DailyWifeAtUser')):
        messages.append(MessageSegment.at(user_id))
        messages.append('\n')
    messages.append(text)
    if isinstance(image, str):
        if image.startswith(('http://', 'https://')):
            try:
                image_ref = await _download_image(image)
            except RuntimeError as exc:
                logger.warning(f'{LOG_PREFIX} 下载萝莉图片失败: {exc}')
                await _send_loli_text(bot, str(exc))
                return
        else:
            # 本地图片走 mtime 字节缓存，避免重复读盘
            image_ref = await run_blocking(read_file_bytes_cached, Path(image))
    else:
        image_ref = image
    messages.append(MessageSegment.image(image_ref))
    await _safe_send(bot, messages)


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
            messages.append(MessageSegment.image(image_bytes))

    if not messages:
        await _safe_send(bot, missing_hint)
        return
    await _safe_send(bot, messages if len(messages) > 1 else messages[0])
