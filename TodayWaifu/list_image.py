"""老婆列表图：条目多时渲染成图片，避免一大串文字刷屏。

只在条目超过阈值时启用；条目少时仍发纯文本，保留可复制、可搜索的好处。
视觉上沿用插件内排行榜卡片的规范：背景图压暗 + 半透明白卡片 + 粉紫渐变点缀。
条目按从上到下的单列竖排，读起来与聊天里的列表一致。
"""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from gsuid_core.data_store import get_res_path
from gsuid_core.utils.fonts.fonts import core_font

# 按 2 倍尺寸绘制再缩回：文字与圆角边缘都靠超采样取平滑，直接按目标尺寸画会发毛
_SCALE = 2

_MARGIN = 26
_CARD_RADIUS = 26
_TITLE_SIZE = 32
_TITLE_SUB_SIZE = 18
_ROW_SIZE = 24
_INDEX_SIZE = 19
_COLUMN_GAP = 30
_INDEX_DIAMETER = 32
_INDEX_TEXT_GAP = 13
_ARROW_GAP = 11
_NAME_ROLE_GAP = 22
_MIN_CARD_WIDTH = 520

# 条目越多行距越紧，避免上百人时图片长得离谱
_ROW_HEIGHT_ROOMY = 56
_ROW_HEIGHT_TIGHT = 46
_TIGHT_FROM = 40

# 与排行榜卡片同源的渐变：粉红 → 紫
_GRADIENT_START = (255, 0, 106)
_GRADIENT_END = (106, 92, 255)

_CARD_BG = (255, 255, 255, 112)
_CARD_BORDER = (255, 255, 255, 168)
# 卡片是半透明的，浅色背景会吃掉深色小字，故文字一律带一层白色描边
_TEXT_STROKE = (255, 255, 255, 205)
_STROKE_WIDTH = 3
_TITLE_SHADOW = (28, 32, 42, 110)
_SUB_FG = (108, 116, 132, 255)
_NAME_FG = (44, 50, 62, 255)
_ROLE_FG = (58, 92, 205, 255)
_ARROW_FG = (150, 158, 175, 235)
_INDEX_FG = (255, 255, 255, 255)
_ROW_DIVIDER = (60, 70, 90, 38)
_FALLBACK_TOP = (236, 240, 252)
_FALLBACK_BOTTOM = (214, 222, 246)

_IMAGE_SUFFIXES = ('.jpg', '.jpeg', '.png', '.webp')


def _role_pile_root() -> Path | None:
    root = get_res_path('XutheringWavesUID') / 'custom_role_pile'
    return root if root.is_dir() else None


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
        Image.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _background(width: int, height: int) -> Image.Image:
    """背景优先用鸣潮面板图，取不到时退回淡紫渐变底。"""
    root = _role_pile_root()
    if root is not None:
        dirs = [entry for entry in root.iterdir() if entry.is_dir()]
        for _ in range(4):
            if not dirs:
                break
            chosen = random.choice(dirs)
            dirs.remove(chosen)
            images = [p for p in chosen.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES]
            if not images:
                continue
            try:
                with Image.open(random.choice(images)) as raw:
                    return _cover_crop(raw.convert('RGBA'), width, height)
            except OSError:
                continue

    strip = Image.new('RGBA', (1, height))
    pixels = strip.load()
    if pixels is not None:
        for y in range(height):
            ratio = y / max(1, height - 1)
            pixels[0, y] = (
                int(_FALLBACK_TOP[0] + (_FALLBACK_BOTTOM[0] - _FALLBACK_TOP[0]) * ratio),
                int(_FALLBACK_TOP[1] + (_FALLBACK_BOTTOM[1] - _FALLBACK_TOP[1]) * ratio),
                int(_FALLBACK_TOP[2] + (_FALLBACK_BOTTOM[2] - _FALLBACK_TOP[2]) * ratio),
                255,
            )
    return strip.resize((width, height), Image.NEAREST)


def _darken(width: int, height: int) -> Image.Image:
    """自上而下加深的压暗层，让白卡片与浅色文字都压得住。"""
    strip = Image.new('RGBA', (1, height))
    pixels = strip.load()
    if pixels is not None:
        for y in range(height):
            ratio = y / max(1, height - 1)
            pixels[0, y] = (14, 18, 28, int(96 + 76 * ratio))
    return strip.resize((width, height), Image.NEAREST)


def _gradient_image(size: tuple[int, int], start: tuple[int, int, int], end: tuple[int, int, int]) -> Image.Image:
    """生成水平渐变色块，供文字与徽章取色用。"""
    width, height = size
    strip = Image.new('RGBA', (width, 1))
    pixels = strip.load()
    if pixels is not None:
        for x in range(width):
            ratio = x / max(1, width - 1)
            pixels[x, 0] = (
                int(start[0] + (end[0] - start[0]) * ratio),
                int(start[1] + (end[1] - start[1]) * ratio),
                int(start[2] + (end[2] - start[2]) * ratio),
                255,
            )
    return strip.resize((width, height), Image.NEAREST)


def _gradient_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    start: tuple[int, int, int],
    end: tuple[int, int, int],
) -> Image.Image:
    """水平渐变色文字，用作标题。"""
    probe = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    bbox = probe.textbbox((0, 0), text, font=font)
    width = max(1, int(bbox[2] - bbox[0]) + 12)
    height = max(1, int(bbox[3] - bbox[1]) + 12)
    mask = Image.new('L', (width, height), 0)
    ImageDraw.Draw(mask).text((-bbox[0] + 6, -bbox[1] + 6), text, font=font, fill=255)
    result = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    result.paste(_gradient_image((width, height), start, end), (0, 0), mask)
    return result


