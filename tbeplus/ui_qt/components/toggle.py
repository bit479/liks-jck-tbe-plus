"""开关：日志“暂停滚动”、偏好设置等使用。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import paint, theme


class Toggle(QWidget):
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on = checked
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(34, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v) -> None:
        self._pos = float(v)
        self.update()

    def is_on(self) -> bool:
        return self._on

    def set_on(self, on: bool, *, notify: bool = False) -> None:
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        if notify:
            self.toggled.emit(on)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.set_on(not self._on, notify=True)
            e.accept()
            return
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.set_on(not self._on, notify=True)
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(0, 0, self.width(), self.height())
        off = theme.mix("bg_hover_strong", "accent", self._pos)
        paint.surface(p, rect, radius=9, fill=off,
                      border="border_strong" if self._pos < 0.5 else None,
                      border_alpha=200)
        cx = 9 + (self.width() - 18) * self._pos
        paint.dot(p, cx, 9, 6, "#FFFFFF" if self._pos > 0.5 else theme.C["text2"])
