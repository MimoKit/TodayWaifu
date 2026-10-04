"""TodayWaifu - help module.

帮助图由外部绘图工具渲染，单次耗时以秒计，且输入包含 JSON 与多份图片资源。因此本模块
的重点不是渲染本身，而是把「渲染结果何时仍然有效」判定清楚：任何参与渲染的资源发生
变化都必须触发重绘，否则用户会长期看到旧帮助图。
"""
from __future__ import annotations

import json
from pathlib import Path
from collections import OrderedDict

from PIL import Image

from gsuid_core.help.model import PluginHelp
from gsuid_core.help.draw_new_plugin_help import get_new_help

from .shared import (
    LOG_PREFIX,
    HELP_ICON_PATH,
    Bot,
    Event,
    MessageSegment,
    logger,
    asyncio,
    help_sv,
    _safe_send,
    register_help,
)
from .executor import run_blocking
from ..daily_wife_config import DailyWifeShowConfig

# 横幅、背景、分类条与命令卡贴图统一由外部绘图资源包提供，路径在该包内解析。
# 横幅/背景/分类条随风格成套切换，故不固定为模块级常量，而是每次调用时按风格解析；
# 命令卡片贴图与风格无关，是唯一可以全局固定的资源。
from ..todaywaifu_help.get_help import (
    HELP_DATA as _HELP_JSON_PATH,
    ICON_PATH as _ICON_PATH,
    ITEM_PATH as _HELP_ITEM,
    DEFAULT_STYLE as _HELP_DEFAULT_STYLE,
    HELP_STYLE_NAMES as _HELP_STYLE_NAMES,
    HELP_TEXT_COLORS,
    HelpButtons,
    resolve_style_path,
    attach_command_icons,
)

# 资源签名：路径、mtime_ns、大小；缺失时为 None
# 用 mtime_ns 而非秒级 mtime：同秒内的两次写入在秒级精度下无法区分，会漏掉重绘。
PathSignature = tuple[str, int, int] | None
# 帮助缓存键：7 个资源签名 + 列数 + 权限等级 + 风格编号（见 _help_cache_key）
# 权限等级参与键是因为帮助内容随权限裁剪，不同权限的用户不能共用同一张图。
# 分类条与命令卡贴图同样计入签名：它们参与渲染，替换后若不换键会长期显示旧图。
# 风格编号显式入键：横幅/背景/分类条按风格成套切换，仅靠路径签名虽也能区分，
# 但一旦两套风格共用某张资源，键就会静默复用另一种风格的旧图。
HelpCacheKey = tuple[
    PathSignature,
    PathSignature,
    PathSignature,
    PathSignature,
    PathSignature,
    tuple[PathSignature, ...],
    PathSignature,
    int,
    int,
    int,
]

# 帮助图缓存容量刻意很小（4）：键维度多，不同键的图几乎不会复用，
# 保留过多只会长期占用内存中较大的位图。
_HELP_CACHE_MAX_ENTRIES = 4
_HELP_CACHE: OrderedDict[HelpCacheKey, str] = OrderedDict()
# 在途渲染合并表：同一键的并发请求共享一次渲染，否则同时多人触发帮助会重复起绘图任务。
_HELP_INFLIGHT: dict[HelpCacheKey, asyncio.Task[str]] = {}


def _load_help_data() -> dict[str, PluginHelp]:
    # 帮助条目以 JSON 维护而非写在 Python 中：文案调整无需改动代码，
    # 且绘图工具本就按该结构消费数据。
    with _HELP_JSON_PATH.open('r', encoding='utf-8') as f:
        return json.load(f)


def _show_config_path(key: str) -> Path | None:
    # 配置项可能被填成空串、带引号的路径或指向已被删除的文件；三种情况都按「未配置」
    # 处理，由调用方回落到内置资源，而不是把无效路径传给 PIL 触发异常。
    value = str(DailyWifeShowConfig.get_config(key).data or '').strip().strip('"')
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_file() else None


