"""TodayWaifu - help module."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from gsuid_core.help.draw_new_plugin_help import get_new_help

from ..daily_wife_config import DailyWifeShowConfig
from .shared import *  # noqa: F403

_HELP_JSON_PATH = BASE_DIR / 'help.json'
_TEXTURE_DIR = BASE_DIR / 'texture2d'
_BANNER_BG_PATH = BASE_DIR / 'fb93f5370f556a51db172863420aa50e.png'
_BG_PATH = _TEXTURE_DIR / 'bg.jpg'
_ICON_PATH = _TEXTURE_DIR / 'icons'


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

    with Image.open(plugin_icon_path) as _icon:
        icon = _icon.convert('RGBA')

    banner_bg = None
    custom_banner_bg_path = _show_config_path('DailyWifeHelpBannerBgUpload')
    banner_bg_path = custom_banner_bg_path or _BANNER_BG_PATH
    if banner_bg_path.is_file():
        with Image.open(banner_bg_path) as _bb:
            _bb = _bb.convert('RGBA')
            if custom_banner_bg_path is None:
                bw, bh = _bb.size
                # 只保留上面 40%，大切底部
                banner_bg = _bb.crop((0, 0, bw, int(bh * 0.40)))
            else:
                banner_bg = _bb

    help_bg = None
    custom_help_bg_path = _show_config_path('DailyWifeHelpBgUpload')
    help_bg_path = custom_help_bg_path or _BG_PATH
    if help_bg_path.is_file():
        with Image.open(help_bg_path) as _bg:
            _bg = _bg.convert('RGBA')
            if custom_help_bg_path is None:
                bgw, bgh = _bg.size
                # 顶部填充暗色，把人脸推到横幅以下
                pad_h = 700
                _padded = Image.new('RGBA', (bgw, bgh + pad_h), (15, 15, 25, 255))
                _padded.paste(_bg, (0, pad_h))
                help_bg = _padded
            else:
                help_bg = _bg

    data = _load_help_data()
    column = _help_column()
    extra: dict = {}
    if banner_bg is not None:
        extra['banner_bg'] = banner_bg
    if help_bg is not None:
        extra['help_bg'] = help_bg
    if _ICON_PATH.is_dir():
        extra['icon_path'] = _ICON_PATH
    img = await get_new_help(
        plugin_name='TodayWaifu',
        plugin_info={'v1.0': ''},
        plugin_icon=icon,
        plugin_help=data,
        plugin_prefix='',
        help_mode='dark',
        banner_sub_text='找到你今天的她',
        enable_cache=False,
        column=column,
        pm=ev.user_pm,
        **extra,
    )
    await _safe_send(bot, MessageSegment.image(img))


if HELP_ICON_PATH.is_file():
    try:
        with Image.open(HELP_ICON_PATH) as _help_icon:
            register_help('TodayWaifu', '今日老婆帮助', _help_icon.convert('RGBA'))
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 注册插件帮助失败: {exc}')
