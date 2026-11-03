"""筛选胶囊组：日志级别筛选（全部 / 成功 / 关键 / 警告 / 错误）。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QWidget

from .. import paint, theme


class _Chip(QWidget):
    clicked = Signal(str)

    def __init__(self, key: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._text = text
        self._active = False
        self._hover = 0.0
        self._font = theme.f(12, 500)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(QFontMetrics(self._font).horizontalAdvance(text) + 20)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v) -> None:
        self._hover = float(v)
        self.update()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
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

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(0, 0, self.width(), self.height())
        if self._active:
            paint.surface(p, rect, radius=13, fill="bg_active", highlight=14)
        elif self._hover > 0:
            c = QColor(theme.C["bg_hover"])
            c.setAlpha(int(255 * self._hover))
            paint.surface(p, rect, radius=13, fill=c)
        ink = "text" if self._active else ("text2" if self._hover > 0.4 else "text3")
        paint.text(p, rect, self._text, self._font, ink,
                   Qt.AlignmentFlag.AlignCenter)


class FilterChips(QWidget):
    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._chips: dict[str, _Chip] = {}
        self._current = items[0][0] if items else ""
        for key, text in items:
            chip = _Chip(key, text)
            chip.clicked.connect(self.choose)
            lay.addWidget(chip)
            self._chips[key] = chip
        self._apply()

    def choose(self, key: str, *, notify: bool = True) -> None:
        if key not in self._chips:
            return
        self._current = key
        self._apply()
        if notify:
            self.changed.emit(key)

    def current(self) -> str:
        return self._current

    def _apply(self) -> None:
        for key, chip in self._chips.items():
            chip.set_active(key == self._current)
