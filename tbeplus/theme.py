"""设计令牌：颜色、字体、间距、圆角。

原则：整个界面只从这里取值。
- 颜色服务于信息识别，不为好看堆渐变色块；
- 深色只出现在左侧导航，内容区一律浅底白卡，保证长时间阅读不累眼；
- 字号按"页标题 > 模块标题 > 正文 > 辅助"四档，不再临时定字号。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 配色
C = {
    # 底色：页面浅灰（设计稿 #F5F7FB），卡片纯白
    "bg": "#F5F7FB",
    "surface": "#FFFFFF",
    "surface_alt": "#F8FAFC",     # 表头、代码块这类次级面
    "surface_hi": "#F1F5F9",      # 输入框、次级按钮
    "surface_hover": "#E8EEF6",
    "overlay": "#00000000",

    # 线条
    "stroke": "#E2E8F0",
    "stroke_hi": "#CBD5E1",

    # 文字
    "text": "#1F2937",
    "text2": "#64748B",
    "text3": "#94A3B8",

    # 主操作色（设计稿：品牌主色 #2864F0）
    "accent": "#2864F0",
    "accent_hi": "#1B4FD0",
    "accent_soft": "#EAF1FE",
    "accent_line": "#C3D6FB",

    # 语义色
    "ok": "#16A34A",
    "ok_soft": "#ECFDF3",
    "ok_line": "#BBF7D0",
    "warn": "#D97706",
    "warn_soft": "#FFF7ED",
    "warn_line": "#FED7AA",
    "err": "#DC2626",
    "err_soft": "#FEF2F2",
    "err_line": "#FECACA",

    # 左侧深色导航（设计稿：深海军蓝 #14243A）
    "nav": "#14243A",
    "nav_active": "#1E3048",
    "nav_hover": "#1B2C42",
    "nav_text": "#E2E8F0",
    "nav_muted": "#94A3B8",
    "nav_line": "#24384F",
    "nav_accent": "#60A5FA",
    "nav_sel": "#2864F0",          # 选中项：品牌蓝底（设计稿蓝色选中态）
    "nav_sel_hover": "#3D75F2",    # 选中项悬停：蓝底微亮

    # 深色导航上的文字/图标
    "on_dark": "#FFFFFF",         # 导航上的主文字
    "nav_group": "#6B7F97",       # 导航分组标题
    "nav_icon": "#7A8CA3",        # 未选中项的图标

    # 中性色阶（指标卡、未选中胶囊、日志正文）
    "slate": "#475569",
    "ink": "#0F172A",             # 按钮按下时的混合基色

    # 语义色的深一档（胶囊文字，保证浅底上的对比度）
    "ok_deep": "#15803D",
    "warn_deep": "#B45309",
    "err_deep": "#B91C1C",

    # 日志控制台：浅灰底，靠文字颜色区分级别
    "term": "#F8FAFC",
    "log_text": "#334155",
    "like": "#2864F0",        # "新点赞"用主色，语义上不是警报
    "like_hi": "#1B4FD0",
    "like_soft": "#EAF1FE",
}

# ---------------------------------------------------------------- 字体
UI = "Microsoft YaHei UI"
MONO = "Consolas"
ICON = "Segoe MDL2 Assets"


def f(size: int, bold: bool = False, family: str = UI) -> tuple:
    """tkinter 字体元组。正数 = 磅，跟随系统 DPI 缩放。

    常用档位（96dpi 下约等于）：
        20pt ≈ 27px  页面主标题
        14pt ≈ 19px  模块标题
        12pt ≈ 16px  卡片标题
        10pt ≈ 13px  正文
         9pt ≈ 12px  辅助信息
    """
    return (family, size, "bold") if bold else (family, size)


def icon(size: int = 10, bold: bool = False) -> tuple:
    return (ICON, size, "bold") if bold else (ICON, size)


# ---------------------------------------------------------------- 间距 / 圆角
GAP_XS = 4
GAP_S = 8
GAP_M = 12
GAP = 16
GAP_L = 24
GAP_XL = 32

RADIUS_CARD = 10
RADIUS_CTRL = 8
RADIUS_CHIP = 14

SIDEBAR_W = 224
NAV_W = 192
HEADER_H = 80
