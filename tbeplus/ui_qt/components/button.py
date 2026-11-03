"""按钮：QPainter 自绘 + 悬停过渡，替代 QSS 无法做过渡的原生按钮。

状态完整：normal / hover / pressed / disabled / keyboard-focus。
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from .. import icons, paint, theme

_VARIANTS = {"primary", "secondary", "ghost", "danger", "link"}


class Button(QPushButton):
    def __init__(self, text: str = "", *, variant: str = "secondary",
                 icon: str | None = None, icon_color: str | None = None,
                 height: int = theme.CTRL_H, pad: int = 14, gap: int = 7,
                 radius: int = theme.RADIUS_CTRL, size: int = 13,
                 weight: int = 500, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._variant = variant if variant in _VARIANTS else "secondary"
        self._icon = icon
        self._icon_color = icon_color
        self._pad = pad
        self._gap = gap
        self._radius = radius
        self.setFont(theme.f(size, weight))
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._hover = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    # ------------------------------------------------------------------ 外部
    def set_text(self, text: str, icon: str | None = None) -> None:
        self.setText(text)
        self._icon = icon
        self.updateGeometry()
        self.update()

    def set_variant(self, variant: str) -> None:
        self._variant = variant if variant in _VARIANTS else "secondary"
        self.update()

    @property
    def text_width(self) -> int:
        return QFontMetrics(self.font()).horizontalAdvance(self.text())

    # ------------------------------------------------------------------ 动画
    def _on_anim(self, value) -> None:
        self._hover = float(value)
        self.update()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, e) -> None:  # noqa: N802
        if self.isEnabled():
            self._animate(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._animate(0.0)
        super().leaveEvent(e)

    # ------------------------------------------------------------------ 尺寸
    def sizeHint(self) -> QSize:  # noqa: N802
        w = self.text_width + self._pad * 2
        if self._icon:
            w += theme.ICON + self._gap
        return QSize(w, self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(self.rect())
        enabled = self.isEnabled()
        down = self.isDown() and enabled
        h = self._hover

        fill, border, ink = self._colors(enabled, h, down)
        if fill is not None:
            paint.surface(p, rect, radius=self._radius, fill=fill,
                          highlight=18 if self._variant == "primary" else 0)
        if border is not None:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(border, 1.0))
            p.drawPath(paint.rrect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                                   max(0.0, self._radius - 0.5)))

        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            ring = QColor(theme.C["accent"])
            ring.setAlpha(70)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 1.0))
            p.drawPath(paint.rrect(rect.adjusted(0.5, 0.5, -0.5, -0.5),
                                   max(0.0, self._radius - 0.5)))

        text = self.text()
        if not text and not self._icon:
            return
        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(text)
        total = tw + ((theme.ICON + self._gap) if self._icon else 0)
        x = (rect.width() - total) / 2
        if self._icon:
            paint.paint_icon(p, self._icon,
                             QRectF(x, (rect.height() - theme.ICON) / 2,
                                    theme.ICON, theme.ICON),
                             theme.C[self._icon_color] if self._icon_color else ink)
            x += theme.ICON + self._gap
        p.setFont(self.font())
        p.setPen(QColor(ink))
        p.drawText(QRectF(x, 0, rect.width() - x, rect.height()),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   text)

    def _colors(self, enabled: bool, h: float, down: bool):
        v = self._variant
        if not enabled:
            if v == "primary":
                return (theme.C["accent_subtle"], None, theme.C["text_disabled"])
            if v == "link":
                return (None, None, theme.C["text_disabled"])
            return (None, QColor(theme.C["border"]), theme.C["text_disabled"])

        light = QColor(theme.C["accent_text"])
        if v == "primary":
            base = theme.C["accent_pressed"] if down else theme.mix("accent", "accent_hover", h).name()
            return (base, None, theme.C["on_accent"])
        if v == "secondary":
            base = theme.mix("bg_surface", "bg_hover_strong", 0.55 * h if not down else 1.0)
            border = QColor(theme.C["border_strong"])
            border.setAlpha(200 + int(55 * h))
            return (base, border, theme.C["text"])
        if v == "danger":
            base = QColor(theme.C["danger_subtle"])
            base.setAlpha(int(255 * h) if not down else 255)
            border = QColor(theme.C["danger"])
            border.setAlpha(90 + int(70 * h))
            return (base, border, theme.C["danger"])
        if v == "link":
            return (None, None, theme.C["accent_hover"] if h > 0.5 else theme.C["accent_text"])
        # ghost
        base = QColor(theme.C["bg_hover_strong"])
        base.setAlpha(255 if down else int(230 * h))
        return (base, None, theme.C["text"] if h > 0.3 else theme.C["text2"])


class IconButton(QPushButton):
    """方形图标按钮（顶栏、卡头等）。"""

    def __init__(self, name: str, *, size: int = 30, icon_size: int = theme.ICON,
                 variant: str = "ghost", tooltip: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._icon_size = icon_size
        self._hover = 0.0
        self._variant = variant
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if tooltip:
            self.setToolTip(tooltip)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v) -> None:
        self._hover = float(v)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(1.0)
        self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(0.0)
        self._anim.start()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(self.rect())
        if self._variant == "ghost" and self._hover > 0:
            c = QColor(theme.C["bg_hover_strong"])
            c.setAlpha(int(230 * self._hover))
            paint.surface(p, rect, radius=theme.RADIUS_CTRL, fill=c)
        color = (theme.C["text"] if self.isEnabled() and self._hover > 0.35
                 else theme.C["text2"] if self.isEnabled() else theme.C["text_disabled"])
        side = self._icon_size
        paint.paint_icon(p, self._name,
                         QRectF((rect.width() - side) / 2,
                                (rect.height() - side) / 2, side, side), color)


class WindowButton(QPushButton):
    """标题栏窗口控制按钮（最小化/最大化/关闭）。"""

    def __init__(self, name: str, kind: str = "normal",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._kind = kind
        self._hover = False
        self.setFixedSize(44, 30)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_icon(self, name: str) -> None:
        self._name = name
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(self.rect())
        if self._hover:
            if self._kind == "close":
                paint.surface(p, rect.adjusted(2, 2, -2, -2), radius=5,
                              fill=theme.C["danger"])
            else:
                paint.surface(p, rect.adjusted(2, 2, -2, -2), radius=5,
                              fill=QColor(255, 255, 255, 18))
        color = "#FFFFFF" if (self._hover and self._kind == "close") else theme.C["text2"]
        side = 14.0
        paint.paint_icon(p, self._name,
                         QRectF((rect.width() - side) / 2 + self.devicePixelRatioF() * 0,
                                (rect.height() - side) / 2, side, side), color)