def _help_style() -> int:
    """读出配置里的帮助图风格编号，越界或非整数一律回落到默认风格。

    风格决定整套横幅/背景/分类条贴图，取值非法时不能静默套用另一套风格，
    因此这里先夹到已知编号区间再交由 resolve_style_path 解析目录。
    """
    value = DailyWifeShowConfig.get_config('DailyWifeHelpStyle').data
    try:
        style = int(value)
    except (TypeError, ValueError):
        style = _HELP_DEFAULT_STYLE
    return style if style in _HELP_STYLE_NAMES else _HELP_DEFAULT_STYLE


def _help_column() -> int:
    # 列数必须夹在 [1, 10]：绘图布局在超出该范围时会溢出画布或除零，
    # 非法配置按默认 3 列处理。
    value = DailyWifeShowConfig.get_config('DailyWifeHelpColumn').data
    try:
        column = int(value)
    except (TypeError, ValueError):
        column = 3
    return max(1, min(10, column))


def _path_signature(path: Path | None) -> tuple[str, int, int] | None:
    # 读取失败返回 None 而非抛出：签名仅用于判断是否需要重绘，
    # 一次 stat 失败不应让帮助命令整体不可用。
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
    text_path: Path,
    style: int,
    column: int,
    pm: int,
) -> HelpCacheKey:
    # 每个签名对应一份参与渲染的资源，缺一都会导致「换了图但帮助未更新」。
    return (
        _path_signature(_HELP_JSON_PATH),
        _path_signature(icon_path),
        _path_signature(banner_bg_path),
        _path_signature(help_bg_path),
        _path_signature(_ICON_PATH),
        # 分类条为多张轮换，故整组取签名；命令卡为单张。
        tuple(_path_signature(path) for path in sorted(text_path.glob('cag_bg*.png'))),
        # 命令卡与风格无关，取共用的那一份。
        _path_signature(_HELP_ITEM),
        column,
        pm,
        style,
    )


def _build_help_inputs(
    plugin_icon_path: Path,
    custom_banner_bg_path: Path | None,
    custom_help_bg_path: Path | None,
    text_path: Path,
) -> tuple[
    Image.Image,
    dict[str, PluginHelp],
    dict[str, Image.Image | Path | list[Image.Image]],
]:
    """在线程中读取 JSON 和 PIL 资源，避免阻塞事件循环。"""
    with Image.open(plugin_icon_path) as source:
        icon = source.convert('RGBA')

    extra: dict[str, Image.Image | Path | list[Image.Image]] = {}
    # text_path 是当前风格的内置贴图目录，仅在用户未上传自定义图时才作为回落。
    banner_bg_path = custom_banner_bg_path or text_path / 'banner_bg.jpg'
    if banner_bg_path.is_file():
        with Image.open(banner_bg_path) as source:
            # 横幅已按横幅比例制作，内置与自定义都不再裁切。
            extra['banner_bg'] = source.convert('RGBA')

    help_bg_path = custom_help_bg_path or text_path / 'bg.jpg'
    if help_bg_path.is_file():
        with Image.open(help_bg_path) as source:
            extra['help_bg'] = source.convert('RGBA')

    # 图标以目录形式传入而非逐个打开：绘图工具按需读取，避免一次性载入全部图标位图。
    if _ICON_PATH.is_dir():
        extra['icon_path'] = _ICON_PATH

    # 分类条与命令卡按卡片式版式绘制：分类条取自当前风格目录并多张轮换，
    # 命令卡为两风格共用的单张。
    buttons = HelpButtons(text_path)
    extra['cag_bg'] = buttons.cag_bg
    extra['item_bg'] = buttons.item

    # 逐条命令补上图标：help.json 只写命令名，图标需按别名表回查资源目录。
    return icon, attach_command_icons(_load_help_data(), _ICON_PATH), extra


