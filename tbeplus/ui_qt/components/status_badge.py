"""状态胶囊：运行状态、连接状态共用。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from .. import paint, theme

_TONES = {
    "idle": ("text3", None, "text2"),
    "run": ("accent", "accent_subtle", "accent_text"),
    "ok": ("ok", "ok_subtle", "ok"),
    "warn": ("warn", "warn_subtle", "warn"),
    "danger": ("danger", "danger_subtle", "danger"),
}


class StatusBadge(QWidget):
    def __init__(self, text: str = "空闲", tone: str = "idle",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._tone = tone
        self._font = theme.f(12, 500)
        self.setFixedHeight(24)

    def set_state(self, text: str, tone: str) -> None:
        if (text, tone) == (self._text, self._tone):
            return
        self._text, self._tone = text, tone
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        w = QFontMetrics(self._font).horizontalAdvance(self._text) + 30
        return QSize(max(64, w), 24)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        dot_ink, bg, ink = _TONES.get(self._tone, _TONES["idle"])
        rect = QRectF(0, 0, self.width(), 24)
        if bg:
            paint.surface(p, rect, radius=12, fill=bg, border=_TONES[self._tone][0],
                          border_alpha=60)
        paint.dot(p, 12, 12, 3.2, dot_ink)
        paint.text(p, QRectF(21, 0, self.width() - 26, 24), self._text,
                   self._font, ink)