def _gradient_badge(diameter: int) -> Image.Image:
    """渐变圆形徽章底，用作序号。"""
    badge = _gradient_image((diameter, diameter), _GRADIENT_START, _GRADIENT_END)
    mask = Image.new('L', (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=255)
    out = Image.new('RGBA', (diameter, diameter), (0, 0, 0, 0))
    out.paste(badge, (0, 0), mask)
    return out


def render_wife_list_image(title_text: str, items: list[tuple[int, str, str]]) -> bytes:
    """把老婆列表渲染成 PNG 字节；items 为空时只画标题栏。"""
    s = _SCALE
    title_font = core_font(_TITLE_SIZE * s)
    sub_font = core_font(_TITLE_SUB_SIZE * s, weight=400)
    name_font = core_font(_ROW_SIZE * s)
    role_font = core_font(_ROW_SIZE * s, weight=630)
    index_font = core_font(_INDEX_SIZE * s, weight=630)

    probe = ImageDraw.Draw(Image.new('RGB', (8, 8)))

    def width_of(text: str, font: object) -> int:
        return int(probe.textlength(text, font=font))

    row_height = (_ROW_HEIGHT_TIGHT if len(items) >= _TIGHT_FROM else _ROW_HEIGHT_ROOMY) * s
    index_d = _INDEX_DIAMETER * s
    arrow_w = width_of('→', name_font)

    # 单列竖排：序号自上而下递增，与聊天里的列表读法一致
    widest = 0
    for _, display_name, wife_name in items:
        row_w = index_d + width_of(display_name, name_font) + arrow_w + width_of(wife_name, role_font)
        widest = max(widest, row_w)
    row_w = widest + (_INDEX_TEXT_GAP + _NAME_ROLE_GAP + _ARROW_GAP) * s + 20 * s

    sub_text = f'共 {len(items)} 位' if items else '暂无记录'
    title_image = _gradient_text(title_text, title_font, _GRADIENT_START, _GRADIENT_END)

    card_w = max(_MIN_CARD_WIDTH * s, row_w, title_image.width + 60 * s) + 34 * s * 2
    card_h = 22 * s + _TITLE_SIZE * s + 22 * s + (len(items) * row_height + 16 * s if items else 0)
    width = card_w + _MARGIN * s * 2
    height = card_h + _MARGIN * s * 2

    canvas = _background(width, height)
    canvas = Image.alpha_composite(canvas, _darken(width, height))

    card = Image.new('RGBA', (card_w, card_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        (0, 0, card_w - 1, card_h - 1),
        radius=_CARD_RADIUS * s,
        fill=_CARD_BG,
        outline=_CARD_BORDER,
        width=max(1, s * 2),
    )

    left = 34 * s
    top = 22 * s
    title_x = max(left, (card_w - title_image.width) // 2)
    draw.text((title_x + 2 * s, top + 2 * s), title_text, font=title_font, fill=_TITLE_SHADOW)
    card.paste(title_image, (title_x, top), title_image)
    draw.text(
        (card_w - left - width_of(sub_text, sub_font), top + 10 * s),
        sub_text,
        font=sub_font,
        fill=_SUB_FG,
        stroke_width=_STROKE_WIDTH,
        stroke_fill=_TEXT_STROKE,
    )

    if items:
        badge = _gradient_badge(index_d)
        body_top = top + _TITLE_SIZE * s + 22 * s
        # items 的首元素是排序用的时间戳，序号必须按显示顺序重新编号
        for position, (_, display_name, wife_name) in enumerate(items, 1):
            row = position - 1
            center_y = body_top + row * row_height + row_height // 2
            if row:
                divider_y = center_y - row_height // 2
                draw.line((left, divider_y, card_w - left, divider_y), fill=_ROW_DIVIDER, width=max(1, s))

            badge_top = center_y - index_d // 2
            card.paste(badge, (left, badge_top), badge)
            draw.text(
                (left + index_d // 2, center_y),
                str(position),
                font=index_font,
                fill=_INDEX_FG,
                anchor='mm',
            )

            cursor = left + index_d + _INDEX_TEXT_GAP * s
            draw.text(
                (cursor, center_y),
                display_name,
                font=name_font,
                fill=_NAME_FG,
                anchor='lm',
                stroke_width=_STROKE_WIDTH,
                stroke_fill=_TEXT_STROKE,
            )
            cursor += width_of(display_name, name_font) + _NAME_ROLE_GAP * s
            draw.text(
                (cursor, center_y),
                '→',
                font=name_font,
                fill=_ARROW_FG,
                anchor='lm',
                stroke_width=_STROKE_WIDTH,
                stroke_fill=_TEXT_STROKE,
            )
            cursor += arrow_w + _ARROW_GAP * s
            draw.text(
                (cursor, center_y),
                wife_name,
                font=role_font,
                fill=_ROLE_FG,
                anchor='lm',
                stroke_width=_STROKE_WIDTH,
                stroke_fill=_TEXT_STROKE,
            )

    canvas.alpha_composite(card, (_MARGIN * s, _MARGIN * s))
    buffer = io.BytesIO()
    # 背景是照片，PNG 无损会到近 MB；JPEG 体积小得多且此处不含透明区域
    canvas.convert('RGB').resize((width // s, height // s), Image.LANCZOS).save(
        buffer, format='JPEG', quality=88, optimize=True
    )
    return buffer.getvalue()
