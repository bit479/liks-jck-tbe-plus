"""实时日志：四列事件列表（时间 / 级别 / 来源 / 内容）。

实现要点：
- QAbstractListModel + 自绘 delegate，行数再多也不卡（虚拟滚动）；
- 五档筛选（全部 / 成功 / 关键 / 警告 / 错误）与关键词搜索走 proxy；
- 自动滚动可暂停，行数上限 3000，避免长任务无限增长；
- 空状态是一块轻量提示，而不是巨大的空白框。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel, QModelIndex, QRectF, QSortFilterProxyModel, QTimer, Qt, Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QListView, QStackedLayout, QStyle,
    QStyledItemDelegate, QWidget,
)

from .. import icons, paint, theme
from ..viewmodel import ERROR, INFO, KEY, LogRecord, SUCCESS, WARN

_LEVEL_STYLE = {
    INFO: ("INFO", "text3", "text2"),
    SUCCESS: ("OK", "ok", "text2"),
    KEY: ("KEY", "accent_text", "text"),
    WARN: ("WARN", "warn", "text2"),
    ERROR: ("ERR", "danger", "danger"),
}

_MAX_ROWS = 3000
_ROW_H = 28
# 列坐标（页面上的列头复用同一组数值，保证表头与内容对齐）
LEFT = 12
COL_TIME_W = 62
COL_LEVEL_W = 58
COL_SOURCE_W = 62
COL_LEVEL_X = LEFT + COL_TIME_W
COL_SOURCE_X = COL_LEVEL_X + COL_LEVEL_W + 4
COL_TEXT_X = COL_SOURCE_X + COL_SOURCE_W + 6
COL_TIME = LEFT


class LogModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[LogRecord] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        rec = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return rec.text
        return None

    def record(self, row: int) -> LogRecord | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def append(self, rec: LogRecord) -> None:
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(rec)
        self.endInsertRows()
        if len(self._rows) > _MAX_ROWS:
            drop = len(self._rows) - _MAX_ROWS
            self.beginRemoveRows(QModelIndex(), 0, drop - 1)
            del self._rows[:drop]
            self.endRemoveRows()

    def clear(self) -> None:
        if not self._rows:
            return
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()


class LogFilter(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.level = "all"
        self.keyword = ""

    def set_level(self, level: str) -> None:
        self.level = level
        self.invalidateFilter()

    def set_keyword(self, keyword: str) -> None:
        self.keyword = keyword.strip().lower()
        self.invalidateFilter()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.UserRole and index.isValid():
            src = self.mapToSource(index)
            return self.sourceModel().record(src.row())
        return super().data(index, role)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model: LogModel = self.sourceModel()  # type: ignore[assignment]
        rec = model.record(source_row)
        if rec is None:
            return False
        if self.level != "all" and rec.level != self.level:
            return False
        if self.keyword:
            hay = f"{rec.text} {rec.source} {rec.time}".lower()
            if self.keyword not in hay:
                return False
        return True


class LogDelegate(QStyledItemDelegate):
    def __init__(self, view: QWidget) -> None:
        super().__init__(view)
        self._view = view
        self._hover = -1

    def set_hover(self, row: int) -> None:
        if row != self._hover:
            self._hover = row
            self._view.viewport().update()

    def sizeHint(self, option, index):  # noqa: N802
        size = super().sizeHint(option, index)
        size.setHeight(_ROW_H)
        return size

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        rec: LogRecord = index.data(Qt.ItemDataRole.UserRole)
        if rec is None:
            return
        p = painter
        paint.quality(p)
        rect = QRectF(option.rect)
        row_rect = QRectF(0, rect.y(), self._view.viewport().width(), rect.height())
        if index.row() == self._hover:
            c = QColor(theme.C["bg_hover"])
            c.setAlpha(120)
            paint.surface(p, row_rect.adjusted(4, 1, -4, -1), radius=5, fill=c)
        elif option.state & QStyle.StateFlag.State_Selected:
            paint.surface(p, row_rect.adjusted(4, 1, -4, -1), radius=5,
                          fill="bg_hover_strong")

        cy = rect.y() + rect.height() / 2
        ink = _LEVEL_STYLE.get(rec.level, _LEVEL_STYLE[INFO])

        time_font = theme.f(11, mono=True)
        paint.text(p, QRectF(COL_TIME, rect.y(), COL_TIME_W, rect.height()),
                   rec.time, time_font, "text3")

        lx = COL_LEVEL_X
        paint.dot(p, lx + 3, cy, 2.6, ink[1])
        paint.text(p, QRectF(lx + 10, rect.y(), COL_LEVEL_W, rect.height()),
                   ink[0], theme.f(10, 600, spacing=0.4), ink[1])

        sx = COL_SOURCE_X
        paint.text(p, QRectF(sx, rect.y(), COL_SOURCE_W, rect.height()),
                   rec.source, theme.f(11), "text3")

        cx = COL_TEXT_X
        avail = rect.width() - cx - 12
        font = theme.f(12)
        body = paint.elide(rec.text, font, avail)
        paint.text(p, QRectF(cx, rect.y(), max(0, avail), rect.height()),
                   body, font, ink[2])


class _EmptyState(QWidget):
    def __init__(self, title: str, hint: str, steps: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._hint = hint
        self._steps = list(steps or [])

    def set_texts(self, title: str, hint: str,
                  steps: list[str] | None = None) -> None:
        self._title, self._hint = title, hint
        self._steps = list(steps or [])
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        step_h = 19
        total = 24 + 10 + 20 + 6 + 20 + (len(self._steps) * step_h + 14
                                         if self._steps else 0)
        y = max(8.0, (self.height() - total) / 2)
        cx = self.width() / 2

        paint.paint_icon(p, "nav-log", QRectF(cx - 12, y, 24, 24),
                         theme.C["text_disabled"])
        y += 24 + 10
        p.setFont(theme.f(13, 500))
        p.setPen(paint.qcolor("text3"))
        p.drawText(QRectF(0, y, self.width(), 20),
                   int(Qt.AlignmentFlag.AlignCenter), self._title)
        y += 26
        p.setFont(theme.f(12))
        p.setPen(paint.qcolor("text_disabled"))
        p.drawText(QRectF(0, y, self.width(), 20),
                   int(Qt.AlignmentFlag.AlignCenter), self._hint)
        y += 34 if self._steps else 0

        if self._steps:
            font = theme.f(11)
            p.setFont(font)
            widest = max(p.fontMetrics().horizontalAdvance(t) for t in self._steps)
            left = cx - widest / 2 - 6
            for step in self._steps:
                p.setPen(paint.qcolor("text_disabled"))
                p.drawText(QRectF(left, y, widest + 12, step_h),
                           int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                           step)
                y += step_h


class LogView(QWidget):
    count_changed = Signal(int)

    def __init__(self, *, empty_title: str = "还没有日志",
                 empty_hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._auto = True

        self.model = LogModel()
        self.proxy = LogFilter()
        self.proxy.setSourceModel(self.model)

        self.list = QListView()
        self.list.setModel(self.proxy)
        self.list.setItemDelegate(LogDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setMouseTracking(True)
        self.list.setStyleSheet("QListView { background: transparent; outline: none; }")
        self.list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.list.entered.connect(lambda idx: self.list.itemDelegate().set_hover(idx.row()))

        self._empty_title = empty_title
        self._empty_hint = empty_hint
        self._empty_steps = [
            "① 先在执行面板「准备并接管客户端」",
            "② 点「开始点赞」或「试运行」",
            "③ 每篇资讯的结果会实时出现在这里",
        ]
        self.empty = _EmptyState(empty_title, empty_hint, self._empty_steps)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.empty)
        self._stack.addWidget(self.list)
        self._stack.setCurrentWidget(self.empty)

        # 源模型变化（新日志/清空）与代理变化（筛选/搜索）都要刷新空状态与计数
        for sig in (self.model.rowsInserted, self.model.rowsRemoved,
                    self.model.modelReset, self.proxy.rowsInserted,
                    self.proxy.rowsRemoved, self.proxy.modelReset,
                    self.proxy.layoutChanged):
            sig.connect(self._on_rows)

    # ------------------------------------------------------------------ 对外
    def append(self, rec: LogRecord) -> None:
        self.model.append(rec)
        if self._auto:
            QTimer.singleShot(0, self._scroll_bottom)

    def append_many(self, recs: list[LogRecord]) -> None:
        for rec in recs:
            self.model.append(rec)
        if self._auto:
            QTimer.singleShot(0, self._scroll_bottom)

    def clear(self) -> None:
        self.model.clear()

    def set_filter(self, level: str) -> None:
        self.proxy.set_level(level)

    def set_keyword(self, text: str) -> None:
        self.proxy.set_keyword(text)

    def set_auto_scroll(self, on: bool) -> None:
        self._auto = on
        if on:
            self._scroll_bottom()

    def records(self) -> list[LogRecord]:
        return [self.model.record(i) for i in range(self.model.rowCount())]

    def visible_records(self) -> list[LogRecord]:
        """当前筛选 + 搜索后可见的记录（复制/导出用）。"""
        out: list[LogRecord] = []
        for row in range(self.proxy.rowCount()):
            rec = self.proxy.index(row, 0).data(Qt.ItemDataRole.UserRole)
            if rec is not None:
                out.append(rec)
        return out

    def visible_count(self) -> int:
        return self.proxy.rowCount()

    # ------------------------------------------------------------------ 内部
    def _scroll_bottom(self) -> None:
        bar = self.list.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_rows(self, *args) -> None:  # noqa: ANN002
        total = self.model.rowCount()
        shown = self.proxy.rowCount()
        if shown:
            self._stack.setCurrentWidget(self.list)
        else:
            if total:
                self.empty.set_texts("没有匹配的事件",
                                     "换个关键词，或把级别筛选切回「全部」")
            else:
                self.empty.set_texts(self._empty_title, self._empty_hint,
                                     self._empty_steps)
            self._stack.setCurrentWidget(self.empty)
        self.count_changed.emit(shown)
