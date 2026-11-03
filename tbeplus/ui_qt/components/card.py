"""卡片：统一圆角、边框与内边距的容器（自绘，保证任意 DPR 下边缘干净）。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import paint, theme


class Card(QWidget):
    def __init__(self, *, fill: str = "bg_surface", border: str = "border",
                 radius: int = theme.RADIUS_CARD, padding: tuple[int, int, int, int] = (18, 16, 18, 16),
                 spacing: int = 12, highlight: int = 10,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fill = fill
        self._border = border
        self._radius = radius
        self._highlight = highlight
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*padding)
        self.body.setSpacing(spacing)

    def set_fill(self, fill: str) -> None:
        self._fill = fill
        self.update()

    def add_title(self, title: str, subtitle: str = "") -> QHBoxLayout:
        """加标题行，返回该行布局以便继续追加右侧控件。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lab = QLabel(title)
        lab.setFont(theme.f(13, 600))
        lab.setStyleSheet(f"color: {theme.C['text']}; background: transparent;")
        row.addWidget(lab)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setFont(theme.f(12))
            sub.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
            row.addWidget(sub)
        row.addStretch(1)
        self.body.addLayout(row)
        return row

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint.surface(p, rect, radius=self._radius, fill=self._fill,
                      border=self._border, highlight=self._highlight)
