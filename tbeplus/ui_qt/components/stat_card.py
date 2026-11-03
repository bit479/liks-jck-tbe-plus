"""统计卡：数字为视觉中心，无重边框；无数据时显示 0（不使用横线占位）。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from .. import paint, theme


class StatCard(QWidget):
    def __init__(self, label: str, *, tone: str = "text3",
                 note: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = label
        self._tone = tone
        self._note = note
        self._value: int | None = None
        self.setMinimumHeight(100)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(),
                           self.sizePolicy().verticalPolicy())

    def set_value(self, value: int | None, note: str | None = None) -> None:
        self._value = value
        if note is not None:
            self._note = note
        self.update()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(168, 100)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(132, 100)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        w, h = self.width(), self.height()
        rect = QRectF(0.5, 0.5, w - 1, h - 1)
        paint.surface(p, rect, radius=theme.RADIUS_CARD, fill="bg_surface",
                      border="border", highlight=10)

        paint.dot(p, 17, 21, 3.0, self._tone)
        paint.text(p, QRectF(26, 12, w - 42, 18), self._label,
                   theme.f(12, 500), "text2")

        value = self._value or 0
        size = 32 if w >= 140 else 26
        num_font = theme.f(size, 600, tabular=True)
        ink = self._tone if (value and self._tone != "text3") else theme.C["text"]
        p.setFont(num_font)
        p.setPen(paint.qcolor(ink))
        p.drawText(QRectF(15, 28, w - 28, h - 54),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   f"{value:,}")

        if self._note:
            note_font = theme.f(11)
            text = paint.elide(self._note, note_font, w - 32)
            paint.text(p, QRectF(16, h - 27, w - 32, 16), text, note_font, "text3")
