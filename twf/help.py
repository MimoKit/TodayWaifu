"""TodayWaifu - help module."""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

from PIL import Image
from gsuid_core.help.draw_new_plugin_help import get_new_help

from ..daily_wife_config import DailyWifeShowConfig
from .shared import *  # noqa: F403

_HELP_JSON_PATH = BASE_DIR / 'help.json'
_TEXTURE_DIR = BASE_DIR / 'texture2d'
_BANNER_BG_PATH = BASE_DIR / 'fb93f5370f556a51db172863420aa50e.png'
_BG_PATH = _TEXTURE_DIR / 'bj.jpg'
_ICON_PATH = _TEXTURE_DIR / 'icons'
_HELP_CACHE_MAX_ENTRIES = 4
_HELP_CACHE: OrderedDict[tuple[Any, ...], str] = OrderedDict()
_HELP_INFLIGHT: dict[tuple[Any, ...], asyncio.Task[str]] = {}


def _load_help_data():
    with _HELP_JSON_PATH.open('r', encoding='utf-8') as f:
        return json.load(f)


def _show_config_path(key: str) -> Path | None:
    value = str(DailyWifeShowConfig.get_config(key).data or '').strip().strip('"')
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_file() else None


def _help_column() -> int:
    value = DailyWifeShowConfig.get_config('DailyWifeHelpColumn').data
    try:
        column = int(value)
    except (TypeError, ValueError):
        column = 3
    return max(1, min(10, column))


def _path_signature(path: Path | None) -> tuple[str, int, int] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return str(path), stat.st_mtime_ns, stat.st_size


def _help_cache_key(
    icon_path: Path,
    banner_bg_path: Path,
    help_bg_path: Path,
    column: int,
    pm: int,
) -> tuple[Any, ...]:
    return (
        _path_signature(_HELP_JSON_PATH),
        _path_signature(icon_path),
        _path_signature(banner_bg_path),
        _path_signature(help_bg_path),
        _path_signature(_ICON_PATH),
        column,
        pm,
    )


def _build_help_inputs(
    plugin_icon_path: Path,
    custom_banner_bg_path: Path | None,
    custom_help_bg_path: Path | None,
) -> tuple[Image.Image, dict[str, Any], dict[str, Image.Image | Path]]:
    """在线程中读取 JSON 和 PIL 资源，避免阻塞事件循环。"""
    with Image.open(plugin_icon_path) as source:
        icon = source.convert('RGBA')

    extra: dict[str, Image.Image | Path] = {}
    banner_bg_path = custom_banner_bg_path or _BANNER_BG_PATH
    if banner_bg_path.is_file():
        with Image.open(banner_bg_path) as source:
            banner = source.convert('RGBA')
            if custom_banner_bg_path is None:
                width, height = banner.size
                extra['banner_bg'] = banner.crop((0, 0, width, int(height * 0.40)))
            else:
                extra['banner_bg'] = banner.copy()

    help_bg_path = custom_help_bg_path or _BG_PATH
    if help_bg_path.is_file():
        with Image.open(help_bg_path) as source:
            background = source.convert('RGBA')
            if custom_help_bg_path is None:
                width, height = background.size
                padded = Image.new('RGBA', (width, height + 700), (15, 15, 25, 255))
                padded.paste(background, (0, 700))
                extra['help_bg'] = padded
            else:
                extra['help_bg'] = background.copy()

    return icon, _load_help_data(), extra


async def _render_help(
    key: tuple[Any, ...],
    plugin_icon_path: Path,
    custom_banner_bg_path: Path | None,
    custom_help_bg_path: Path | None,
    column: int,
    pm: int,
) -> str:
    async def render() -> str:
        icon, data, extra = await asyncio.to_thread(
            _build_help_inputs,
            plugin_icon_path,
            custom_banner_bg_path,
            custom_help_bg_path,
        )
        return await get_new_help(
            plugin_name='TodayWaifu',
            plugin_info={'v1.0': ''},
            plugin_icon=icon,
            plugin_help=data,
            plugin_prefix='',
            help_mode='dark',
            banner_sub_text='找到你今天的她',
            # This module owns the mtime/config-aware cache key above.
            enable_cache=False,
            column=column,
            pm=pm,
            **extra,
        )

    task = _HELP_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(render())
        _HELP_INFLIGHT[key] = task
    try:
        result = await task
    finally:
        if task.done() and _HELP_INFLIGHT.get(key) is task:
            _HELP_INFLIGHT.pop(key, None)
    _HELP_CACHE[key] = result
    _HELP_CACHE.move_to_end(key)
    while len(_HELP_CACHE) > _HELP_CACHE_MAX_ENTRIES:
        _HELP_CACHE.popitem(last=False)
    return result


@help_sv.on_fullmatch(
    ('今日老婆帮助', '老婆帮助'),
    block=True,
    to_ai="""查看 TodayWaifu 今日老婆插件帮助。
    当用户问“今日老婆怎么用”“今日老婆帮助”“老婆插件有什么命令”时调用。
    Args:
        text: 无需参数，留空。
    """,
)
async def daily_wife_help(bot: Bot, ev: Event):
    plugin_icon_path = _show_config_path('DailyWifeHelpIconUpload') or HELP_ICON_PATH
    if not plugin_icon_path.is_file():
        logger.warning(f'{LOG_PREFIX} 插件图标不存在: {plugin_icon_path}')
        return await _safe_send(bot, '帮助图片生成失败，ICON.png 缺失。')

    custom_banner_bg_path = _show_config_path('DailyWifeHelpBannerBgUpload')
    custom_help_bg_path = _show_config_path('DailyWifeHelpBgUpload')
    banner_bg_path = custom_banner_bg_path or _BANNER_BG_PATH
    help_bg_path = custom_help_bg_path or _BG_PATH
    column = _help_column()
    key = _help_cache_key(
        plugin_icon_path,
        banner_bg_path,
        help_bg_path,
        column,
        int(ev.user_pm),
    )
    image = _HELP_CACHE.get(key)
    if image is None:
        image = await _render_help(
            key,
            plugin_icon_path,
            custom_banner_bg_path,
            custom_help_bg_path,
            column,
            int(ev.user_pm),
        )
    else:
        _HELP_CACHE.move_to_end(key)
    await _safe_send(bot, MessageSegment.image(image))


if HELP_ICON_PATH.is_file():
    try:
        with Image.open(HELP_ICON_PATH) as _help_icon:
            register_help('TodayWaifu', '今日老婆帮助', _help_icon.convert('RGBA'))
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 注册插件帮助失败: {exc}')
