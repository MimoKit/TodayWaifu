from __future__ import annotations

import json
from typing import Dict, Optional
from pathlib import Path

import aiofiles
from PIL import Image

from gsuid_core.help.model import PluginHelp
from gsuid_core.help.draw_new_plugin_help import get_new_help

PLUGIN_DIR = Path(__file__).resolve().parent.parent
ICON = PLUGIN_DIR / "ICON.png"
HELP_DATA = PLUGIN_DIR / "help.json"
ICON_PATH = PLUGIN_DIR / "texture2d" / "icons"
TEXT_PATH = Path(__file__).resolve().parent / "texture2d"
# 命令卡片贴图与风格无关：两种风格下都是同一张 490x175，故只存一份共用，
# 不放进风格目录里重复两遍。
ITEM_PATH = TEXT_PATH / "item.png"

# 帮助图风格编号 -> 该风格的横幅/背景/分类条所在子目录。
# 分类条、背景与横幅是一整套配色，交叉取用（如手绘横幅配二次元分类条）会明显割裂，
# 故按风格整套切换，不提供跨风格混搭的入口。
HELP_STYLES: Dict[int, Path] = {
    1: TEXT_PATH / "anime",
    2: TEXT_PATH / "handdrawn",
}
HELP_STYLE_NAMES: Dict[int, str] = {
    1: "二次元风格",
    2: "手绘风格",
}
# 手绘风格为默认：底色柔和浅，深色标题文字在其上对比度最高、最易读。
DEFAULT_STYLE = 2

# 浅色立绘底上，light 模式默认灰 (102,102,102) 几乎看不见
HELP_TEXT_COLORS = {
    "banner_title_color": (10, 12, 16),
    "banner_sub_color": (22, 24, 30),
    "cag_title_color": (10, 12, 16),
    "cag_sub_color": (22, 24, 30),
}

# help.json 命令名 -> texture2d/icons 文件名（不含后缀）
ICON_ALIAS: Dict[str, str] = {
    "今日老婆": "今日老婆",
    "今日老公": "今日老公",
    "今日异环老婆": "今日老婆",
    "今日战双老婆": "今日老婆",
    "上传战双老婆图片": "老婆上传图片",
    "今日普通老婆": "今日老婆",
    "老婆列表": "老婆列表",
    "老公列表": "老公列表",
    "普通老婆列表": "老婆列表",
    "抢老婆": "抢老婆",
    "送老婆": "送老婆",
    "接受老婆赠送": "同意送老婆",
    "拒绝老婆赠送": "拒绝送老婆",
    "抢老公": "抢老婆",
    "送老公": "送老婆",
    "接受老公赠送": "同意送老婆",
    "拒绝老公赠送": "拒绝送老婆",
    "离婚": "老婆删除",
    "老公离婚": "老婆删除",
    "娶群友": "娶群友",
    "今日萝莉": "今日萝莉",
    "抢萝莉": "抢老婆",
    "送萝莉": "送老婆",
    "接受萝莉赠送": "同意送老婆",
    "拒绝萝莉赠送": "拒绝送老婆",
    "萝莉离婚": "删除萝莉图片",
    "上传萝莉图片": "今日萝莉上传",
    "查看萝莉图片": "今日萝莉列表",
    "删除萝莉图片": "删除萝莉图片",
    # 今日正太与今日萝莉同构，复用萝莉一套图标。
    "今日正太": "今日萝莉",
    "抢正太": "抢老婆",
    "送正太": "送老婆",
    "接受正太赠送": "同意送老婆",
    "拒绝正太赠送": "拒绝送老婆",
    "正太离婚": "老婆删除",
    "创建老婆": "老婆创建",
    "上传老婆图片": "老婆上传图片",
    "查看老婆图片": "老婆图片列表",
    "删除老婆": "老婆删除",
    "分配老婆": "分配老婆",
}


class HelpButtons:
    """帮助图按钮贴图包：item 为命令卡片，cag_bg 为分类条（可多张循环）。

    卡片贴图两种风格共用，故从 texture2d 根目录取；分类条按风格取自各自子目录。
    """

    def __init__(self, text_path: Path = TEXT_PATH, item_path: Path = ITEM_PATH):
        self.item = Image.open(item_path).convert("RGBA")
        numbered = sorted(
            text_path.glob("cag_bg_[0-9]*.png"),
            key=lambda p: p.stem,
        )
        if numbered:
            self.cag_bg = [Image.open(path).convert("RGBA") for path in numbered]
        else:
            self.cag_bg = [Image.open(text_path / "cag_bg.png").convert("RGBA")]


def resolve_style_path(style: int) -> Path:
    """把配置里的风格编号解析成贴图目录，未知编号回落到默认风格。

    该值来自用户手填的配置，可能越界或根本不是整数；此处不抛错而是回落到默认风格，
    避免一个无效取值让整个帮助命令不可用。
    """
    return HELP_STYLES.get(style, HELP_STYLES[DEFAULT_STYLE])


def attach_command_icons(
    plugin_help: Dict[str, PluginHelp], icon_path: Path
) -> Dict[str, PluginHelp]:
    for group in plugin_help.values():
        for item in group.get("data", []):
            name = str(item.get("name", ""))
            stem = ICON_ALIAS.get(name, name)
            icon_file = icon_path / f"{stem}.png"
            if icon_file.is_file():
                item["icon"] = str(icon_file)
    return plugin_help


async def get_help_data() -> Dict[str, PluginHelp]:
    async with aiofiles.open(HELP_DATA, "rb") as file:
        return json.loads(await file.read())


async def get_help(
    pm: int = 6,
    *,
    plugin_icon: Optional[Image.Image] = None,
    banner_bg: Optional[Image.Image] = None,
    help_bg: Optional[Image.Image] = None,
    icon_path: Optional[Path] = None,
    column: int = 4,
    style: int = DEFAULT_STYLE,
    enable_cache: bool = False,
    plugin_prefix: str = "",
):
    icon_dir = icon_path or ICON_PATH
    # 横幅/背景/分类条统一按风格取，避免交叉取用产生割裂的配色。
    text_dir = resolve_style_path(style)
    plugin_help = attach_command_icons(await get_help_data(), icon_dir)
    buts = HelpButtons(text_dir)
    if plugin_icon is None:
        plugin_icon = Image.open(ICON).convert("RGBA")
    if banner_bg is None:
        banner_bg = Image.open(text_dir / "banner_bg.jpg").convert("RGBA")
    if help_bg is None:
        help_bg = Image.open(text_dir / "bg.jpg").convert("RGBA")

    return await get_new_help(
        plugin_name="TodayWaifu",
        plugin_info={"v1.0": ""},
        plugin_icon=plugin_icon,
        plugin_help=plugin_help,
        plugin_prefix=plugin_prefix,
        help_mode="light",
        banner_bg=banner_bg,
        banner_sub_text="找到你今天的她",
        help_bg=help_bg,
        cag_bg=buts,
        item_bg=buts.item,
        icon_path=icon_dir,
        enable_cache=enable_cache,
        column=column,
        pm=pm,
        **HELP_TEXT_COLORS,
    )
