"""新版 Qt 界面的设计令牌：颜色、字体、尺寸、QSS。

约束：
- 所有颜色只在本文件定义，控件与页面不得散落色值；
- 尺寸一律使用逻辑像素（Qt 高 DPI 下自动按缩放比例换算）；
- 字体通过 ``QFont.setFamilies`` 提供 Latin → 中文的回落链，
  避免 QSS 的 font-family 只能指定单一字体导致中文变成方框。
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtGui import QColor, QFont, QFontDatabase

# --------------------------------------------------------------------------- 颜色
C = {
    # 底层背景（由深到浅）
    "bg_app": "#0E1014",
    "bg_sidebar": "#0C0E12",
    "bg_canvas": "#111318",
    "bg_surface": "#181B22",
    "bg_elevated": "#1E222B",
    "bg_subtle": "#14171D",
    # 交互态
    "bg_hover": "#1F242D",
    "bg_hover_strong": "#292E38",
    "bg_active": "#2B313C",
    # 线条
    "border": "#232833",
    "border_strong": "#333A48",
    "border_soft": "#1B1F27",
    # 文字
    "text": "#E6E8ED",
    "text2": "#9299A8",
    "text3": "#6B7280",
    "text_disabled": "#4B5160",
    # 强调色（低饱和紫）
    "accent": "#8B83F6",
    "accent_hover": "#9C95F8",
    "accent_pressed": "#7C74E9",
    "accent_subtle": "#1E1B33",
    "accent_line": "#3B3470",
    "accent_text": "#B3ADF9",
    "on_accent": "#0F1117",
    # 语义色
    "ok": "#58B98C",
    "ok_subtle": "#152620",
    "ok_line": "#2C4E40",
    "warn": "#D8A85F",
    "warn_subtle": "#2A2317",
    "warn_line": "#4A3C24",
    "danger": "#E0736F",
    "danger_subtle": "#2C1A1D",
    "danger_line": "#4E2C30",
    "info": "#6E9FD8",
    "info_subtle": "#16222F",
}

# --------------------------------------------------------------------------- 尺寸
SIDEBAR_W = 220
TITLEBAR_H = 44
TOPBAR_H = 60
GAP_X = 20          # 主工作区左右内边距
GAP_Y = 16          # 模块之间的纵向间距
RADIUS_CARD = 10
RADIUS_CTRL = 7
CTRL_H = 32         # 标准控件高度
CTRL_H_SM = 28
ICON = 16

# --------------------------------------------------------------------------- 字体
_LATIN = ("Inter", "Segoe UI Variable Text", "Segoe UI")
_CN = ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei")
_MONO = ("JetBrains Mono", "Cascadia Mono", "Consolas")


@lru_cache(maxsize=1)
def families() -> tuple[str, str, str]:
    """返回 (界面字体回落链, 首选字体名, 等宽字体名)。"""
    db = set(QFontDatabase.families())
    chain = [n for n in _LATIN if n in db] + [n for n in _CN if n in db]
    if not chain:
        chain = ["Sans Serif"]
    mono = next((n for n in _MONO if n in db), "Consolas")
    return "|".join(chain), chain[0], mono


def base_font() -> QFont:
    """应用全局字体：13px，Latin 优先、中文回落。"""
    chain, _, _ = families()
    f = QFont()
    f.setFamilies(chain.split("|"))
    f.setPixelSize(13)
    f.setHintingPreference(QFont.HintingPreference.PreferDefaultHinting)
    return f


def f(size: int = 13, weight: int = 400, mono: bool = False,
      spacing: float = 0.0, tabular: bool = False) -> QFont:
    """按像素字号构造字体。``weight`` 用 400/500/600/700。

    ``tabular`` 打开 OpenType ``tnum``，让统计数字等宽对齐；
    字体不支持该特性时静默忽略，不影响布局。
    """
    chain, _, mono_name = families()
    font = QFont()
    font.setFamilies([mono_name] if mono else chain.split("|"))
    font.setPixelSize(size)
    font.setWeight(QFont.Weight(weight))
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    if tabular:
        try:
            font.setFeature(QFont.Tag("tnum"), 1)
        except Exception:  # noqa: BLE001 - 旧版本 Qt 无此 API
            pass
    return font


def color(key: str, alpha: int | None = None) -> QColor:
    c = QColor(C[key])
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def mix(a: str, b: str, t: float) -> QColor:
    """线性混合两个令牌色，用于 hover 过渡的中间帧。"""
    ca, cb = QColor(C.get(a, a)), QColor(C.get(b, b))
    t = max(0.0, min(1.0, t))
    return QColor(
        round(ca.red() + (cb.red() - ca.red()) * t),
        round(ca.green() + (cb.green() - ca.green()) * t),
        round(ca.blue() + (cb.blue() - ca.blue()) * t),
        round(ca.alpha() + (cb.alpha() - ca.alpha()) * t),
    )


# --------------------------------------------------------------------------- QSS
_QSS = """
QWidget { color: @text@; }
QMainWindow, #appRoot { background: @bg_app@; }
QStackedWidget#pageHost { background: @bg_canvas@; }
QToolTip {
    background: @bg_elevated@; color: @text@;
    border: 1px solid @border_strong@; border-radius: 6px; padding: 5px 8px;
}

/* ---------- 输入框 ---------- */
QLineEdit {
    background: @bg_subtle@; border: 1px solid @border@;
    border-radius: @r_ctrl@px; padding: 0 10px; min-height: @ctrl@px;
    color: @text@; selection-background-color: @accent@; selection-color: @on_accent@;
}
QLineEdit:hover { border-color: @border_strong@; }
QLineEdit:focus { border-color: @accent@; background: @bg_surface@; }
QLineEdit:disabled { color: @text_disabled@; background: @bg_sidebar@; }

QComboBox {
    background: @bg_subtle@; border: 1px solid @border@; border-radius: @r_ctrl@px;
    padding: 0 8px 0 10px; min-height: @ctrl@px; color: @text@;
}
QComboBox:hover { border-color: @border_strong@; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: @bg_elevated@; border: 1px solid @border_strong@;
    border-radius: 8px; padding: 4px; color: @text@; outline: none;
    selection-background-color: @bg_hover_strong@; selection-color: @text@;
}

QCheckBox { color: @text2@; spacing: 8px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid @border_strong@; background: @bg_subtle@;
}
QCheckBox::indicator:hover { border-color: @accent_line@; }
QCheckBox::indicator:checked { background: @accent@; border-color: @accent@; }

/* ---------- 滚动条 ---------- */
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px 2px 2px 0; }
QScrollBar::handle:vertical {
    background: @border_strong@; border-radius: 3px; min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: @text3@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0 2px 2px 2px; }
QScrollBar::handle:horizontal {
    background: @border_strong@; border-radius: 3px; min-width: 28px;
}
QScrollBar::handle:horizontal:hover { background: @text3@; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* ---------- 菜单 ---------- */
QMenu {
    background: @bg_elevated@; border: 1px solid @border_strong@;
    border-radius: 8px; padding: 5px; color: @text@;
}
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 5px; }
QMenu::item:selected { background: @bg_hover_strong@; }
QMenu::separator { height: 1px; background: @border@; margin: 5px 8px; }
"""


def build_qss() -> str:
    q = _QSS
    for key, val in C.items():
        q = q.replace(f"@{key}@", val)
    return (q.replace("@r_ctrl@", str(RADIUS_CTRL))
             .replace("@ctrl@", str(CTRL_H)))


def ink(key: str) -> str:
    """取色值字符串（供 QSS/富文本用）。"""
    return C[key]
