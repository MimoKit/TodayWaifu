"""TodayWaifu - 功能未开启提示卡片（playwright 渲染）。

某个玩法在配置里没开启时，不再静默忽略命令，而是渲染一张浅色可爱风的
提示卡片图发给用户，告诉 TA 这是哪个功能、用哪条命令、去控制台开哪个开关。

浏览器不挑版本：优先用 playwright 自带 chromium，找不到就依次尝试系统里
已安装的 Chrome / Edge / Chromium / Brave，全部不可用则降级为纯文字提示。
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING
from datetime import datetime

from .shared import LOG_PREFIX, Bot, MessageSegment, logger, _safe_send

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

_CARD_WIDTH = 1040
_DEVICE_SCALE = 2

# 渲染结果内容只跟功能键有关，静态可缓存
_CARD_CACHE: dict[str, bytes] = {}
# 记录首次探测成功的浏览器启动参数，之后直接用，不再逐个试
_LAUNCH_KWARGS_CACHE: dict[str, str] | None = None

# 系统浏览器可执行文件名（按常见程度排序），配合 shutil.which 探测
_SYSTEM_BROWSER_BINARIES = (
    'google-chrome',
    'google-chrome-stable',
    'chromium',
    'chromium-browser',
    'microsoft-edge',
    'microsoft-edge-stable',
    'brave-browser',
)


class _FeatureInfo:
    """一个可开关玩法的卡片文案。"""

    def __init__(
        self,
        title: str,
        config_title: str,
        config_key: str,
        commands: tuple[str, ...],
        accent: str,
        accent_soft: str,
    ) -> None:
        self.title = title
        self.config_title = config_title
        self.config_key = config_key
        self.commands = commands
        self.accent = accent
        self.accent_soft = accent_soft


_FEATURES: dict[str, _FeatureInfo] = {
    'husband': _FeatureInfo(
        '今日老公',
        '启用今日老公',
        'DailyWifeHusbandEnabled',
        ('今日老公', '老公列表'),
        '#5aa9e6',
        '#e3f0fb',
    ),
    'nte': _FeatureInfo(
        '今日异环老婆',
        '启用今日异环老婆',
        'DailyWifeNteEnabled',
        ('今日异环老婆',),
        '#9f8cf2',
        '#efebfd',
    ),
    'pgr': _FeatureInfo(
        '今日战双老婆',
        '启用今日战双老婆',
        'DailyWifePgrEnabled',
        ('今日战双老婆', 'jrzslp'),
        '#7c83e8',
        '#ebebfb',
    ),
    'loli': _FeatureInfo(
        '今日萝莉',
        '启用今日萝莉',
        'DailyLoliEnabled',
        ('今日萝莉',),
        '#f083a6',
        '#fdeaf1',
    ),
    'shota': _FeatureInfo(
        '今日正太',
        '启用今日正太',
        'DailyShotaEnabled',
        ('今日正太',),
        '#f2a45c',
        '#fdf1e3',
    ),
    'marry_member': _FeatureInfo(
        '娶群友',
        '启用娶群友',
        'DailyWifeMarryGroupMemberEnabled',
        ('娶群友',),
        '#4fc48d',
        '#e6f7ef',
    ),
    'rob_wife': _FeatureInfo(
        '抢老婆',
        '启用抢老婆',
        'DailyWifeRobEnabled',
        ('抢老婆 @对方',),
        '#f083a6',
        '#fdeaf1',
    ),
    'rob_husband': _FeatureInfo(
        '抢老公',
        '启用抢老公',
        'DailyHusbandRobEnabled',
        ('抢老公 @对方',),
        '#5aa9e6',
        '#e3f0fb',
    ),
    'rob_loli': _FeatureInfo(
        '抢萝莉',
        '启用抢萝莉',
        'DailyLoliRobEnabled',
        ('抢萝莉 @对方',),
        '#f083a6',
        '#fdeaf1',
    ),
    'rob_shota': _FeatureInfo(
        '抢正太',
        '启用抢正太',
        'DailyShotaRobEnabled',
        ('抢正太 @对方',),
        '#f2a45c',
        '#fdf1e3',
    ),
    'gift_wife': _FeatureInfo(
        '送老婆',
        '启用送老婆',
        'DailyWifeGiftEnabled',
        ('送老婆 @对方',),
        '#f083a6',
        '#fdeaf1',
    ),
    'gift_husband': _FeatureInfo(
        '送老公',
        '启用送老公',
        'DailyHusbandGiftEnabled',
        ('送老公 @对方',),
        '#5aa9e6',
        '#e3f0fb',
    ),
    'gift_loli': _FeatureInfo(
        '送萝莉',
        '启用送萝莉',
        'DailyLoliGiftEnabled',
        ('送萝莉 @对方',),
        '#f083a6',
        '#fdeaf1',
    ),
    'gift_shota': _FeatureInfo(
        '送正太',
        '启用送正太',
        'DailyShotaGiftEnabled',
        ('送正太 @对方',),
        '#f2a45c',
        '#fdf1e3',
    ),
    # normal 系列的开关配置项未在 CONFIG_DEFAULT 注册（恒为开启），
    # 这里登记仅用于兜底完整性，正常流程不会渲染到
    'rob_normal': _FeatureInfo(
        '抢老婆',
        '启用抢老婆',
        'DailyWifeNormalRobEnabled',
        ('抢老婆 @对方',),
        '#f083a6',
        '#fdeaf1',
    ),
    'gift_normal': _FeatureInfo(
        '送老婆',
        '启用送老婆',
        'DailyWifeNormalGiftEnabled',
        ('送老婆 @对方',),
        '#f083a6',
        '#fdeaf1',
    ),
}

_FONT_STACK = (
    '"PingFang SC", "Hiragino Sans GB", "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif'
)

# 装饰 SVG 的 path 数据，提出来避免 HTML 模板里的超长行
_HEART_PATH = (
    'M12 21s-7.5-4.6-10-9C.5 8 2.5 4.5 6 4.5c2.2 0 3.7 1.2 4.5 2.6'
    '.8-1.4 2.3-2.6 4.5-2.6 3.5 0 5.5 3.5 4 7.5-2.5 4.4-7 9-7 9z'
)
_STAR_PATH = 'M12 2l2.1 6.2L20 9.2l-5 4.1 1.6 6.4L12 16.2l-4.6 3.5L9 13.3 4 9.2l5.9-1z'
_ICON_HEART_PATH = (
    'M36 58c-9.5-5.8-18-12.6-21-20.4C12.6 31 16 25 21.5 25c4 0 6.8 2 8.6 4.7C32 27 34.8 25 38.8 25c5.5 0 8.9 6 5.5 12.6'
)


def _build_card_html(info: _FeatureInfo) -> str:
    chips = ''.join(f'<span class="chip">{cmd}</span>' for cmd in info.commands)
    today = datetime.now().strftime('%Y-%m-%d')
    a = info.accent
    soft = info.accent_soft
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: {_CARD_WIDTH}px;
    font-family: {_FONT_STACK};
    color: #5c4a52;
    background:
      radial-gradient(ellipse 620px 420px at 12% -6%, #ffe3ee 0%, transparent 70%),
      radial-gradient(ellipse 560px 460px at 96% 18%, #e8e4fd 0%, transparent 70%),
      radial-gradient(ellipse 720px 480px at 50% 108%, #fff0dd 0%, transparent 70%),
      linear-gradient(160deg, #fffaf3 0%, #fff3f6 52%, #f6f2ff 100%);
  }}
  #card {{
    position: relative;
    padding: 46px 52px 40px;
    overflow: hidden;
  }}
  /* 圆点网格 */
  .dots {{
    position: absolute;
    inset: 0;
    background-image: radial-gradient(rgba(214, 132, 166, 0.16) 2px, transparent 2.6px);
    background-size: 34px 34px;
    -webkit-mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, black, transparent 75%);
            mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, black, transparent 75%);
  }}
  .deco {{ position: absolute; pointer-events: none; }}
  header {{
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 30px;
  }}
  .badge {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 24px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.75);
    border: 1.5px solid #ffffff;
    box-shadow: 0 6px 18px rgba(214, 132, 166, 0.14);
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.22em;
    color: {a};
  }}
  .badge .pulse {{
    width: 11px;
    height: 11px;
    border-radius: 50%;
    background: {a};
    box-shadow: 0 0 0 5px {soft};
  }}
  .meta {{
    text-align: right;
    font-size: 15px;
    letter-spacing: 0.14em;
    color: #b99aa8;
    font-weight: 600;
  }}
  .meta b {{ color: #8f7280; letter-spacing: 0.3em; display: block; font-size: 13px; margin-bottom: 3px; }}
  main {{
    position: relative;
    background: rgba(255, 255, 255, 0.86);
    border: 2px solid #ffffff;
    border-radius: 44px;
    box-shadow:
      0 24px 60px rgba(214, 132, 166, 0.18),
      0 4px 14px rgba(214, 132, 166, 0.10);
    padding: 56px 60px 48px;
  }}
  /* 票根式侧边虚线缺口 */
  main::before, main::after {{
    content: '';
    position: absolute;
    top: 118px;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: #fff3f6;
    border: 2px solid #ffffff;
  }}
  main::before {{ left: -20px; box-shadow: inset 0 4px 10px rgba(214,132,166,0.12); }}
  main::after {{ right: -20px; box-shadow: inset 0 4px 10px rgba(214,132,166,0.12); }}
  .hero {{ display: flex; align-items: center; gap: 34px; }}
  .icon-wrap {{
    flex-shrink: 0;
    width: 128px;
    height: 128px;
    border-radius: 40px;
    background: linear-gradient(145deg, {soft}, #ffffff);
    border: 2px solid #ffffff;
    box-shadow: 0 12px 28px {soft}, inset 0 -6px 14px rgba(255,255,255,0.9);
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  h1 {{
    font-size: 54px;
    line-height: 1.2;
    letter-spacing: 0.02em;
    background: linear-gradient(120deg, {a} 10%, #d987ab 55%, #b48ae0 90%);
    -webkit-background-clip: text;
            background-clip: text;
    color: transparent;
    margin-bottom: 12px;
  }}
  .sub {{
    font-size: 24px;
    color: #8f7280;
    font-weight: 600;
  }}
  .sub b {{ color: {a}; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 26px; }}
  .chip {{
    padding: 9px 20px;
    border-radius: 999px;
    background: {soft};
    color: {a};
    font-size: 19px;
    font-weight: 700;
    letter-spacing: 0.04em;
    border: 1.5px dashed {a};
  }}
  .divider {{
    display: flex;
    align-items: center;
    gap: 18px;
    margin: 40px 0 34px;
    color: #d9a7bc;
  }}
  .divider::before, .divider::after {{
    content: '';
    flex: 1;
    border-top: 2px dashed #eccfdc;
  }}
  .steps {{ display: flex; flex-direction: column; gap: 18px; }}
  .step {{
    display: flex;
    align-items: center;
    gap: 22px;
    padding: 20px 26px;
    border-radius: 26px;
    background: linear-gradient(120deg, rgba(255,255,255,0.9), {soft}55);
    border: 1.5px solid #f7e4ec;
  }}
  .step .num {{
    flex-shrink: 0;
    width: 46px;
    height: 46px;
    border-radius: 16px;
    background: {a};
    color: #ffffff;
    font-size: 23px;
    font-weight: 800;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 6px 14px {a}66;
  }}
  .step b {{ font-size: 23px; color: #6b5560; display: block; margin-bottom: 4px; }}
  .step b i {{ color: {a}; font-style: normal; }}
  .step p {{ font-size: 17px; color: #ab8f9c; }}
  .keyrow {{
    margin-top: 30px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 16px 24px;
    border-radius: 20px;
    background: #fdf6f9;
    border: 1.5px solid #f3dde7;
  }}
  .key {{
    font-family: "SFMono-Regular", "Cascadia Mono", Consolas, "Noto Sans Mono CJK SC", monospace;
    font-size: 19px;
    font-weight: 700;
    color: {a};
    letter-spacing: 0.02em;
  }}
  .keyrow span:last-child {{ font-size: 15px; color: #b99aa8; letter-spacing: 0.2em; font-weight: 600; }}
  footer {{
    position: relative;
    margin-top: 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: #b99aa8;
    font-size: 16px;
    letter-spacing: 0.1em;
    font-weight: 600;
  }}
  footer .brand {{ color: {a}; letter-spacing: 0.24em; font-weight: 800; }}
</style>
</head>
<body>
<div id="card">
  <div class="dots"></div>

  <svg class="deco" style="top:36px; right:210px;" width="34" height="34"
       viewBox="0 0 24 24" fill="{a}" opacity="0.35">
    <path d="{_HEART_PATH}"/>
  </svg>
  <svg class="deco" style="bottom:120px; left:26px;" width="44" height="44"
       viewBox="0 0 24 24" fill="{a}" opacity="0.22">
    <path d="{_HEART_PATH}"/>
  </svg>
  <svg class="deco" style="bottom:60px; right:40px;" width="30" height="30"
       viewBox="0 0 24 24" fill="#b48ae0" opacity="0.35">
    <path d="{_STAR_PATH}"/>
  </svg>
  <svg class="deco" style="top:120px; left:34px;" width="26" height="26"
       viewBox="0 0 24 24" fill="#b48ae0" opacity="0.3">
    <path d="{_STAR_PATH}"/>
  </svg>

  <header>
    <div class="badge"><span class="pulse"></span>FEATURE OFFLINE · 功能未开启</div>
    <div class="meta"><b>TODAY WAIFU</b>{today}</div>
  </header>

  <main>
    <div class="hero">
      <div class="icon-wrap">
        <svg width="72" height="72" viewBox="0 0 72 72" fill="none">
          <path d="{_ICON_HEART_PATH}" stroke="{a}" stroke-width="4" stroke-linecap="round"/>
          <path d="M46 34l8-1-6.5 7 8-1" stroke="#b48ae0" stroke-width="3.4"
                stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="28" cy="41" r="2.6" fill="{a}"/>
          <path d="M25 48c2.4 2 5.6 3.2 9 3.6" stroke="{a}" stroke-width="3.4" stroke-linecap="round"/>
        </svg>
      </div>
      <div>
        <h1>这个功能还在睡觉哦</h1>
        <p class="sub">「<b>{info.title}</b>」功能当前未开启，暂时不能使用</p>
        <div class="chips">{chips}</div>
      </div>
    </div>

    <div class="divider">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="{_HEART_PATH}"/></svg>
    </div>

    <div class="steps">
      <div class="step">
        <div class="num">1</div>
        <div><b>打开 Bot 的网页控制台</b><p>浏览器进入 GsCore 的 WebConsole 管理页面</p></div>
      </div>
      <div class="step">
        <div class="num">2</div>
        <div><b>找到 <i>TodayWaifu</i> 插件配置</b><p>在插件管理或配置列表中定位今日老婆的配置项</p></div>
      </div>
      <div class="step">
        <div class="num">3</div>
        <div><b>打开「<i>{info.config_title}</i>」开关并保存</b><p>回来重新发送命令，就可以开心玩耍啦</p></div>
      </div>
    </div>

    <div class="keyrow">
      <span class="key">{info.config_key}</span>
      <span>配置项 ID</span>
    </div>
  </main>

  <footer>
    <span class="brand">TODAY WAIFU · 今日老婆</span>
    <span>开启之后再来找我玩呀</span>
  </footer>
</div>
</body>
</html>"""


