"""首页视图模型：把业务侧的运行事件收敛成 UI 需要的状态。

设计原则（对应 docs/ui-redesign/UI与业务层解耦方案.md）：
- 不 import ``engine`` / ``client``，只消费文本行；
- 解析规则与现有 Tkinter 版 ``App._ingest`` 保持一致（同一批日志格式），
  便于新旧界面对账；
- 只做“读日志 → 得状态”，不改变任务执行逻辑，也不伪造数据：
  没有事件时所有统计为 0、阶段全部为 idle。

数据来源（真实存在的引擎日志）：
    ``板块：xxx`` / ``列表里共 N 篇`` / ``时间段筛选：…`` /
    ``[i/n] MM-DD <状态> 标题`` / ``本板块小结：…``
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

# 逐条日志状态文字 → 归类（与 tbeplus/ui.py 的 STATUS_KINDS 同源）
STATUS_KINDS: tuple[tuple[str, str], ...] = (
    ("点赞已受理待复查", "unclear"),
    ("新点赞", "liked"),
    ("已确认点赞", "liked"),
    ("原本已点赞", "already"),
    ("待点赞", "dry"),
    ("点赞失败", "failed"),
    ("读状态失败", "failed"),
    ("没看到状态变化", "failed"),
    ("like-unconfirmed", "unclear"),
    ("digg-unconfirmed", "unclear"),
    ("already-liked", "already"),
    ("liked", "liked"),
    ("digg-sent", "liked"),
    ("dry-run", "dry"),
    ("like-failed", "failed"),
    ("read-failed", "failed"),
    ("no-change", "failed"),
    ("need-login", "failed"),
)

STAGE_LABELS = ("客户端连接", "获取资讯", "资讯筛选", "执行点赞", "结果统计")

# 日志级别
INFO, SUCCESS, KEY, WARN, ERROR = "info", "success", "key", "warn", "error"

_LEVEL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (ERROR, ("点赞失败", "读状态失败", "没看到状态变化", "失败", "错误", "异常",
             "need-login", "like-failed", "read-failed", "no-change", "中断")),
    (WARN, ("待点赞", "未成功", "超时", "重试", "跳过", "警告", "unclear")),
    (KEY, ("板块", "时间段筛选", "本板块小结", "列表里共", "开始", "完成",
           "接管", "停止")),
    (SUCCESS, ("新点赞", "已确认点赞", "原本已点赞", "成功")),
)

_SOURCE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("连接", ("客户端", "连接", "接管", "token", "端口", "会话")),
    ("资讯", ("板块", "列表", "时间段", "筛选", "读取")),
    ("点赞", ("点赞", "点过", "已赞", "like", "digg")),
)


def classify_level(text: str) -> str:
    for level, keys in _LEVEL_RULES:
        if any(k in text for k in keys):
            return level
    return INFO


def classify_source(text: str) -> str:
    for source, keys in _SOURCE_RULES:
        if any(k in text for k in keys):
            return source
    return "系统"


@dataclass
class LogRecord:
    time: str
    level: str
    source: str
    text: str


@dataclass
class RunStats:
    liked: int = 0
    already: int = 0
    dry: int = 0
    unclear: int = 0
    failed: int = 0
    total: int = 0
    current: int = 0
    section: str = ""

    @property
    def done(self) -> int:
        return self.liked + self.already + self.dry + self.unclear + self.failed

    @property
    def success(self) -> int:
        return self.liked + self.already

    @property
    def pending(self) -> int:
        return max(self.total - self.done, 0)

    @property
    def percent(self) -> int:
        return 0 if not self.total else min(100, round(self.done * 100 / self.total))

    @property
    def success_rate(self) -> float | None:
        if not self.done:
            return None
        return self.success * 100.0 / self.done


@dataclass
class _Section:
    name: str
    total: int = 0
    last: int = 0
    counts: dict[str, int] = field(default_factory=dict)


class HomeViewModel(QObject):
    """首页状态机。UI 只读这里，不直接碰业务对象。"""

    stats_changed = Signal(object)       # RunStats
    stages_changed = Signal(list)        # list[str] 长度 5
    log_added = Signal(object)           # LogRecord
    run_state_changed = Signal(str, str)  # (state, 说明)；state: idle/run/done/error/stopped
    current_changed = Signal(str, str)   # (当前处理说明, 时间戳)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sections: list[_Section] = []
        self._cur: _Section | None = None
        self._seen_section = False
        self._seen_filter = False
        self._seen_item = False
        self._seen_summary = False
        self._seen_connect = False
        self._state = "idle"
        self._note = "等待开始"
        self._started_at = 0.0
        self._elapsed = 0.0

    # ------------------------------------------------------------------ 查询
    @property
    def state(self) -> str:
        return self._state

    @property
    def note(self) -> str:
        return self._note

    @property
    def elapsed(self) -> float:
        if self._state == "run" and self._started_at:
            return time.monotonic() - self._started_at
        return self._elapsed

    def stats(self) -> RunStats:
        liked = already = dry = unclear = failed = 0
        total = 0
        for sec in self._sections:
            liked += sec.counts.get("liked", 0)
            already += sec.counts.get("already", 0)
            dry += sec.counts.get("dry", 0)
            unclear += sec.counts.get("unclear", 0)
            failed += sec.counts.get("failed", 0)
            total += sec.total
        return RunStats(
            liked=liked, already=already, dry=dry, unclear=unclear,
            failed=failed, total=total,
            current=self._cur.last if self._cur else 0,
            section=self._cur.name if self._cur else "",
        )

    def stages(self) -> list[str]:
        """五阶段状态：idle / active / done / error。"""
        st = ["idle"] * 5
        if self._seen_connect:
            st[0] = "done"
        elif self._state == "run":
            st[0] = "active"
        if self._seen_section:
            st[1] = "done"
        elif self._seen_connect and self._state == "run":
            st[1] = "active"
        if self._seen_filter or self._seen_item:
            st[2] = "done"
        elif self._seen_section:
            st[2] = "active"
        if self._seen_item:
            st[3] = "done" if self._seen_summary else "active"
        elif self._seen_filter:
            st[3] = "active"
        if self._seen_summary:
            st[4] = "done"
        elif self._seen_item and self._state in ("done", "stopped"):
            st[4] = "active"

        if self._state == "error":
            for i in range(4, -1, -1):
                if st[i] != "done":
                    st[i] = "error"
                    break
        elif self._state == "idle":
            st = ["idle"] * 5
        return st

    # ------------------------------------------------------------------ 生命周期
    def start(self, note: str = "正在准备…") -> None:
        self._sections.clear()
        self._cur = None
        self._seen_section = self._seen_filter = False
        self._seen_item = self._seen_summary = False
        self._seen_connect = False
        self._state = "run"
        self._note = note
        self._started_at = time.monotonic()
        self._elapsed = 0.0
        self._emit_all()

    def finish(self, state: str) -> None:
        if self._state == "run":
            self._elapsed = self.elapsed
        self._state = state
        self._note = {"done": "已完成", "stopped": "已停止",
                      "error": "运行出错"}.get(state, self._note)
        self._emit_all()

    def reset(self) -> None:
        self._sections.clear()
        self._cur = None
        self._state = "idle"
        self._started_at = 0.0
        self._elapsed = 0.0
        self._seen_section = self._seen_filter = False
        self._seen_item = self._seen_summary = self._seen_connect = False
        self._note = "等待开始"
        self._emit_all()

    def mark_connected(self, note: str = "客户端已接管") -> None:
        self._seen_connect = True
        self._note = note
        self._emit_all()

    # ------------------------------------------------------------------ 事件输入
    def ingest_line(self, line: str, *, stamp: str | None = None,
                    level: str | None = None,
                    source: str | None = None) -> LogRecord:
        """喂入一行引擎日志：既产出日志记录，也推进统计与阶段。"""
        s = line.strip()
        now = stamp or time.strftime("%H:%M:%S")
        rec = LogRecord(now, level or classify_level(s),
                        source or classify_source(s), s)

        if s:
            self._apply(s)
        self.log_added.emit(rec)
        return rec

    def _apply(self, s: str) -> None:
        if "接管" in s or "客户端已连接" in s or "端口" in s:
            self._seen_connect = True

        if "时间段筛选" in s:
            self._seen_filter = True
            self._emit_stats()
            return

        m = re.match(r"^板块：(.+)$", s) or re.match(r"^板块（会话）：(.+)$", s)
        if m:
            name = m.group(1).strip()
            self._cur = _Section(name)
            self._sections.append(self._cur)
            self._seen_section = True
            self.current_changed.emit(f"正在处理：{name}", time.strftime("%H:%M:%S"))
            self._emit_progress()
            return

        m = re.match(r"^列表里共 (\d+) 篇", s)
        if m and self._cur is not None:
            self._cur.total = int(m.group(1))
            self._emit_stats()
            return

        m = re.match(r"^\[(\d+)/(\d+)\]\s+(.+)$", s)
        if m and self._cur is not None:
            idx, total, rest = int(m.group(1)), int(m.group(2)), m.group(3)
            if total:
                self._cur.total = max(self._cur.total, total)
            if idx <= self._cur.last:
                return
            self._cur.last = idx
            rest = re.sub(r"^\d{2}-\d{2}\s+", "", rest)
            kind, title = "failed", rest
            for label, k in STATUS_KINDS:
                if rest.startswith(label):
                    kind, title = k, rest[len(label):].strip()
                    break
            title = re.sub(r"^\([^)]*\)\s*", "", title)
            self._cur.counts[kind] = self._cur.counts.get(kind, 0) + 1
            self._seen_item = True
            hint = title[:56] + ("…" if len(title) > 56 else "")
            self.current_changed.emit(hint or f"第 {idx} 篇", time.strftime("%H:%M:%S"))
            self._emit_progress()
            return

        if s.startswith("本板块小结"):
            self._seen_summary = True
            self._emit_progress()
            return

        if "已手动停止" in s:
            self.finish("stopped")

    # ------------------------------------------------------------------ 内部
    def _emit_stats(self) -> None:
        self.stats_changed.emit(self.stats())

    def _emit_progress(self) -> None:
        """只更新统计与阶段：运行状态由 start/finish 显式驱动，
        避免每条日志都重发 run_state 把“当前资讯”刷回初始文案。"""
        self.stats_changed.emit(self.stats())
        self.stages_changed.emit(self.stages())

    def _emit_all(self) -> None:
        self.stats_changed.emit(self.stats())
        self.stages_changed.emit(self.stages())
        self.run_state_changed.emit(self._state, self._note)
