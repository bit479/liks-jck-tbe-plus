"""任务执行面板：状态、进度、阶段、操作按钮合一。

按钮全部转发为信号，由页面绑定到原有业务回调；本组件不执行任何任务逻辑。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import theme
from ..viewmodel import STAGE_LABELS, RunStats
from .button import Button
from .card import Card
from .progress_bar import ProgressBar
from .stages import StageSteps
from .status_badge import StatusBadge

_STATE_LABELS = {
    "idle": ("空闲", "idle"),
    "run": ("运行中", "run"),
    "done": ("已完成", "ok"),
    "stopped": ("已停止", "warn"),
    "error": ("运行出错", "danger"),
}


class _MiniStat(QWidget):
    """右侧的小指标（成功 / 失败 / 用时）。"""

    def __init__(self, label: str, value: str = "0", *, tone: str = "text",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self.value = QLabel(value)
        self.value.setFont(theme.f(13, 600, tabular=True))
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.value.setStyleSheet(f"color: {theme.C[tone]}; background: transparent;")
        cap = QLabel(label)
        cap.setFont(theme.f(11))
        cap.setAlignment(Qt.AlignmentFlag.AlignRight)
        cap.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        lay.addWidget(self.value)
        lay.addWidget(cap)

    def set_value(self, text: str, tone: str | None = None) -> None:
        self.value.setText(text)
        if tone:
            self.value.setStyleSheet(f"color: {theme.C[tone]}; background: transparent;")


class TaskPanel(Card):
    run_clicked = Signal()
    dry_clicked = Signal()
    stop_clicked = Signal()
    attach_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(padding=(18, 14, 18, 14), spacing=10, parent=parent)

        # ---------------- 第一行：标题 / 状态 / 接管入口 ----------------
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel("执行任务")
        title.setFont(theme.f(13, 600))
        title.setStyleSheet(f"color: {theme.C['text']}; background: transparent;")
        head.addWidget(title)

        self.badge = StatusBadge("空闲", "idle")
        head.addWidget(self.badge)

        self.current = QLabel("还没有开始")
        self.current.setFont(theme.f(12))
        self.current.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        head.addWidget(self.current, 1)

        self.attach_btn = Button("准备并接管客户端", variant="ghost", icon="plug",
                                 height=theme.CTRL_H_SM, size=12, pad=10)
        self.attach_btn.clicked.connect(self.attach_clicked)
        head.addWidget(self.attach_btn)
        self.body.addLayout(head)

        # ---------------- 第二行：计数 / 待处理 / 操作按钮 ----------------
        row = QHBoxLayout()
        row.setSpacing(10)

        self.count = QLabel("0 / 0")
        self.count.setFont(theme.f(22, 600, tabular=True))
        self.count.setStyleSheet(f"color: {theme.C['text']}; background: transparent;")
        row.addWidget(self.count, 0, Qt.AlignmentFlag.AlignBottom)
        unit = QLabel("篇")
        unit.setFont(theme.f(12))
        unit.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        row.addWidget(unit, 0, Qt.AlignmentFlag.AlignBottom)
        row.addSpacing(14)

        self.pending = QLabel("待处理 0")
        self.pending.setFont(theme.f(12))
        self.pending.setStyleSheet(f"color: {theme.C['text3']}; background: transparent;")
        row.addWidget(self.pending, 0, Qt.AlignmentFlag.AlignBottom)

        row.addStretch(1)
        self.run_btn = Button("开始点赞", variant="primary", icon="play", weight=600,
                              height=34, pad=16)
        self.run_btn.clicked.connect(self.run_clicked)
        row.addWidget(self.run_btn)

        self.dry_btn = Button("试运行", variant="secondary", icon="eye", height=34, pad=14)
        self.dry_btn.setToolTip("只读取与判断，不实际点赞")
        self.dry_btn.clicked.connect(self.dry_clicked)
        row.addWidget(self.dry_btn)

        self.stop_btn = Button("停止", variant="danger", icon="square", height=34, pad=13)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_clicked)
        row.addWidget(self.stop_btn)
        self.body.addLayout(row)

        # ---------------- 第三行：进度条 ----------------
        self.bar = ProgressBar(height=6)
        self.body.addWidget(self.bar)

        # ---------------- 第四行：阶段 / 小指标 ----------------
        foot = QHBoxLayout()
        foot.setSpacing(18)
        self.stages = StageSteps(STAGE_LABELS)
        foot.addWidget(self.stages, 1)
        self.stat_ok = _MiniStat("成功")
        self.stat_err = _MiniStat("失败")
        self.stat_time = _MiniStat("用时", "00:00", tone="text2")
        foot.addWidget(self.stat_ok)
        foot.addWidget(self.stat_err)
        foot.addWidget(self.stat_time)
        self.body.addLayout(foot)

    # ------------------------------------------------------------------ 更新
    def set_state(self, state: str, note: str = "") -> None:
        label, tone = _STATE_LABELS.get(state, _STATE_LABELS["idle"])
        self.badge.set_state(label, tone)
        running = state in ("run", "stopping")
        for btn in (self.run_btn, self.dry_btn, self.attach_btn):
            btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        if note:
            self.current.setText(note)

    def set_current(self, text: str) -> None:
        self.current.setText(text)

    def set_stats(self, stats: RunStats) -> None:
        total = stats.total
        self.count.setText(f"{stats.done} / {total}" if total else f"{stats.done} / 0")
        self.pending.setText(f"待处理 {stats.pending}")
        self.bar.set_progress(stats.done, total)
        self.stat_ok.set_value(str(stats.success))
        self.stat_err.set_value(str(stats.failed),
                                "danger" if stats.failed else "text")

    def set_elapsed(self, text: str) -> None:
        self.stat_time.set_value(text)

    def set_stages(self, states: list[str]) -> None:
        self.stages.set_states(states)
