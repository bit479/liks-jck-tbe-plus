"""绘制基础设施：抗锯齿、圆角路径、面板/按钮表面。

所有自绘控件都经由这里，保证：
- 统一开启 Antialiasing / TextAntialiasing；
- 圆角用 QPainterPath，而不是多边形逼近；
- 边框落在半像素上，1px 线在任意 DPR 下都干净。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen

from . import theme


def qcolor(v: str | QColor) -> QColor:
    """令牌名 / #rrggbb / QColor 统一转 QColor。"""
    if isinstance(v, QColor):
        return QColor(v)
    return QColor(theme.C[v] if v in theme.C else v)


def quality(p: QPainter) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)


def rrect(rect: QRectF, radius: float) -> QPainterPath:
    """圆角矩形路径；半径自动收敛到短边一半以内。"""
    r = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    path = QPainterPath()
    path.addRoundedRect(rect, r, r)
    return path


def surface(p: QPainter, rect: QRectF, *, radius: float,
            fill: str | QColor | None, border: str | QColor | None = None,
            border_alpha: int = 255, highlight: int = 0) -> None:
    """画一块圆角表面。

    ``highlight`` > 0 时，在顶部内侧叠一条极淡的高光，用来区分层级，
    替代廉价的投影/渐变。
    """
    path = rrect(rect, radius)
    if fill is not None:
        p.fillPath(path, qcolor(fill))
    if border is not None:
        color = qcolor(border)
        color.setAlpha(border_alpha)
        pen = QPen(color, 1.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rrect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                         max(0.0, radius - 0.5)))
    if highlight > 0:
        p.save()
        p.setClipPath(path)
        p.setPen(QPen(QColor(255, 255, 255, highlight), 1.0))
        p.drawLine(QPointF(rect.left() + radius, rect.top() + 0.5),
                   QPointF(rect.right() - radius, rect.top() + 0.5))
        p.restore()


def dot(p: QPainter, cx: float, cy: float, r: float, color: str | QColor,
        alpha: int = 255) -> None:
    c = qcolor(color)
    c.setAlpha(alpha)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(cx, cy), r, r)


def text(p: QPainter, rect: QRectF, s: str, font, color: str | QColor,
         align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft
         | Qt.AlignmentFlag.AlignVCenter) -> None:
    p.save()
    p.setFont(font)
    p.setPen(qcolor(color))
    p.drawText(rect, int(align), s)
    p.restore()


def elide(text: str, font, width: float) -> str:
    return QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight,
                                         int(max(0, width)))


def paint_icon(p: QPainter, name: str, rect: QRectF,
               color: str | QColor, opacity: float = 1.0) -> None:
    """转发到矢量图标渲染（延迟导入，避免模块循环依赖）。"""
    from .icons import paint_icon as _paint_svg

    _paint_svg(p, name, rect, color if isinstance(color, str) else color.name(),
               opacity)
