# -*- coding: utf-8 -*-
"""生成客户端图标 assets/app.ico / app.png —— 点赞（thumbs up）标志。

图案取自 Material Design 的 thumb_up 路径（24x24），用三次贝塞尔展平后
按 256 网格重绘，再降采样生成各尺寸，边缘平滑、16px 也认得出。
红底白手，和另外两个工具（蓝白 / 深板岩金）在任务栏上一眼可分。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "assets")
ICO_PATH = os.path.join(OUT, "app.ico")
PNG_PATH = os.path.join(OUT, "app.png")
PREVIEW_PATH = os.path.join(OUT, "icon_preview.png")

N = 256
TILE = (226, 75, 74, 255)      # #E24B4A 点赞红
GLYPH = (255, 255, 255, 255)   # 白色手势

SCALE = 6.8                    # 24 网格 -> 256
OX, OY = 46.4, 54.0            # 让图案在底板上居中

# 袖口小方块（Material 路径里独立的一块）
CUFF = (1, 9, 5, 21)

SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (40, 40),
         (32, 32), (24, 24), (20, 20), (16, 16)]


def cubic(p0, c1, c2, p1, n: int = 24):
    """三次贝塞尔展平成折线（不含起点）。"""
    out = []
    for i in range(1, n + 1):
        t = i / n
        m = 1 - t
        out.append((
            m ** 3 * p0[0] + 3 * m * m * t * c1[0] + 3 * m * t * t * c2[0] + t ** 3 * p1[0],
            m ** 3 * p0[1] + 3 * m * m * t * c1[1] + 3 * m * t * t * c2[1] + t ** 3 * p1[1],
        ))
    return out


def hand_outline():
    """Material thumb_up 的轮廓点（24 网格）。"""
    p = [(23.0, 12.0)]
    p += cubic((23, 12), (23, 10.9), (22.1, 10), (21, 10))
    p += [(14.69, 10.0)]
    p += [(15.64, 5.43), (15.67, 5.11)]
    p += cubic((15.67, 5.11), (15.67, 4.70), (15.50, 4.32), (15.23, 4.05))
    p += [(14.17, 1.0), (7.59, 7.59)]
    p += cubic((7.59, 7.59), (7.22, 7.95), (7, 8.45), (7, 9))
    p += [(7, 19)]
    p += cubic((7, 19), (7, 20.1), (7.9, 21), (9, 21))
    p += [(18, 21)]
    p += cubic((18, 21), (18.83, 21), (19.54, 20.5), (19.84, 19.78))
    p += [(22.86, 12.73)]
    p += cubic((22.86, 12.73), (22.95, 12.50), (23, 12.26), (23, 12))
    return p


def draw_icon(supersample: int = 4) -> Image.Image:
    s = supersample
    size = N * s
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 底板
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=56 * s, fill=TILE)

    def pt(x, y):
        return ((OX + SCALE * x) * s, (OY + SCALE * y) * s)

    # 袖口
    x0, y0 = pt(CUFF[0], CUFF[1])
    x1, y1 = pt(CUFF[2], CUFF[3])
    d.rounded_rectangle([x0, y0, x1, y1], radius=9 * s, fill=GLYPH)

    # 手掌 + 拇指
    d.polygon([pt(x, y) for x, y in hand_outline()], fill=GLYPH)
    return img


def build() -> str:
    os.makedirs(OUT, exist_ok=True)
    big = draw_icon(4).resize((256, 256), Image.LANCZOS)
    big.save(ICO_PATH, format="ICO", sizes=SIZES)
    big.save(PNG_PATH)

    preview = Image.new("RGBA", (256 + 48 + 32 + 16 + 4 * 12, 256), (255, 255, 255, 255))
    x = 0
    for w in (256, 48, 32, 16):
        thumb = draw_icon(4).resize((w, w), Image.LANCZOS)
        preview.paste(thumb, (x, (256 - w) // 2), thumb)
        x += w + 12
    preview.save(PREVIEW_PATH)
    return ICO_PATH


if __name__ == "__main__":
    print("图标已生成：", build())
    print("PNG 预览：", PNG_PATH)