async def _render_help(
    key: HelpCacheKey,
    plugin_icon_path: Path,
    custom_banner_bg_path: Path | None,
    custom_help_bg_path: Path | None,
    text_path: Path,
    column: int,
    pm: int,
) -> str:
    async def render() -> str:
        # 图像解码与 JSON 解析都是阻塞操作，必须投递到专用线程池，
        # 否则会冻结事件循环并连带停摆其它并发命令。
        icon, data, extra = await run_blocking(
            _build_help_inputs,
            plugin_icon_path,
            custom_banner_bg_path,
            custom_help_bg_path,
            text_path,
        )
        return await get_new_help(
            plugin_name='TodayWaifu',
            plugin_info={'v1.0': ''},
            plugin_icon=icon,
            plugin_help=data,
            plugin_prefix='',
            help_mode='light',
            banner_sub_text='找到你今天的她',
            # 绘图工具自带的缓存无法感知 mtime 与配置变化，
            # 本模块已按资源签名维护更精确的缓存键，故在此关闭其内部缓存。
            enable_cache=False,
            column=column,
            pm=pm,
            # 浅色立绘底上默认灰字几乎不可见，这里显式指定深色文字。
            **HELP_TEXT_COLORS,
            **extra,
        )

    # 合并同一键的并发渲染：绘图成本高，重复渲染既拖慢响应也浪费线程池。
    task = _HELP_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(render())
        _HELP_INFLIGHT[key] = task
    try:
        result = await task
    finally:
        # 仅在任务已结束且仍是当前项时移除，避免误删后来者登记的新任务。
        if task.done() and _HELP_INFLIGHT.get(key) is task:
            _HELP_INFLIGHT.pop(key, None)
    # 渲染完成后写入 LRU，并按容量上限淘汰最久未用的键。
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
    covers=['插件全部指令与用法说明'],
    aliases=['今日老婆·帮助', '今日老婆·怎么用'],
)
async def daily_wife_help(bot: Bot, ev: Event) -> list[str] | None:
    # 图标缺失时直接给出明确提示并终止：后续渲染必然失败，
    # 提前返回可以避免把底层绘图异常暴露成难以理解的报错。
    plugin_icon_path = _show_config_path('DailyWifeHelpIconUpload') or HELP_ICON_PATH
    if not plugin_icon_path.is_file():
        logger.warning(f'{LOG_PREFIX} 插件图标不存在: {plugin_icon_path}')
        return await _safe_send(bot, '帮助图片生成失败，ICON.png 缺失。')

    custom_banner_bg_path = _show_config_path('DailyWifeHelpBannerBgUpload')
    custom_help_bg_path = _show_config_path('DailyWifeHelpBgUpload')
    # 风格决定内置横幅/背景/分类条取自哪一套贴图；先解析出目录再据此拼路径，
    # 这样键中登记的路径与 _build_help_inputs 的实际取值必然一致。
    style = _help_style()
    text_path = resolve_style_path(style)
    # 键中登记的是实际参与渲染的路径（自定义或内置），与 _build_help_inputs 的选择
    # 逻辑保持一致，否则自定义图切换后缓存键不变，帮助图不会更新。
    banner_bg_path = custom_banner_bg_path or text_path / 'banner_bg.jpg'
    help_bg_path = custom_help_bg_path or text_path / 'bg.jpg'
    column = _help_column()
    key = _help_cache_key(
        plugin_icon_path,
        banner_bg_path,
        help_bg_path,
        text_path,
        style,
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
            text_path,
            column,
            int(ev.user_pm),
        )
    else:
        # 命中也要更新 LRU 位置，否则常用键会因其它键写入而被提前淘汰。
        _HELP_CACHE.move_to_end(key)
    await _safe_send(bot, MessageSegment.image(image))


# 注册入口在导入期执行一次：Core 只在插件加载时读取帮助注册表，
# 若延后到首次命令再注册，菜单中将始终看不到本插件。
if HELP_ICON_PATH.is_file():
    try:
        with Image.open(HELP_ICON_PATH) as _help_icon:
            register_help('TodayWaifu', '今日老婆帮助', _help_icon.convert('RGBA'))
    except OSError as exc:
        # 图标损坏只影响菜单图标显示，不应阻断插件加载，故仅记录告警。
        logger.warning(f'{LOG_PREFIX} 注册插件帮助失败: {exc}')
