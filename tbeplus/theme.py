"""设计令牌：颜色、字体、间距、圆角。

Linear-inspired 深色工作台：
- 页面与面板用相邻灰阶 + 低对比边框分层，不靠阴影和彩色块；
- 强调色低饱和，只给主操作、当前选中和焦点；
- 字号按"页标题 > 模块标题 > 正文 > 辅助"分档，不再临时定字号。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 配色
C = {
    # 底色
    "bg": "#111318",             # 应用主背景
    "surface": "#181B22",        # 面板 / 卡片
    "surface_alt": "#15171D",    # 次级面（表头、计划摘要）
    "surface_hi": "#232730",     # 输入框 / 次级按钮 / 弱填充
    "surface_hover": "#292E38",  # 悬停
    "overlay": "#00000000",

    # 线条（低对比）
    "stroke": "#23262E",
    "stroke_hi": "#333845",

    # 文字
    "text": "#E6E8ED",
    "text2": "#9299A8",
    "text3": "#6E7482",

    # 主强调色（低饱和紫）
    "accent": "#8B83F6",
    "accent_hi": "#A59EF8",
    "accent_soft": "#262244",
    "accent_line": "#3D3670",

    # 语义色
    "ok": "#5FC39A",
    "ok_soft": "#16281F",
    "ok_line": "#2A4A3A",
    "warn": "#D2A65C",
    "warn_soft": "#2A2317",
    "warn_line": "#4A3B22",
    "err": "#D97C84",
    "err_soft": "#2C1A1D",
    "err_line": "#4A2A2E",

    # 左侧导航
    "nav": "#101216",
    "nav_active": "#1E222A",
    "nav_hover": "#1A1D24",
    "nav_text": "#E6E8ED",
    "nav_muted": "#9299A8",
    "nav_line": "#23262E",
    "nav_accent": "#8B83F6",
    "nav_sel": "#1E222A",          # 选中：弱填充，不用整块高饱和
    "nav_sel_hover": "#232730",

    # 深色底上的文字 / 强调底上的文字
    "on_dark": "#FFFFFF",
    "on_accent": "#14151C",
    "nav_group": "#5E6472",
    "nav_icon": "#6E7482",

    # 中性色阶
    "slate": "#B0B6C3",
    "ink": "#05060A",

    # 语义色亮一档（深底上的胶囊文字）
    "ok_deep": "#8FE0BC",
    "warn_deep": "#E8C789",
    "err_deep": "#F0A6AC",

    # 日志控制台
    "term": "#15171D",
    "log_text": "#B9BFCC",
    "like": "#8B83F6",
    "like_hi": "#A59EF8",
    "like_soft": "#262244",
}

# ---------------------------------------------------------------- 字体
UI = "Microsoft YaHei UI"
MONO = "Consolas"
ICON = "Segoe MDL2 Assets"


def f(size: int, bold: bool = False, family: str = UI) -> tuple:
    """tkinter 字体元组。正数 = 磅，跟随系统 DPI 缩放。"""
    return (family, size, "bold") if bold else (family, size)


def icon(size: int = 10, bold: bool = False) -> tuple:
    return (ICON, size, "bold") if bold else (ICON, size)


# ---------------------------------------------------------------- 间距 / 圆角
GAP_XS = 4
GAP_S = 8
GAP_M = 12
GAP = 16
GAP_L = 20
GAP_XL = 28

RADIUS_WINDOW = 12
RADIUS_CARD = 10
RADIUS_CTRL = 6
RADIUS_CHIP = 999

SIDEBAR_W = 220
NAV_W = 192
HEADER_H = 68