def _fallback_text(info: _FeatureInfo) -> str:
    cmds = ' / '.join(info.commands)
    return (
        f'「{info.title}」功能还没有开启哦~\n'
        f'请主人在网页控制台的 TodayWaifu 配置中打开「{info.config_title}」开关，'
        f'保存后再发送 {cmds} 就可以使用啦'
    )


async def _launch_browser(playwright: Playwright) -> Browser:
    """按优先级尝试可用的浏览器，成功后缓存启动参数。"""
    global _LAUNCH_KWARGS_CACHE
    candidates: list[dict[str, str]] = []
    if _LAUNCH_KWARGS_CACHE is not None:
        candidates.append(_LAUNCH_KWARGS_CACHE)
    else:
        candidates.append({})
        candidates.append({'channel': 'chrome'})
        candidates.append({'channel': 'msedge'})
        for binary in _SYSTEM_BROWSER_BINARIES:
            path = shutil.which(binary)
            if path:
                candidates.append({'executable_path': path})

    last_error: Exception | None = None
    for kwargs in candidates:
        try:
            browser = await playwright.chromium.launch(headless=True, **kwargs)
        except Exception as exc:
            # 能力探测：这个浏览器不可用就换下一个，全部失败才报错
            last_error = exc
            logger.debug(f'{LOG_PREFIX} 未开启卡片浏览器启动失败 {kwargs or "bundled"}: {exc}')
            continue
        _LAUNCH_KWARGS_CACHE = kwargs
        if kwargs:
            logger.info(f'{LOG_PREFIX} 未开启提示卡片将使用浏览器: {kwargs}')
        return browser
    raise RuntimeError(f'没有可用的浏览器: {last_error}')


async def _render_feature_card(feature: str) -> bytes:
    cached = _CARD_CACHE.get(feature)
    if cached is not None:
        return cached

    from playwright.async_api import async_playwright

    html = _build_card_html(_FEATURES[feature])
    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright)
        try:
            page = await browser.new_page(
                viewport={'width': _CARD_WIDTH, 'height': 720},
                device_scale_factor=_DEVICE_SCALE,
            )
            await page.set_content(html, wait_until='load')
            await page.evaluate('document.fonts.ready.then(() => true)')
            image = await page.locator('#card').screenshot(type='png')
        finally:
            await browser.close()
    _CARD_CACHE[feature] = image
    return image


async def send_feature_disabled_notice(bot: Bot, feature: str) -> list[str] | None:
    """发送「功能未开启」提示卡片；浏览器不可用或渲染失败时降级为文字。"""
    info = _FEATURES[feature]
    try:
        image = await _render_feature_card(feature)
    except Exception as exc:
        logger.warning(f'{LOG_PREFIX} 未开启提示卡片渲染失败，降级为文字提示: {exc}')
        return await _safe_send(bot, _fallback_text(info))
    return await _safe_send(bot, MessageSegment.image(image))
