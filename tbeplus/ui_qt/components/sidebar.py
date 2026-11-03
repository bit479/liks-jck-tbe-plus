"""侧栏：版本切换、三分组导航、底部客户端状态与版本信息。"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import paint, theme
from .button import IconButton
from .card import Card

NAV_GROUP_TITLE_H = 30


class NavItem(QWidget):
    """单个导航项：自绘，带 hover 过渡与清晰但克制的选中态。"""

    clicked = Signal(str)

    def __init__(self, key: str, label: str, icon: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self._label = label
        self._icon = icon
        self._active = False
        self._hover = 0.0
        self.setFixedHeight(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v) -> None:
        self._hover = float(v)
        self.update()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._animate(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._animate(0.0)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.key)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Space):
            self.clicked.emit(self.key)
            e.accept()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(0, 0, self.width(), self.height())
        if self._active:
            paint.surface(p, rect, radius=6, fill="bg_active", highlight=12)
        elif self._hover > 0:
            c = QColor(theme.C["bg_hover"])
            c.setAlpha(int(255 * self._hover))
            paint.surface(p, rect, radius=6, fill=c)

        ink = ("accent_text" if self._active else
               "text" if self._hover > 0.4 else "text2")
        paint.paint_icon(p, self._icon, QRectF(10, 8, 16, 16),
                         theme.C["accent"] if self._active else theme.C[ink])
        paint.text(p, QRectF(34, 0, self.width() - 40, self.height()),
                   self._label, theme.f(13, 500), ink)
        if self.hasFocus():
            ring = QColor(theme.C["accent"])
            ring.setAlpha(70)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 1.0))
            p.drawPath(paint.rrect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6))


class VersionPicker(QWidget):
    """侧栏顶部的版本选择器，点击有明确的交互反馈。"""

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._hover = 0.0
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v) -> None:
        self._hover = float(v)
        self.update()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._animate(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._animate(0.0)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        rect = QRectF(0, 0, self.width(), self.height())
        if self._hover > 0:
            c = QColor(theme.C["bg_hover"])
            c.setAlpha(int(255 * self._hover))
            paint.surface(p, rect, radius=6, fill=c)
        paint.dot(p, 14, self.height() / 2, 3.4, "accent")
        font = theme.f(12, 500)
        label = paint.elide(self._text, font, self.width() - 62)
        paint.text(p, QRectF(24, 0, self.width() - 58, self.height()), label,
                   font, "text" if self._hover > 0.4 else "text2")
        paint.paint_icon(p, "chevron-down",
                         QRectF(self.width() - 26, (self.height() - 14) / 2, 14, 14),
                         theme.C["text3"])


class _StatusRow(QWidget):
    """状态点 + 状态文字（自绘，避免 QLabel 无法着色圆点）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text = "正在检查…"
        self.tone = "text3"
        self.setFixedHeight(20)

    def set(self, text: str, tone: str) -> None:
        self.text, self.tone = text, tone
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        h = self.height()
        paint.dot(p, 4, h / 2, 3.2, self.tone)
        paint.text(p, QRectF(13, 0, self.width() - 16, h), self.text,
                   theme.f(12, 600), "text")


class Sidebar(QWidget):
    page_selected = Signal(str)

    def __init__(self, groups: list[tuple[str, list[tuple[str, str, str]]]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(theme.SIDEBAR_W)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.nav: dict[str, NavItem] = {}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 12)
        lay.setSpacing(1)

        self.version = VersionPicker("老版本 TBe+")
        self.version.setToolTip("点击切换版本模式（客户端会话 / 钉钉版）")
        lay.addWidget(self.version)

        for title, items in groups:
            lay.addSpacing(6)
            group = QLabel(title)
            group.setFont(theme.f(11, 500, spacing=0.3))
            group.setStyleSheet(
                f"color: {theme.C['text3']}; background: transparent;"
                "padding-left: 10px;")
            lay.addWidget(group)
            lay.addSpacing(2)
            for key, label, icon in items:
                item = NavItem(key, label, icon)
                item.clicked.connect(self.page_selected)
                lay.addWidget(item)
                self.nav[key] = item

        lay.addStretch(1)

        self.status_card = Card(fill="bg_surface", radius=9,
                                padding=(12, 10, 12, 10), spacing=4)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(4)
        cap = QLabel("客户端状态")
        cap.setFont(theme.f(11))
        cap.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        head.addWidget(cap)
        head.addStretch(1)
        self.refresh_btn = IconButton("refresh", size=20, icon_size=12,
                                      tooltip="重新检测客户端状态")
        head.addWidget(self.refresh_btn)
        self.status_card.body.addLayout(head)

        self._state_row = _StatusRow()
        self.status_card.body.addWidget(self._state_row)

        self.note = QLabel("")
        self.note.setFont(theme.f(11))
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        self.status_card.body.addWidget(self.note)
        lay.addWidget(self.status_card)

        lay.addSpacing(8)
        self.footer = QLabel("")
        self.footer.setFont(theme.f(11))
        self.footer.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;"
                                  "padding-left: 4px;")
        lay.addWidget(self.footer)

    def set_page(self, key: str) -> None:
        for k, item in self.nav.items():
            item.set_active(k == key)

    def set_connection(self, text: str, tone: str = "text3", note: str = "") -> None:
        self._state_row.set(text, tone)
        self.note.setText(note)
        self.status_card.updateGeometry()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        p.fillRect(self.rect(), paint.qcolor("bg_sidebar"))
        p.setPen(QPen(paint.qcolor("border"), 1.0))
        p.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
