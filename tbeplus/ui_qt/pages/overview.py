"""首页：统计四卡 + 执行面板 + 实时事件列表。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from .. import icons, paint, theme
from ..components.button import Button, IconButton
from ..components.card import Card
from ..components.chips import FilterChips
from ..components.log_view import (
    COL_LEVEL_X, COL_SOURCE_X, COL_TEXT_X, COL_TIME, COL_TIME_W, LogView,
)
from ..components.stat_card import StatCard
from ..components.task_panel import TaskPanel
from ..components.toggle import Toggle
from ..viewmodel import HomeViewModel, RunStats

_FILTERS = [("all", "全部"), ("success", "成功"), ("key", "关键"),
            ("warn", "警告"), ("error", "错误")]


class _ColumnHeader(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(22)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        font = theme.f(11)
        for text, x, w in (("时间", COL_TIME, COL_TIME_W),
                           ("级别", COL_LEVEL_X + 10, 44),
                           ("来源", COL_SOURCE_X, 44),
                           ("事件", COL_TEXT_X, self.width() - COL_TEXT_X)):
            paint.text(p, QRectF(x, 0, w, self.height()), text, font, "text3")
        p.setPen(paint.qcolor("border_soft"))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class OverviewPage(QWidget):
    start_requested = Signal()
    dry_requested = Signal()
    stop_requested = Signal()
    attach_requested = Signal()
    open_log_requested = Signal()

    def __init__(self, vm: HomeViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vm = vm
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(theme.GAP_X, theme.GAP_Y, theme.GAP_X, theme.GAP_Y)
        root.setSpacing(theme.GAP_Y)

        # ---------------- 统计 ----------------
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.card_processed = StatCard("已处理资讯", tone="text3")
        self.card_liked = StatCard("新增点赞", tone="accent")
        self.card_already = StatCard("原本已赞", tone="ok")
        self.card_failed = StatCard("执行失败", tone="danger")
        for card in (self.card_processed, self.card_liked,
                     self.card_already, self.card_failed):
            card.setFixedHeight(100)
            stats_row.addWidget(card, 1)
        root.addLayout(stats_row)

        # ---------------- 执行面板 ----------------
        self.task = TaskPanel()
        self.task.run_clicked.connect(self.start_requested)
        self.task.dry_clicked.connect(self.dry_requested)
        self.task.stop_clicked.connect(self.stop_requested)
        self.task.attach_clicked.connect(self.attach_requested)
        root.addWidget(self.task)

        # ---------------- 实时日志 ----------------
        card = Card(padding=(16, 12, 16, 10), spacing=6)
        card.setMinimumHeight(240)
        head = card.add_title("实时日志")

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索事件或来源…")
        self.search.setFixedWidth(188)
        self.search.setClearButtonEnabled(True)
        self.search.setFont(theme.f(12))
        self.search.textChanged.connect(lambda t: self.log.set_keyword(t))
        head.addWidget(self.search)

        self.pause_toggle = Toggle(False)
        self.pause_toggle.setToolTip("暂停自动滚动")
        self.pause_toggle.toggled.connect(self._on_pause)
        head.addWidget(self.pause_toggle)

        self.copy_btn = IconButton("copy", size=28, icon_size=14, tooltip="复制全部日志")
        self.export_btn = IconButton("download", size=28, icon_size=14, tooltip="导出日志")
        self.clear_btn = IconButton("trash", size=28, icon_size=14, tooltip="清空日志")
        for b in (self.copy_btn, self.export_btn, self.clear_btn):
            head.addWidget(b)
        self.copy_btn.clicked.connect(self._copy_logs)
        self.export_btn.clicked.connect(self._export_logs)
        self.clear_btn.clicked.connect(self._clear_logs)

        # 筛选行
        frow = QHBoxLayout()
        frow.setContentsMargins(0, 2, 0, 0)
        frow.setSpacing(8)
        self.filters = FilterChips(_FILTERS)
        self.filters.changed.connect(lambda key: self.log.set_filter(key))
        frow.addWidget(self.filters)
        frow.addStretch(1)
        self.count_label = QLabel("0 条")
        self.count_label.setFont(theme.f(11))
        self.count_label.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        frow.addWidget(self.count_label)
        self.all_btn = Button("查看全部", variant="link", icon="chevron-right",
                              height=24, size=12, pad=4)
        self.all_btn.clicked.connect(self.open_log_requested)
        frow.addWidget(self.all_btn)
        card.body.addLayout(frow)

        card.body.addWidget(_ColumnHeader())

        self.log = LogView(empty_title="还没有日志",
                           empty_hint="点「开始点赞」或「试运行」，这里会按时间记录每篇资讯")
        self.log.count_changed.connect(self._on_count)
        card.body.addWidget(self.log, 1)
        root.addWidget(card, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        vm.stats_changed.connect(self._on_stats)
        vm.stages_changed.connect(self.task.set_stages)
        vm.log_added.connect(self.log.append)
        vm.run_state_changed.connect(self._on_state)
        vm.current_changed.connect(lambda text, ts: self.task.set_current(text))

        self._on_stats(vm.stats())
        self.task.set_stages(vm.stages())
        self._on_state(vm.state, vm.note)

    # ------------------------------------------------------------------ 槽
    def _on_stats(self, stats: RunStats) -> None:
        total = stats.total
        self.card_processed.set_value(
            stats.done, f"共 {total} 篇" if total else "等待运行")
        self.card_liked.set_value(
            stats.liked, f"待点赞 {stats.dry}" if stats.dry else "本轮新增")
        self.card_already.set_value(stats.already, "已赞过，自动跳过")
        self.card_failed.set_value(
            stats.failed, "可重跑补齐" if stats.failed else "无失败")
        self.task.set_stats(stats)

    def _on_state(self, state: str, note: str) -> None:
        self.task.set_state(state, note)
        if state == "done":
            s = self._vm.stats()
            self.task.set_current(
                f"本轮完成：新增 {s.liked} · 原本已赞 {s.already} · 失败 {s.failed}")
        elif state == "idle":
            self.task.set_current("还没有开始，点「开始点赞」或先「试运行」")
        elif state == "stopped":
            self.task.set_current("已手动停止，已经点上的赞不会重复")
        self._tick()

    def _on_count(self, count: int) -> None:
        self.count_label.setText(f"{count} 条")

    def _on_pause(self, paused: bool) -> None:
        self.log.set_auto_scroll(not paused)

    def _tick(self) -> None:
        sec = int(self._vm.elapsed)
        text = f"{sec // 60:02d}:{sec % 60:02d}"
        self.task.set_elapsed(text)
        self._last_elapsed = text

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _line(rec) -> str:  # noqa: ANN001
        return "\t".join((rec.time, rec.level.upper(), rec.source, rec.text))

    def _copy_logs(self) -> None:
        recs = self.log.visible_records()
        if not recs:
            self._toast("没有可复制的日志")
            return
        QGuiApplication.clipboard().setText("\n".join(self._line(r) for r in recs))
        self._toast(f"已复制 {len(recs)} 条日志")

    def _export_logs(self) -> None:
        recs = self.log.visible_records()
        if not recs:
            self._toast("没有可导出的日志")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", "tbeplus-log.txt", "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\t".join(("时间", "级别", "来源", "事件")) + "\n")
                fh.write("\n".join(self._line(r) for r in recs))
                fh.write("\n")
        except OSError as exc:
            self._toast(f"导出失败：{exc}")
            return
        self._toast(f"已导出 {len(recs)} 条到 {path}")

    def _clear_logs(self) -> None:
        if not self.log.records():
            self._toast("日志已经是空的")
            return
        self.log.clear()
        self._toast("已清空日志")

    def _toast(self, text: str) -> None:
        win = self.window()
        if hasattr(win, "show_toast"):
            win.show_toast(text)

    # ------------------------------------------------------------------ 对外
    def elapsed_text(self) -> str:
        return getattr(self, "_last_elapsed", "00:00")

    def set_auto_scroll(self, on: bool) -> None:
        self.pause_toggle.set_on(not on, notify=False)
        self.log.set_auto_scroll(on)
