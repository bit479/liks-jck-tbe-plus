"""未迁移页面的占位：明确说明状态，不摆假数据、不做假交互。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import paint, theme


class PlaceholderPage(QWidget):
    def __init__(self, title: str, hint: str, icon: str = "nav-dashboard",
                 stage: str = "待迁移", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._hint = hint
        self._icon = icon
        self._stage = stage

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        cy = self.height() / 2 - 40
        paint.paint_icon(p, self._icon, QRectF(self.width() / 2 - 13, cy, 26, 26),
                         theme.C["text_disabled"])

        p.setFont(theme.f(15, 600))
        p.setPen(paint.qcolor("text2"))
        p.drawText(QRectF(0, cy + 40, self.width(), 22),
                   int(Qt.AlignmentFlag.AlignCenter), self._title)

        p.setFont(theme.f(12))
        p.setPen(paint.qcolor("text3"))
        p.drawText(QRectF(0, cy + 66, self.width(), 40),
                   int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                   self._hint)

        tag_font = theme.f(11)
        tw = p.fontMetrics().horizontalAdvance(self._stage) + 20
        p.setFont(tag_font)
        tag = QRectF(self.width() / 2 - tw / 2, cy + 112, tw, 22)
        paint.surface(p, tag, radius=11, fill="bg_surface", border="border")
        paint.text(p, tag, self._stage, tag_font, "text3",
                   Qt.AlignmentFlag.AlignCenter)
