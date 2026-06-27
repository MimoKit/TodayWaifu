"""TodayWaifu - help module."""
from __future__ import annotations

import json
from pathlib import Path

from gsuid_core.help.draw_new_plugin_help import get_new_help

from .shared import *  # noqa: F403

_HELP_JSON_PATH = BASE_DIR / 'help.json'
_TEXTURE_DIR = BASE_DIR / 'texture2d'
_BANNER_BG_PATH = BASE_DIR / 'fb93f5370f556a51db172863420aa50e.png'
_BG_PATH = _TEXTURE_DIR / 'bg.jpg'
_ICON_PATH = _TEXTURE_DIR / 'icons'


def _load_help_data():
    with _HELP_JSON_PATH.open('r', encoding='utf-8') as f:
        return json.load(f)


@sv.on_fullmatch('今日老婆帮助', block=True)
async def daily_wife_help(bot: Bot, ev: Event):
    if not HELP_ICON_PATH.is_file():
        logger.warning(f'{LOG_PREFIX} 插件图标不存在: {HELP_ICON_PATH}')
        return await bot.send('帮助图片生成失败，ICON.png 缺失。')

    with Image.open(HELP_ICON_PATH) as _icon:
        icon = _icon.convert('RGBA')

    banner_bg = None
    if _BANNER_BG_PATH.is_file():
        with Image.open(_BANNER_BG_PATH) as _bb:
            _bb = _bb.convert('RGBA')
            bw, bh = _bb.size
            # 只保留上面 40%，大切底部
            banner_bg = _bb.crop((0, 0, bw, int(bh * 0.40)))

    help_bg = None
    if _BG_PATH.is_file():
        with Image.open(_BG_PATH) as _bg:
            _bg = _bg.convert('RGBA')
            bgw, bgh = _bg.size
            # 顶部填充暗色，把人脸推到横幅以下
            pad_h = 700
            _padded = Image.new('RGBA', (bgw, bgh + pad_h), (15, 15, 25, 255))
            _padded.paste(_bg, (0, pad_h))
            help_bg = _padded

    data = _load_help_data()
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
        pm=ev.user_pm,
        **extra,
    )
    await bot.send(MessageSegment.image(img))


if HELP_ICON_PATH.is_file():
    try:
        with Image.open(HELP_ICON_PATH) as _help_icon:
            register_help('TodayWaifu', '今日老婆帮助', _help_icon.convert('RGBA'))
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 注册插件帮助失败: {exc}')
