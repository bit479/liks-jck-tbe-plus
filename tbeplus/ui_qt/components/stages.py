"""任务阶段条：五段式进度，状态由真实运行事件驱动。

状态：idle（未开始）/ active（进行中）/ done（已完成）/ error（失败）。
用“分段胶囊 + 标签”替代圆点连线：更紧凑、更整齐，宽度不足时自动隐藏标签。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .. import paint, theme

_SEG_H = 3


class StageSteps(QWidget):
    def __init__(self, labels: tuple[str, ...] | list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._labels = list(labels)
        self._states = ["idle"] * len(self._labels)
        self.setFixedHeight(26)
        self.setMinimumWidth(300)

    def set_states(self, states: list[str]) -> None:
        states = list(states)[:len(self._labels)]
        states += ["idle"] * (len(self._labels) - len(states))
        if states == self._states:
            return
        self._states = states
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        n = len(self._labels)
        w = self.width()
        if not n or w <= 0:
            return

        font = theme.f(11)
        show_label = w >= 300
        gap = 6
        seg_w = (w - gap * (n - 1)) / n
        seg_y = 7.0

        for i, label in enumerate(self._labels):
            state = self._states[i]
            x = i * (seg_w + gap)
            if state == "active":
                seg_ink, ink, weight = "accent", "accent_text", 600
            elif state == "done":
                seg_ink, ink, weight = "ok", "text2", 400
            elif state == "error":
                seg_ink, ink, weight = "danger", "danger", 500
            else:
                seg_ink, ink, weight = "text_disabled", "text3", 400

            paint.surface(p, QRectF(x, seg_y, seg_w, _SEG_H), radius=1.5,
                          fill=seg_ink)
            if show_label:
                paint.text(p, QRectF(x, 12, seg_w, 14), label,
                           theme.f(11, weight), ink,
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
