"""矢量图标：把 resources/icons/*.svg 直接渲染到 QPainter。

不用位图、不用 Emoji：
- 图标是 SVG 路径，随 DPI 缩放始终清晰；
- 颜色在渲染前替换 ``currentColor``，同一份资源可用于不同语义色；
- 只做 ``QSvgRenderer.render(painter, rect)``，不缓存像素尺寸，
  因此 125% / 150% / 200% 下都是矢量级锐利。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_ICON_DIR = Path(__file__).with_name("resources") / "icons"


@lru_cache(maxsize=256)
def _renderer(name: str, color: str) -> QSvgRenderer:
    path = _ICON_DIR / f"{name}.svg"
    if not path.exists():
        raise FileNotFoundError(f"缺少图标资源: {path}")
    text = path.read_text(encoding="utf-8").replace("currentColor", color)
    return QSvgRenderer(QByteArray(text.encode("utf-8")))


def paint_icon(p: QPainter, name: str, rect: QRectF, color: str | QColor,
               opacity: float = 1.0) -> None:
    """把图标画进 rect（保持正方形居中，不拉伸）。"""
    hex_color = color.name() if isinstance(color, QColor) else color
    side = min(rect.width(), rect.height())
    x = rect.x() + (rect.width() - side) / 2
    y = rect.y() + (rect.height() - side) / 2
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    p.setOpacity(p.opacity() * max(0.0, min(1.0, opacity)))
    _renderer(name, hex_color).render(p, QRectF(x, y, side, side))
    p.restore()


def pixmap(name: str, size: int, color: str, dpr: float = 1.0) -> QPixmap:
    """渲染为位图（仅用于 QIcon、窗口图标等必须位图的场合）。"""
    px = max(1, round(size * dpr))
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _renderer(name, color).render(p, QRectF(0, 0, px, px))
    p.end()
    return pm


def icon(name: str, color: str, size: int = 16) -> QIcon:
    """多倍图 QIcon，交给 Qt 按当前屏幕 DPR 选择。"""
    ic = QIcon()
    for dpr in (1.0, 1.25, 1.5, 2.0, 3.0):
        ic.addPixmap(pixmap(name, size, color, dpr))
    return ic


def icon_size(size: int) -> QSize:
    return QSize(size, size)
