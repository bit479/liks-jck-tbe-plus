"""进度条：自绘圆角条，进度变化做 240ms 补间，避免跳动。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import paint, theme


class ProgressBar(QWidget):
    def __init__(self, *, height: int = 6, tone: str = "accent",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone = tone
        self._value = 0.0
        self._target = 0.0
        self._h = height
        self.setFixedHeight(height)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v) -> None:
        self._value = float(v)
        self.update()

    def set_progress(self, done: int, total: int) -> None:
        self.set_ratio(0.0 if not total else min(1.0, done / total))

    def set_ratio(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, ratio))
        if abs(ratio - self._target) < 1e-4:
            return
        self._target = ratio
        self._anim.stop()
        self._anim.setStartValue(self._value)
        self._anim.setEndValue(ratio)
        self._anim.start()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        w, h = self.width(), self._h
        r = h / 2
        track = QRectF(0, 0, w, h)
        paint.surface(p, track, radius=r, fill="border")
        if self._value > 0:
            fw = max(h, w * self._value)
            paint.surface(p, QRectF(0, 0, fw, h), radius=r, fill=self._tone)
