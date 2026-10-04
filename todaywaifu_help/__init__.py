"""TodayWaifu 帮助图渲染。

命令入口仍在 TodayWaifu/help.py（priority=0，避免被「今日老婆」前缀截获）。

提供两套可切换的绘图风格（1=二次元风格，2=手绘风格，默认 2），
分类条、背景与横幅按风格成套切换，命令卡片贴图两风格共用。
"""

from .get_help import (
    ICON,
    ICON_PATH,
    ITEM_PATH,
    TEXT_PATH,
    HELP_STYLES,
    DEFAULT_STYLE,
    HELP_STYLE_NAMES,
    HelpButtons,
    get_help,
    resolve_style_path,
)

__all__ = [
    "ICON",
    "ICON_PATH",
    "TEXT_PATH",
    "ITEM_PATH",
    "HELP_STYLES",
    "HELP_STYLE_NAMES",
    "DEFAULT_STYLE",
    "HelpButtons",
    "get_help",
    "resolve_style_path",
]
