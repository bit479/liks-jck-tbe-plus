"""顶部工具栏：页面标题、运行状态、计时与快捷入口。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import paint, theme
from .button import IconButton
from .status_badge import StatusBadge


class TopBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(theme.TOPBAR_H)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(theme.GAP_X, 0, theme.GAP_X, 0)
        lay.setSpacing(12)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.title = QLabel("任务概览")
        self.title.setFont(theme.f(17, 600))
        self.title.setStyleSheet(f"color: {theme.C['text']}; background: transparent;")
        self.sub = QLabel("")
        self.sub.setFont(theme.f(12))
        self.sub.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        col.addWidget(self.title)
        col.addWidget(self.sub)
        lay.addLayout(col)
        lay.addStretch(1)

        self.badge = StatusBadge("空闲", "idle")
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        sep = QWidget()
        sep.setFixedSize(1, 18)
        sep.setStyleSheet(f"background: {theme.C['border_strong']};")
        lay.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)

        self.elapsed = QLabel("用时 00:00")
        self.elapsed.setFont(theme.f(12, mono=True))
        self.elapsed.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        lay.addWidget(self.elapsed, 0, Qt.AlignmentFlag.AlignVCenter)

        self.settings_btn = IconButton("nav-sliders", size=30, icon_size=15,
                                       tooltip="任务设置")
        lay.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_page(self, title: str, sub: str = "") -> None:
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))

    def set_state(self, text: str, tone: str, elapsed: str | None = None) -> None:
        self.badge.set_state(text, tone)
        if elapsed is not None:
            self.elapsed.setText(f"用时 {elapsed}")

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        p.fillRect(self.rect(), paint.qcolor("bg_canvas"))
        p.setPen(QPen(paint.qcolor("border"), 1.0))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
