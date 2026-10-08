"""TodayWaifu 角色剧情与对话台词模块，读取随插件分发的 role_quotes.json。

台词库是只读的内置资源（路径见 resource_paths.role_quotes_path），用户上传的自定义
角色不参与覆盖：用户图片目录只提供图像，若允许覆盖台词，升级时新旧文案会混杂，
且同一角色在不同部署下呈现不一致。内置库缺失或格式损坏时整体回退到空结果，
由调用方决定是否省略台词行，而不是让抽卡流程失败。
"""

from __future__ import annotations

import json
import random
from typing import Tuple

from .resource_paths import role_quotes_path

# 台词与署名同处一条消息，超长台词在手机 QQ 气泡里会折成多行；该值是排版可接受的
# 上限，内置库应逐条控制在此长度内，超出仅作截断兜底。
MAX_QUOTE_LENGTH = 60

# 缓存用 (内容, 文件 mtime) 组合校验，而不是只缓存一次：插件升级会替换同路径的台词
# 文件，仅按 mtime 判断即可在无需重启 Core 的情况下让新台词生效。
_QUOTES_CACHE: dict[str, tuple[str, ...]] = {}
_DEFAULT_QUOTES_CACHE: tuple[str, ...] = ()
_CACHE_MTIME: float = 0.0


def _load_bundled_quotes() -> Tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    global _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE, _CACHE_MTIME
    path = role_quotes_path()
    # 缺少台词库不算错误：插件其余功能照常可用，仅表现为没有台词。
    if not path.is_file():
        return {}, ()

    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    # 文件未变更时直接返回旧缓存，避免每次抽卡都重新解析整个 JSON。
    if _QUOTES_CACHE and mtime == _CACHE_MTIME:
        return _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE

    # 解析失败时返回上一次成功的缓存而非空结果：已加载过的台词不会因为一次读取
    # 抖动而全部消失，最坏情况是继续沿用旧内容。
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE

    # 顶层结构不是对象说明文件已损坏，同样按「沿用旧缓存」处理。
    if not isinstance(data, dict):
        return _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE

    raw_quotes = data.get("role_quotes")
    raw_default = data.get("default_quotes")

    quotes: dict[str, tuple[str, ...]] = {}
    if isinstance(raw_quotes, dict):
        for k, v in raw_quotes.items():
            # 逐条容错：单个角色写成非列表只丢该角色，不影响整库可用性。
            if isinstance(v, (list, tuple)):
                quotes[str(k)] = tuple(str(x) for x in v if str(x).strip())

    defaults: tuple[str, ...] = ()
    if isinstance(raw_default, (list, tuple)):
        defaults = tuple(str(x) for x in raw_default if str(x).strip())

    _QUOTES_CACHE = quotes
    _DEFAULT_QUOTES_CACHE = defaults
    # mtime 在成功解析后才记录：解析失败不更新，使下次调用仍会重试读取。
    _CACHE_MTIME = mtime
    return _QUOTES_CACHE, _DEFAULT_QUOTES_CACHE


def get_role_quote(name: str) -> str:
    """获取角色的剧情/对话文本，附带角色名，文本内容不超过 MAX_QUOTE_LENGTH 字。

    回退顺序为「精确名 → 最长包含键 → 默认台词 → 空字符串」。精确匹配优先是为了让
    用户能看到完全对应的角色台词；包含匹配只用于名称被加上前后缀（如「老婆」）的场景。
    返回空串而非抛错，调用方据此省略台词行，避免影响图片发送这一主流程。
    """
    clean_name = name.strip()
    role_quotes, default_quotes = _load_bundled_quotes()

    quotes: tuple[str, ...] | None = role_quotes.get(clean_name)
    matched_name = clean_name
    if quotes is None:
        # 只认「角色名里含某个已知键」；反向匹配会让「心」被「鉴心」截胡
        # 取最长命中键：多个键同时包含于角色名时，长键语义更具体，是更可靠的归属。
        contained = [k for k in role_quotes if k in clean_name]
        if contained:
            matched_name = max(contained, key=len)
            quotes = role_quotes[matched_name]
    if quotes is None:
        # 无任何匹配时用默认台词，署名仍保留原始名，使文本与当前角色一致。
        quotes = default_quotes
        matched_name = clean_name

    if not quotes:
        return ""

    selected = random.choice(quotes)
    # 截断仅用于兜底：内置库已控制在限长内，此处防止手工编辑库文件后出现超长台词
    # 撑破气泡排版。
    if len(selected) > MAX_QUOTE_LENGTH:
        selected = selected[:MAX_QUOTE_LENGTH]

    quote_line = f"「{selected}」"
    author_line = f"——{matched_name}"

    # 手机QQ群单行气泡上限约为15字宽，固定为14可确保署名紧贴右侧且不被强制换行
    # 用全角空格填充而非半角：气泡按字宽排版，半角空格的实际占位与字符数不成正比，
    # 无法据此推算右对齐所需宽度。
    target_width = 14
    author_width = len(author_line)
    spaces = "\u3000" * max(0, target_width - author_width)
    return f"{quote_line}\n{spaces}{author_line}"
