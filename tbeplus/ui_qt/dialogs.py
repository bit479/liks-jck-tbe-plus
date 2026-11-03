"""统一弹窗：无边框圆角卡片 + 明确的主次操作。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QDialog, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from . import paint, theme
from .components.button import Button


class _Surface(QWidget):
    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint.surface(p, rect, radius=12, fill="bg_elevated",
                      border="border_strong", highlight=14)


class Dialog(QDialog):
    """基础弹窗；子类往 ``body`` 里加内容即可。"""

    def __init__(self, title: str, text: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self._drag: QPoint | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 24)

        self.surface = _Surface()
        outer.addWidget(self.surface)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(38)
        shadow.setColor(QColor(0, 0, 0, 170))
        shadow.setOffset(0, 10)
        self.surface.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self.surface)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)

        head = QHBoxLayout()
        lab = QLabel(title)
        lab.setFont(theme.f(14, 600))
        lab.setStyleSheet(f"color: {theme.C['text']}; background: transparent;")
        head.addWidget(lab)
        head.addStretch(1)
        lay.addLayout(head)

        if text:
            body = QLabel(text)
            body.setFont(theme.f(12))
            body.setWordWrap(True)
            body.setStyleSheet(f"color: {theme.C['text2']}; background: transparent;")
            lay.addWidget(body)

        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        lay.addLayout(self.body)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.actions.addStretch(1)
        lay.addSpacing(4)
        lay.addLayout(self.actions)

        self.setMinimumWidth(420)

    def add_action(self, text: str, *, variant: str = "secondary",
                   accept: bool = True) -> Button:
        btn = Button(text, variant=variant, height=32, pad=16)
        btn.clicked.connect(self.accept if accept else self.reject)
        self.actions.addWidget(btn)
        return btn

    # ------------------------------------------------------------------ 拖拽
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag = None


def confirm(parent: QWidget | None, title: str, text: str,
            ok_text: str = "确定", cancel_text: str = "取消",
            danger: bool = False) -> bool:
    dlg = Dialog(title, text, parent)
    dlg.add_action(cancel_text, accept=False)
    dlg.add_action(ok_text, accept=True,
                   variant="danger" if danger else "primary")
    return dlg.exec() == QDialog.DialogCode.Accepted
