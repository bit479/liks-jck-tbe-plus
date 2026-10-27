"""图形界面：双击就能用，三个按钮走完全流程。

版面：左侧深色导航（概览 / 运行日志 / 任务设置）+ 顶部标题栏 + 白色工作区。
所有控件自绘（见 widgets.py），不依赖 ttk 主题、不依赖第三方库。
旧版界面保留在 ui_classic.py，随时可以退回去。
"""

from __future__ import annotations

import asyncio
import os
import queue
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from tkinter import (
    BOTH, LEFT, RIGHT, X, Y, BooleanVar, StringVar, Tk, filedialog, messagebox,
)

import tkinter as tk

from . import __version__ as VERSION
from . import client as client_mod
from . import theme as T
from . import widgets as W
from .config import APP_DIR, Config, load_config
from .engine import LikeEngine, parse_user_date, summarize
from .theme import C

# 逐条日志里的状态文字 → 归类。API 模式打中文，页面模式打英文，两种都要认
STATUS_KINDS = (
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

HOME = "home"
LOG = "log"
SETTINGS = "settings"

PAGES = {
    HOME: ("概览", "自动遍历已勾选的板块，把没点赞的文章都点上；赞过的会跳过。"),
    LOG: ("运行日志", "完整过程记录。逐条明细可以折叠，出问题时先看这里。"),
    SETTINGS: ("任务设置", "时间段、板块开关和运行节奏。改完回概览开始跑。"),
}


class RadioChips(tk.Frame):
    """一组只能选一个的胶囊。"""

    def __init__(self, master, options, command=None, bg=C["surface"]):
        super().__init__(master, bg=bg)
        self._command = command
        self.chips: dict = {}
        for key, text in options:
            chip = W.Chip(self, text, False,
                          command=lambda k=key: self.choose(k), bg=bg)
            chip.pack(side=LEFT, padx=(0, 8))
            self.chips[key] = chip

    def choose(self, key: str, notify: bool = True) -> None:
        for k, chip in self.chips.items():
            chip.set(k == key)
        if notify and self._command:
            self._command(key)


class App:
    def __init__(self, root: Tk, config: Config):
        self.root = root
        self.cfg = config
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        # 后台线程不能直接碰 Tk（跨线程调 after 会偶发 RuntimeError），
        # 状态变更一律塞进队列，由主线程的 _drain 统一处理
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()

        self.running = False
        self.started_at = 0.0
        self.cur_section: str | None = None
        self.sections: dict = {}
        self.page = HOME

        self._compact: bool | None = None
        self._resize_job = None
        self._section_labels: list = []
        self._status_busy = False
        self._client_port = None
        self._client_running = False
        self._status_event = threading.Event()
        self._status_event.set()

        root.title(f"TBe+ 新闻资讯自动点赞  v{VERSION}")
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(1280, max(800, sw - 48))
        h = min(880, max(640, sh - 110))
        # 调试用：TBE_UI_SIZE=1000x740 强制窗口尺寸（截图、录屏方便）
        forced = os.environ.get("TBE_UI_SIZE", "")
        if "x" in forced:
            try:
                w, h = (int(v) for v in forced.lower().split("x", 1))
            except ValueError:
                pass
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 16)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(min(880, w), min(700, h))
        root.configure(bg=C["bg"])

        self._build_sidebar()
        self._build_header()
        self._build_pages()
        self._show_page(HOME)

        self._refresh_adv()
        self._refresh_status()
        self._sync_sections()
        self.root.after(80, self._drain)
        self.root.after(500, self._tick)
        self.root.after(60, self._sync_compact)
        self.root.after(400, self._sync_compact)
        self.root.after(260, self._sync_window_size)
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _sync_window_size(self) -> None:
        """把 Tk 的尺寸模型对回系统实际客户区。

        某些 DPI 缩放/远程桌面环境里，窗口管理器会把窗口压到比请求更小，
        但 Tk 仍按请求的尺寸排版——结果是右侧内容被裁掉，滚动条也出不来。
        映射完成后用 Win32 读一次真实客户区，发现不一致就按真实尺寸重设。
        """
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            rect = wintypes.RECT()
            if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect)):
                return
            rw, rh = rect.right, rect.bottom
            if rw < 200 or rh < 200:
                return
            if abs(rw - self.root.winfo_width()) > 8 or \
                    abs(rh - self.root.winfo_height()) > 8:
                self.root.geometry(f"{rw}x{rh}")
                self.root.after(60, self._sync_compact)
        except Exception:  # noqa: BLE001
            pass

    # ==================================================================
    # 骨架
    # ==================================================================
    def _build_sidebar(self) -> None:
        bar = tk.Frame(self.root, bg=C["nav"], width=T.SIDEBAR_W)
        bar.pack(side=LEFT, fill=Y)
        bar.pack_propagate(False)
        self.sidebar = bar

        # 品牌
        brand = tk.Frame(bar, bg=C["nav"])
        brand.pack(fill=X, padx=16, pady=(20, 18))
        logo = tk.Canvas(brand, width=38, height=38, bg=C["nav"],
                         highlightthickness=0, bd=0)
        logo.pack(side=LEFT)
        W.round_rect(logo, 1, 1, 37, 37, 9, fill=C["accent"], outline="", width=0)
        W.round_rect(logo, 1, 1, 37, 19, 9,
                     fill=W.blend(C["accent"], C["on_dark"], 0.18), outline="", width=0)
        logo.create_text(19, 20, text="\uEB51", font=T.icon(12), fill=C["on_dark"])
        texts = tk.Frame(brand, bg=C["nav"])
        texts.pack(side=LEFT, padx=(10, 0))
        tk.Label(texts, text="TBe+ 自动点赞", font=T.f(11, True), fg=C["on_dark"],
                 bg=C["nav"], anchor="w").pack(anchor="w")
        tk.Label(texts, text=f"新闻资讯  ·  v{VERSION}", font=T.f(8),
                 fg=C["nav_muted"], bg=C["nav"], anchor="w").pack(anchor="w")

        # 导航（按"日常运行 / 配置"分组）
        self.nav: dict = {}
        for group, items in (
            ("运行", ((HOME, "概览", "\uE80F"), (LOG, "运行日志", "\uE81C"))),
            ("配置", ((SETTINGS, "任务设置", "\uE713"),)),
        ):
            tk.Label(bar, text=group, font=T.f(8), fg=C["nav_group"], bg=C["nav"],
                     anchor="w").pack(fill=X, padx=22, pady=(8, 4))
            box = tk.Frame(bar, bg=C["nav"])
            box.pack(fill=X, padx=14)
            for key, text, glyph in items:
                item = W.NavItem(box, text, glyph,
                                 command=lambda k=key: self._show_page(k),
                                 active=(key == HOME))
                item.pack(fill=X, pady=1)
                self.nav[key] = item

        # 底部：客户端状态
        footer = tk.Frame(bar, bg=C["nav"])
        footer.pack(side="bottom", fill=X, padx=14, pady=16)

        card = W.Panel(footer, radius=9, fill=C["nav_active"],
                       stroke=C["nav_line"], height=108, bg=C["nav"])
        card.pack(fill=X)
        card.pack_propagate(False)
        body = card.body
        head = tk.Frame(body, bg=C["nav_active"])
        head.pack(fill=X, padx=12, pady=(10, 0))
        tk.Label(head, text="客户端状态", font=T.f(8), fg=C["nav_muted"],
                 bg=C["nav_active"], anchor="w").pack(side=LEFT)
        W.Btn(head, "", lambda: self._refresh_status(), variant="ghost",
              icon="\uE72C", width=24, height=20, pad=4, bg=C["nav_active"],
              ).pack(side=RIGHT)

        line = tk.Frame(body, bg=C["nav_active"])
        line.pack(fill=X, padx=12, pady=(6, 0))
        self.dot = tk.Canvas(line, width=9, height=9, bg=C["nav_active"],
                             highlightthickness=0, bd=0)
        self.dot.pack(side=LEFT)
        self._draw_dot(C["text3"])
        self.status_var = StringVar(value="正在检查…")
        tk.Label(line, textvariable=self.status_var, font=T.f(10, True),
                 fg=C["on_dark"], bg=C["nav_active"], anchor="w").pack(
            side=LEFT, padx=(6, 0))
        self.status_note = StringVar(value="")
        tk.Label(body, textvariable=self.status_note, font=T.f(8),
                 fg=C["nav_muted"], bg=C["nav_active"], anchor="w",
                 justify="left").pack(anchor="w", padx=12, pady=(4, 0))

        W.Btn(footer, "打开程序目录", self._open_output, variant="ghost",
              icon="\uE8B7", width=T.NAV_W, height=32, size=9,
              bg=C["nav"]).pack(fill=X, pady=(10, 0))

    def _draw_dot(self, color: str) -> None:
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 8, 8, fill=color, outline="")

    def _build_header(self) -> None:
        self.main = tk.Frame(self.root, bg=C["bg"])
        self.main.pack(side=LEFT, fill=BOTH, expand=True)

        header = tk.Frame(self.main, bg=C["surface"], height=T.HEADER_H)
        header.pack(fill=X)
        header.pack_propagate(False)
        inner = tk.Frame(header, bg=C["surface"])
        inner.pack(fill=BOTH, expand=True, padx=24)

        left = tk.Frame(inner, bg=C["surface"])
        left.pack(side=LEFT, pady=10)
        self.title_var = StringVar(value="概览")
        self.sub_var = StringVar(value=PAGES[HOME][1])
        tk.Label(left, textvariable=self.title_var, font=T.f(16, True),
                 fg=C["text"], bg=C["surface"], anchor="w").pack(anchor="w")
        tk.Label(left, textvariable=self.sub_var, font=T.f(9), fg=C["text2"],
                 bg=C["surface"], anchor="w").pack(anchor="w", pady=(3, 0))

        right = tk.Frame(inner, bg=C["surface"])
        right.pack(side=RIGHT, pady=10)
        self.elapsed_var = StringVar(value="")
        tk.Label(right, textvariable=self.elapsed_var, font=(T.MONO, 9),
                 fg=C["text2"], bg=C["surface"]).pack(side=LEFT, padx=(0, 10))
        self.pill = W.StatusPill(right, "空闲", "idle", bg=C["surface"])
        self.pill.pack(side=LEFT)

        W.divider(self.main).pack(fill=X)

    def _build_pages(self) -> None:
        host = tk.Frame(self.main, bg=C["bg"])
        host.pack(fill=BOTH, expand=True, padx=24, pady=24)
        self.pages: dict = {}
        for key, builder in (
            (HOME, self._page_home),
            (LOG, self._page_log),
            (SETTINGS, self._page_settings),
        ):
            frame = tk.Frame(host, bg=C["bg"])
            self.pages[key] = frame
            builder(frame)

    def _show_page(self, key: str) -> None:
        self.page = key
        for name, frame in self.pages.items():
            if name == key:
                frame.pack(fill=BOTH, expand=True)
            else:
                frame.pack_forget()
        for name, item in self.nav.items():
            item.set_active(name == key)
        title, sub = PAGES[key]
        self.title_var.set(title)
        self.sub_var.set(sub)

    # ==================================================================
    # 自适应：窄屏两张一排，按钮文案收短
    # ==================================================================
    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        self._debounce_compact()

    def _on_page_configure(self, _event) -> None:
        self._debounce_compact()

    def _debounce_compact(self) -> None:
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(140, self._sync_compact)

    def _sync_compact(self) -> None:
        """按**内容区实际宽度**决定排几列。

        不能只看窗口宽度：打包成 exe 后系统按 DPI 缩放，窗口可能被窗口管理器
        压到比请求更窄，而 Tk 还认为自己有那么宽，于是右侧卡片被裁掉。
        以内容区真实宽度为准，窗口被压缩也能自动变成两列。
        """
        width = self._cards_host.winfo_width()
        if width <= 1:                      # 还没完成布局
            width = self.root.winfo_width() - T.SIDEBAR_W - 2 * T.GAP_L
        self._apply_compact(width < 4 * 232 + 3 * 12)

    def _apply_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self._layout_cards(compact)
        self._layout_actions(compact)
        for label in self._section_labels:
            label.configure(width=15 if compact else 20)
        self._layout_board_chips(compact)

    def _layout_cards(self, compact: bool) -> None:
        cols = 2 if compact else 4
        for card in self._cards:
            card.grid_forget()
        for c in range(4):
            self._cards_host.grid_columnconfigure(
                c, weight=1 if c < cols else 0, uniform="stat" if c < cols else ""
            )
        for i, card in enumerate(self._cards):
            r, c = divmod(i, cols)
            card.grid(
                row=r, column=c, sticky="nsew",
                padx=(0 if c == 0 else 6, 0 if c == cols - 1 else 6),
                pady=(0, 12) if compact and r == 0 else 0,
            )

    def _layout_actions(self, compact: bool) -> None:
        if compact:
            self.btn_attach.set_text("准备接管", "\uE72C")
            self.btn_dry.set_text("试运行", "\uE768")
        else:
            self.btn_attach.set_text("准备并接管客户端", "\uE72C")
            self.btn_dry.set_text("试运行（只看不点）", "\uE768")

    # ==================================================================
    # 页：概览
    # ==================================================================
    def _card_head(self, body: tk.Frame, title: str, note: str = "",
                   bg: str = C["surface"], pad=(20, 12)) -> tk.Frame:
        row = tk.Frame(body, bg=bg)
        row.pack(fill=X, padx=pad[0], pady=(pad[1], 0))
        tk.Label(row, text=title, font=T.f(12, True), fg=C["text"],
                 bg=bg, anchor="w").pack(side=LEFT)
        if note:
            tk.Label(row, text=note, font=T.f(9), fg=C["text3"],
                     bg=bg, anchor="w").pack(side=LEFT, padx=10)
        return row

    def _page_home(self, f: tk.Frame) -> None:
        f.bind("<Configure>", self._on_page_configure)
        # ---------------- 指标 ----------------
        cards = tk.Frame(f, bg=C["bg"])
        cards.pack(fill=X)
        self.card_processed = W.StatCard(cards, "已处理", C["slate"], "\uE73E")
        self.card_liked = W.StatCard(cards, "新点赞", C["accent"], "\uEB51")
        self.card_already = W.StatCard(cards, "原本已赞", C["ok"], "\uEB52")
        self.card_failed = W.StatCard(cards, "未成功", C["text3"], "\uE7BA")
        self._cards = [self.card_processed, self.card_liked,
                       self.card_already, self.card_failed]
        self._cards_host = cards
        self._layout_cards(False)

        # ---------------- 执行 ----------------
        panel = W.Panel(f)
        panel.pack(fill=X, pady=(12, 0))
        panel.pack_propagate(False)
        body = panel.body
        self._card_head(body, "执行", "先接管客户端，再试运行确认，最后正式点赞")

        row = tk.Frame(body, bg=C["surface"])
        row.pack(fill=X, padx=20, pady=(12, 12))
        self.btn_attach = W.Btn(row, "准备并接管客户端", self._on_attach,
                               variant="secondary", icon="\uE72C")
        self.btn_attach.pack(side=LEFT)
        self.btn_dry = W.Btn(row, "试运行（只看不点）", self._on_dry_run,
                             variant="secondary", icon="\uE768")
        self.btn_dry.pack(side=LEFT, padx=8)
        self.btn_run = W.Btn(row, "开始点赞", self._on_run, variant="primary",
                             icon="\uEB51", bold=True)
        self.btn_run.pack(side=LEFT)
        self.btn_stop = W.Btn(row, "停止", self._on_stop, variant="danger",
                              icon="\uE71A", width=84)
        self.btn_stop.pack(side=RIGHT)
        self.btn_stop.set_enabled(False)

        prow = tk.Frame(body, bg=C["surface"])
        prow.pack(fill=X, padx=20)
        self.home_bar = W.Bar(prow, height=6)
        self.home_bar.pack(side=LEFT, fill=X, expand=True)
        self.prog_var = StringVar(value="0 / 0 篇")
        tk.Label(prow, textvariable=self.prog_var, font=(T.MONO, 9),
                 fg=C["text2"], bg=C["surface"], width=12, anchor="e").pack(
            side=RIGHT, padx=(12, 0))

        self.current_var = StringVar(value="就绪：先点「准备并接管客户端」。")
        tk.Label(body, textvariable=self.current_var, font=T.f(9), fg=C["text3"],
                 bg=C["surface"], anchor="w").pack(fill=X, padx=20, pady=(8, 12))
        panel.fit()

        # ---------------- 板块进度 ----------------
        self.sections_panel = W.Panel(f)
        self.sections_panel.pack(fill=X, pady=(12, 0))
        self.sections_panel.pack_propagate(False)
        sbody = self.sections_panel.body
        head = self._card_head(sbody, "板块进度")
        self.section_note = StringVar(value="")
        tk.Label(head, textvariable=self.section_note, font=T.f(9),
                 fg=C["text3"], bg=C["surface"]).pack(side=RIGHT)
        self.rows_host = tk.Frame(sbody, bg=C["surface"])
        self.rows_host.pack(fill=BOTH, expand=True, padx=20, pady=(10, 12))

        # ---------------- 实时日志 ----------------
        logp = W.Panel(f)
        logp.pack(fill=BOTH, expand=True, pady=(12, 0))
        lb = logp.body
        lhead = self._card_head(lb, "实时日志")
        W.Btn(lhead, "全部记录", lambda: self._show_page(LOG), variant="ghost",
              icon="\uE76C", width=96, height=26, size=9,
              bg=C["surface"]).pack(side=RIGHT)
        self.home_quick = RadioChips(
            lhead, (("all", "全部"), ("key", "关键"), ("issue", "问题")),
            command=self._on_home_filter,
        )
        self.home_quick.pack(side=RIGHT, padx=(0, 8))
        self.home_quick.choose("all", notify=False)

        self.home_log = W.LogView(
            lb, empty_hint="还没有日志。点「准备并接管客户端」，或直接点「开始点赞」。")
        self.home_log.pack(fill=BOTH, expand=True, padx=10, pady=(10, 10))

    def _on_home_filter(self, key: str) -> None:
        self.home_log.set_mode(key)

    # ==================================================================
    # 页：日志
    # ==================================================================
    def _page_log(self, f: tk.Frame) -> None:
        panel = W.Panel(f)
        panel.pack(fill=BOTH, expand=True)
        body = panel.body

        head = self._card_head(body, "运行日志", pad=(20, 16))
        W.Btn(head, "清空", self._clear_log, variant="ghost", width=64,
              height=28, size=9, bg=C["surface"]).pack(side=RIGHT)
        W.Btn(head, "导出", self._export_log, variant="secondary", icon="\uE896",
              width=80, height=28, size=9).pack(side=RIGHT, padx=(0, 8))
        self.log_quick = RadioChips(
            head, (("all", "全部"), ("key", "只看关键"), ("issue", "只看问题")),
            command=self._on_log_filter,
        )
        self.log_quick.pack(side=RIGHT, padx=(0, 14))
        self.log_quick.choose("all", notify=False)

        W.divider(body).pack(fill=X, padx=20, pady=(12, 0))
        self.full_log = W.LogView(body, empty_hint="还没有日志。")
        self.full_log.pack(fill=BOTH, expand=True, padx=10, pady=10)

    def _on_log_filter(self, key: str) -> None:
        self.full_log.set_mode(key)

    def _clear_log(self) -> None:
        self.full_log.clear()
        self.home_log.clear()

    def _export_log(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出运行日志", defaultextension=".txt",
            initialfile=f"点赞日志-{date.today():%Y%m%d}.txt",
            filetypes=[("文本文件", "*.txt")],
        )
        if not path:
            return
        try:
            Path(path).write_text(self.full_log.dump(), encoding="utf-8")
            self.log(f"日志已导出到 {path}")
        except OSError as exc:
            messagebox.showwarning("导出失败", str(exc), parent=self.root)

    # ==================================================================
    # 页：设置
    # ==================================================================
    def _page_settings(self, f: tk.Frame) -> None:
        # 表单类页面在大屏上不要拉满整行，超过 880 就留白，不然看着散
        area = W.ScrollArea(f)
        area.pack(fill=BOTH, expand=True)
        holder = area.inner
        holder.bind("<Configure>", self._on_settings_resize)
        self._setting_cards: list = []

        # ---- 新闻时间段 ----
        p_date = W.Panel(holder)
        p_date.pack(fill=X)
        self._setting_cards.append(p_date)
        p_date.pack_propagate(False)
        b = p_date.body
        head = self._card_head(b, "新闻时间段", pad=(20, 12))
        tk.Label(head, text="只处理这个区间内发布的文章；格式 YYYY-MM-DD，留空表示不限",
                 font=T.f(9), fg=C["text3"], bg=C["surface"]).pack(side=LEFT, padx=10)

        r1 = tk.Frame(b, bg=C["surface"])
        r1.pack(fill=X, padx=20, pady=(12, 10))
        tk.Label(r1, text="从", font=T.f(9), fg=C["text2"],
                 bg=C["surface"]).pack(side=LEFT)
        self.since_var = StringVar(value="")
        self.since_entry = W.entry(r1, self.since_var, width=12)
        self.since_entry.pack(side=LEFT, padx=(6, 16), ipady=4)
        tk.Label(r1, text="到", font=T.f(9), fg=C["text2"],
                 bg=C["surface"]).pack(side=LEFT)
        self.until_var = StringVar(value=date.today().isoformat())
        self.until_entry = W.entry(r1, self.until_var, width=12)
        self.until_entry.pack(side=LEFT, padx=(6, 12), ipady=4)
        self.until_now_var = BooleanVar(value=True)
        self.now_chip = W.Chip(r1, "至今", True, command=self._toggle_until)
        self.now_chip.pack(side=LEFT)

        self.range_quick = RadioChips(
            b, (("none", "不限"), ("7", "近 7 天"), ("30", "近 30 天"),
                ("90", "近 90 天"), ("365", "近 1 年")),
            command=self._on_range,
        )
        self.range_quick.pack(anchor="w", padx=20, pady=(0, 12))
        self.range_quick.choose("none", notify=False)
        p_date.fit()

        # ---- 板块 ----
        p_board = W.Panel(holder)
        p_board.pack(fill=X, pady=(10, 0))
        self._setting_cards.append(p_board)
        p_board.pack_propagate(False)
        b = p_board.body
        head = self._card_head(b, "板块", pad=(20, 12))
        tk.Label(head, text="取消勾选 = 本次跳过", font=T.f(9), fg=C["text3"],
                 bg=C["surface"]).pack(side=LEFT, padx=10)
        rowb = tk.Frame(b, bg=C["surface"])
        rowb.pack(fill=X, padx=20, pady=(10, 0))
        self.chip_row1 = tk.Frame(rowb, bg=C["surface"])
        self.chip_row1.pack(fill=X)
        self.chip_row2 = tk.Frame(rowb, bg=C["surface"])
        self.chip_row2.pack(fill=X)
        self.board_chips: dict = {}
        for name in self._board_names():
            self.board_chips[name] = W.Chip(rowb, name, True,
                                            command=self._on_board_change)
        self._layout_board_chips(False)
        self.board_panel = p_board
        p_board.fit(extra=10)

        # ---- 运行参数 ----
        p_run = W.Panel(holder)
        p_run.pack(fill=X, pady=(10, 0))
        self._setting_cards.append(p_run)
        p_run.pack_propagate(False)
        b = p_run.body
        head = self._card_head(b, "运行参数", pad=(20, 12))
        tk.Label(head, text="默认值够用；0 = 不限篇数 / 不重启客户端",
                 font=T.f(9), fg=C["text3"], bg=C["surface"]).pack(side=LEFT, padx=10)

        r3 = tk.Frame(b, bg=C["surface"])
        r3.pack(fill=X, padx=20, pady=(10, 12))
        tk.Label(r3, text="每板块最多", font=T.f(9), fg=C["text2"],
                 bg=C["surface"]).pack(side=LEFT)
        self.limit_stepper = W.Stepper(r3, value=0, from_=0, to=999,
                                       command=self._refresh_summary)
        self.limit_stepper.pack(side=LEFT, padx=8)
        tk.Label(r3, text="篇", font=T.f(9), fg=C["text3"],
                 bg=C["surface"]).pack(side=LEFT)
        tk.Frame(r3, bg=C["stroke"], width=1, height=18).pack(side=LEFT, padx=18)
        tk.Label(r3, text="每", font=T.f(9), fg=C["text2"],
                 bg=C["surface"]).pack(side=LEFT)
        self.restart_stepper = W.Stepper(
            r3, value=self.cfg.restart_every_n_articles, from_=0, to=500,
            step=10)
        self.restart_stepper.pack(side=LEFT, padx=8)
        tk.Label(r3, text="篇重启客户端", font=T.f(9), fg=C["text3"],
                 bg=C["surface"]).pack(side=LEFT)

        r4 = tk.Frame(b, bg=C["surface"])
        r4.pack(fill=X, padx=20)
        self.fast_var = BooleanVar(value=not self.cfg.api_read_state)
        self.fast_switch = W.Switch(r4, self.fast_var.get(), self._on_fast_toggle)
        self.fast_switch.pack(side=LEFT)
        tk.Label(r4, text="快速模式（不查历史状态）", font=T.f(9), fg=C["text2"],
                 bg=C["surface"]).pack(side=LEFT, padx=(8, 0))
        self.verify_var = BooleanVar(value=self.cfg.api_verify)
        self.verify_switch = W.Switch(r4, self.verify_var.get(),
                                      self._on_verify_toggle)
        self.verify_switch.pack(side=LEFT, padx=(24, 0))
        tk.Label(r4, text="点赞后复查", font=T.f(9), fg=C["text2"],
                 bg=C["surface"]).pack(side=LEFT, padx=(8, 0))
        p_run.fit(extra=10)

        # ---- 连接信息 ----
        p_adv = W.Panel(holder)
        p_adv.pack(fill=X, pady=(10, 0))
        self._setting_cards.append(p_adv)
        p_adv.pack_propagate(False)
        b = p_adv.body
        head = self._card_head(b, "连接信息", pad=(20, 12))
        tk.Label(head, text="来自 config.json", font=T.f(9), fg=C["text3"],
                 bg=C["surface"]).pack(side=LEFT, padx=10)
        W.Btn(head, "重新读取配置", self._reload_config, variant="secondary",
              icon="\uE72C", width=122, height=28, size=9).pack(side=RIGHT)
        W.Btn(head, "打开目录", self._open_output, variant="ghost", width=88,
              height=28, size=9, bg=C["surface"]).pack(side=RIGHT, padx=(0, 8))
        self.adv_var = StringVar(value="")
        tk.Label(b, textvariable=self.adv_var, font=(T.MONO, 9), fg=C["text2"],
                 bg=C["surface"], justify="left", anchor="w").pack(
            fill=X, padx=20, pady=(6, 0))
        p_adv.fit(extra=10)

        # ---- 本次计划 ----
        p_sum = W.Panel(holder, fill=C["accent_soft"],
                        stroke=C["accent_line"])
        p_sum.pack(fill=X, pady=(10, 0))
        self._setting_cards.append(p_sum)
        p_sum.pack_propagate(False)
        b = p_sum.body
        tk.Label(b, text="本次计划", font=T.f(11, True), fg=C["accent_hi"],
                 bg=C["accent_soft"], anchor="w").pack(fill=X, padx=20, pady=(12, 4))
        self.sum_boards = StringVar(value="")
        self.sum_plan = StringVar(value="")
        tk.Label(b, textvariable=self.sum_boards, font=T.f(9), fg=C["text"],
                 bg=C["accent_soft"], anchor="w").pack(fill=X, padx=20)
        tk.Label(b, textvariable=self.sum_plan, font=T.f(9), fg=C["text2"],
                 bg=C["accent_soft"], anchor="w").pack(fill=X, padx=20, pady=(3, 0))
        p_sum.fit(extra=10)

        self.since_entry.bind("<KeyRelease>", lambda _e: self._refresh_summary())
        self.until_entry.bind("<KeyRelease>", lambda _e: self._refresh_summary())
        self._refresh_summary()
        self.settings_area = area
        self.root.after(200, area.refresh)

    def _on_settings_resize(self, event) -> None:
        pad = max(0, event.width - 880)
        for card in getattr(self, "_setting_cards", []):
            card.pack_configure(padx=(0, pad))

    def _layout_board_chips(self, compact: bool) -> None:
        if not getattr(self, "board_chips", None):
            return
        for row in (self.chip_row1, self.chip_row2):
            for child in row.winfo_children():
                child.pack_forget()
        per_row = 2 if compact else 4
        for i, chip in enumerate(self.board_chips.values()):
            row = self.chip_row1 if i < per_row else self.chip_row2
            chip.pack(in_=row, side=LEFT, padx=(0, 8), pady=2)
        panel = getattr(self, "board_panel", None)
        if panel is not None:
            panel.fit(extra=10)

    def _board_names(self) -> list:
        names = list(self.cfg.portal_cards)
        for extra in (self.cfg.session_channel, self.cfg.dingtalk_news_name):
            if extra and extra not in names:
                names.append(extra)
        return names

    def _on_board_change(self) -> None:
        self._sync_sections()
        self._refresh_summary()

    def _on_fast_toggle(self) -> None:
        self.fast_var.set(self.fast_switch.get())
        self._refresh_summary()

    def _on_verify_toggle(self) -> None:
        self.verify_var.set(self.verify_switch.get())
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        """设置页底部那张「本次计划」：改哪一项都能立刻看到结果。"""
        if not getattr(self, "sum_boards", None):
            return
        boards = [n for n, chip in self.board_chips.items() if chip.get()]
        self.sum_boards.set(
            "板块：" + ("、".join(boards) if boards else "一个都没选 —— 跑起来不会做任何事")
        )
        since = self.since_var.get().strip() or "不限"
        until = ("今天" if self.until_now_var.get()
                 else (self.until_var.get().strip() or "不限"))
        mode = "直接点赞（快）" if self.fast_switch.get() else "查状态 + 点赞（准）"
        limit = self.limit_stepper.get()
        plan = [f"时间段 {since} ~ {until}", mode,
                "每板块不限篇数" if not limit else f"每板块 {limit} 篇"]
        if self.verify_switch.get():
            plan.append("点赞后复查")
        self.sum_plan.set(" · ".join(plan))

    def _on_range(self, key: str) -> None:
        today = date.today()
        if key == "none":
            self.since_var.set("")
        else:
            self.since_var.set((today - timedelta(days=int(key))).isoformat())
        self.until_now_var.set(True)
        self.now_chip.set(True)
        self._toggle_until()

    def _toggle_until(self) -> None:
        on = bool(self.now_chip.get())
        self.until_now_var.set(on)
        if on:
            self.until_var.set(date.today().isoformat())
        self.until_entry.configure(state="disabled" if on else "normal")
        self._refresh_summary()

    # ==================================================================
    # 状态 / 日志分发
    # ==================================================================
    def log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain(self) -> None:
        drained = 0
        while drained < 300:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.home_log.append(message)
            self.full_log.append(message)
            self._ingest(message)
            drained += 1
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self._apply_status(payload["port"], payload["running"],
                                   payload["targets"])
            elif kind == "finish":
                self._finish()
        self.root.after(80, self._drain)

    def _refresh_status(self) -> None:
        """端口探测要扫一串端口，没开客户端时要 30 秒——
        必须丢到后台线程，否则窗口一打开就白屏、像卡死。"""
        if self._status_busy:
            return
        self._status_busy = True
        self._status_event.clear()
        self.status_var.set("检测中…")
        self.status_note.set("正在确认 TBe+ 是否已接管")
        self._draw_dot(C["warn"])

        def work() -> None:
            port = None
            targets: list = []
            running = False
            try:
                port = client_mod.resolve_debug_port(self.cfg.debug_port)
                if port:
                    targets = client_mod.summarize_targets(port)
                else:
                    running = client_mod.is_client_running()
            except Exception:  # noqa: BLE001
                pass
            self.ui_queue.put(("status", {"port": port, "running": running,
                                          "targets": targets}))

        threading.Thread(target=work, daemon=True).start()

    def _apply_status(self, port, running: bool, targets: list) -> None:
        self._status_busy = False
        self._client_port = port
        self._client_running = bool(running)
        self._status_event.set()
        if port:
            self.cfg.debug_port = port
            title = (targets[0][1] if targets else "") or ""
            title = title[:16] + ("…" if len(title) > 16 else "")
            self.status_var.set("已接管")
            self.status_note.set(f"端口 {port} · 视图 {len(targets)} 个\n{title}")
            self._draw_dot(C["ok"])
        elif running:
            self.status_var.set("未接管")
            self.status_note.set("TBe+ 在运行，但没有调试端口\n点「准备」重启并接管")
            self._draw_dot(C["warn"])
        else:
            self.status_var.set("未运行")
            self.status_note.set("没检测到 TBe+ 客户端\n有 token 时可免客户端直接跑")
            self._draw_dot(C["text3"])
        self._refresh_adv()

    def _refresh_adv(self) -> None:
        token = (self.cfg.news_token or "").strip()
        token_text = (f"{token[:6]}…{token[-4:]}" if len(token) > 12
                      else ("未设置" if not token else token))
        self.adv_var.set(
            f"调试端口 {self.cfg.debug_port}    接口限速 {self.cfg.api_rate}/秒    "
            f"并发 {self.cfg.api_concurrency}    令牌 {token_text}"
        )

    def _reload_config(self) -> None:
        self.cfg = load_config()
        self.fast_switch.set(not self.cfg.api_read_state)
        self.verify_switch.set(self.cfg.api_verify)
        self.log("已重新读取 config.json")
        self._refresh_status()
        self._sync_sections()

    def _open_output(self) -> None:
        try:
            os.startfile(str(APP_DIR))  # noqa: S606  (Windows 专用)
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("程序目录", f"{APP_DIR}\n\n({exc})", parent=self.root)

    # ==================================================================
    # 板块进度
    # ==================================================================
    def _sync_sections(self) -> None:
        """按当前勾选的板块重建进度行（未开始跑时也能看到有哪几块）。"""
        if self.running:
            return
        names = [n for n, chip in self.board_chips.items() if chip.get()]
        for child in self.rows_host.winfo_children():
            child.destroy()
        self.sections = {}
        self._section_labels = []
        for name in names:
            self._add_section_row(name)
        if not names:
            tk.Label(self.rows_host, text="没有勾选任何板块，请到「任务设置」里勾选。",
                     font=T.f(9), fg=C["text3"], bg=C["surface"]).pack(
                anchor="w", pady=8)
        self.section_note.set(f"共 {len(names)} 个板块")
        self._fit_sections_panel()
        self._recalc()

    def _add_section_row(self, name: str) -> None:
        row = tk.Frame(self.rows_host, bg=C["surface"])
        row.pack(fill=X, pady=3)
        name_label = tk.Label(row, text=name, font=T.f(9), fg=C["text"],
                              bg=C["surface"], anchor="w",
                              width=15 if self._compact else 20)
        name_label.pack(side=LEFT)
        self._section_labels.append(name_label)
        bar = W.Bar(row, height=6)
        bar.pack(side=LEFT, fill=X, expand=True, padx=12)
        count = tk.Label(row, text="—", font=(T.MONO, 9), fg=C["text3"],
                         bg=C["surface"], anchor="e", width=10)
        count.pack(side=LEFT)
        flag = tk.Label(row, text="待处理", font=T.f(9), fg=C["text3"],
                        bg=C["surface"], anchor="e", width=6)
        flag.pack(side=LEFT)
        self.sections[name] = {
            "row": row, "bar": bar, "count": count, "flag": flag,
            "total": 0, "last": 0,
            "c": {"liked": 0, "already": 0, "dry": 0, "unclear": 0, "failed": 0},
        }
        self._fit_sections_panel()

    def _fit_sections_panel(self) -> None:
        self.sections_panel.fit()

    def _recalc(self) -> None:
        total_known = 0
        done = 0
        agg = {"liked": 0, "already": 0, "dry": 0, "unclear": 0, "failed": 0}
        for sec in self.sections.values():
            done += sec["last"]
            total_known += sec["total"]
            for k in agg:
                agg[k] += sec["c"][k]
        self.home_bar.set((done / total_known) if total_known else 0.0)
        self.prog_var.set(f"{done} / {total_known} 篇" if total_known else f"{done} 篇")
        self.card_processed.set(done, f"共 {total_known} 篇" if total_known else "等待运行")
        self.card_liked.set(agg["liked"], f"待点赞 {agg['dry']}" if agg["dry"] else "")
        self.card_already.set(agg["already"], "已赞过，自动跳过")
        self.card_failed.set(agg["failed"], "可重跑补齐" if agg["failed"] else "",
                             accent=C["err"] if agg["failed"] else C["text3"])

    def _ingest(self, line: str) -> None:
        """从日志里读进度：引擎只在日志里报进度，界面顺手解析出来。"""
        s = line.strip()
        m = re.match(r"^板块：(.+)$", s) or re.match(r"^板块（会话）：(.+)$", s)
        if m:
            name = m.group(1).strip()
            self.cur_section = name
            if name not in self.sections:
                self._add_section_row(name)   # 界面没勾到、引擎却跑了，补一行
            self.current_var.set(f"正在处理：{name}")
            return

        m = re.match(r"^列表里共 (\d+) 篇", s)
        if m and self.cur_section in self.sections:
            self.sections[self.cur_section]["total"] = int(m.group(1))
            self._recalc()
            return

        m = re.match(r"^\[(\d+)/(\d+)\]\s+(.+)$", s)
        if m:
            idx, total, rest = int(m.group(1)), int(m.group(2)), m.group(3)
            sec = self.sections.get(self.cur_section or "")
            if sec is None:
                return
            if total:
                sec["total"] = max(sec["total"], total)
            if idx <= sec["last"]:
                return
            sec["last"] = idx
            rest = re.sub(r"^\d{2}-\d{2}\s+", "", rest)
            kind, title = "failed", rest
            for label, k in STATUS_KINDS:
                if rest.startswith(label):
                    kind, title = k, rest[len(label):].strip()
                    break
            sec["c"][kind] = sec["c"].get(kind, 0) + 1
            sec["count"].configure(
                text=f"{idx}/{sec['total']}" if sec["total"] else str(idx),
                fg=C["text2"])
            if sec["total"]:
                sec["bar"].set(idx / sec["total"])
            tones = {"liked": C["accent"], "already": C["ok"],
                     "dry": C["warn"], "unclear": C["warn"], "failed": C["err"]}
            sec["flag"].configure(text="处理中", fg=tones.get(kind, C["text3"]))
            if title:
                self.current_var.set(title[:52] + ("…" if len(title) > 52 else ""))
            self._recalc()
            return

        if s.startswith("本板块小结") and self.cur_section in self.sections:
            sec = self.sections[self.cur_section]
            sec["flag"].configure(text="完成", fg=C["ok"])
            sec["bar"].set(1.0)
            return

        if "已手动停止" in s:
            self.current_var.set("已停止。已经点上的赞不会重复。")

    # ==================================================================
    # 计时 / 运行状态
    # ==================================================================
    def _tick(self) -> None:
        if self.running:
            sec = int(time.monotonic() - self.started_at)
            self.elapsed_var.set(f"用时 {sec // 60:02d}:{sec % 60:02d}")
        self.root.after(500, self._tick)

    def _busy(self, busy: bool) -> None:
        for button in (self.btn_attach, self.btn_dry, self.btn_run):
            button.set_enabled(not busy)
        self.btn_stop.set_enabled(busy)

    def _reset_progress(self) -> None:
        self.cur_section = None
        self._sync_sections()
        self.current_var.set("正在准备…")
        self.elapsed_var.set("用时 00:00")

    # ==================================================================
    # 任务调度
    # ==================================================================
    def _start(self, title: str, coro_factory) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在运行", "上一个任务还没结束，请稍等。",
                                parent=self.root)
            return
        self.stop_flag.clear()
        self._busy(True)
        self._reset_progress()      # 必须在 running=True 之前，否则进度行不重建
        self.running = True
        self.started_at = time.monotonic()
        self.pill.set("运行中", "run")
        self.log("")
        self.log(f"===== {title} =====")

        def worker() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coro_factory())
            except Exception as exc:  # noqa: BLE001
                self.log(f"[出错] {exc}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                self.ui_queue.put(("finish", None))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _finish(self) -> None:
        self.running = False
        self._busy(False)
        self._refresh_status()
        if self.stop_flag.is_set():
            self.pill.set("已停止", "warn")
            self.current_var.set("已停止。已经点上的赞不会重复。")
        else:
            self.pill.set("已完成", "ok")
            self.current_var.set("完成。可以再跑一次补上没成功的文章。")
        if self.started_at:
            sec = int(time.monotonic() - self.started_at)
            self.elapsed_var.set(f"用时 {sec // 60:02d}:{sec % 60:02d}")

    # ==================================================================
    # 三个按钮
    # ==================================================================
    def _on_attach(self) -> None:
        if not messagebox.askyesno(
            "准备接管",
            "这一步会关闭正在运行的 TBe+ 客户端，再带调试端口重新启动。\n\n"
            "登录状态会保留，但正在进行的对话窗口会被中断。\n\n确定继续吗？",
            parent=self.root,
        ):
            return
        self._start("准备：重启并接管 TBe+", self._attach_job)

    async def _attach_job(self) -> None:
        preferred = self.cfg.debug_port
        found = client_mod.resolve_debug_port(preferred, self.log)
        if found:
            self.cfg.debug_port = found
            if found != preferred:
                client_mod.save_debug_port(found)
            self.log(f"客户端已经处于可接管状态（端口 {found}）")
            self.log("准备完成，可以点「试运行」或「开始点赞」了。")
            return

        exe = client_mod.resolve_client_exe(self.cfg.client_exe_candidates, self.log)
        if exe is None:
            self.log("")
            self.log("没能自动找到 TBe.exe，请在接下来弹出的窗口里手动选一下。")
            self.log("一般它在 TBe+ 的安装目录里（文件夹名可能带一串数字）。")
            chosen = self._ask_client_exe()
            if not chosen:
                self.log("没有选择 TBe.exe，已取消。")
                self.log("准备失败。")
                return
            exe = Path(chosen)
            if client_mod.save_client_exe(exe, self.cfg.client_exe_candidates):
                self.log(f"已经记到 config.json，下次会自动用它：{exe}")
            self.cfg.client_exe_candidates = [str(exe)] + list(
                self.cfg.client_exe_candidates
            )

        used = client_mod.restart_client(
            exe, preferred, self.cfg.restart_wait_seconds, self.log
        )
        if not used:
            self.log("准备失败。")
            return
        self.cfg.debug_port = used
        if used != preferred:
            client_mod.save_debug_port(used)
            self.log(f"已经把端口 {used} 记到 config.json，以后会自动用它。")
        self.log(f"准备完成（调试端口 {used}），可以点「试运行」或「开始点赞」了。")

    def _ask_client_exe(self) -> str:
        """弹文件选择框。tkinter 必须在主线程调用，所以用 after + Event 转过去。"""
        result: dict = {}
        done = threading.Event()

        def ask() -> None:
            try:
                result["path"] = filedialog.askopenfilename(
                    parent=self.root,
                    title="请选择 TBe.exe（在 TBe+ 的安装目录里）",
                    filetypes=[
                        ("TBe 主程序", "TBe.exe"),
                        ("可执行文件", "*.exe"),
                        ("所有文件", "*.*"),
                    ],
                )
            finally:
                done.set()

        self.root.after(0, ask)
        done.wait(timeout=600)
        return result.get("path") or ""

    def _on_dry_run(self) -> None:
        self._start("试运行（只看不点）", lambda: self._run_job(True))

    def _on_run(self) -> None:
        if not messagebox.askyesno(
            "开始点赞",
            "将对各板块里所有未点赞的文章执行点赞。\n\n确定继续吗？",
            parent=self.root,
        ):
            return
        self._start("正式点赞", lambda: self._run_job(False))

    def _apply_settings(self) -> None:
        """把界面上的选择写回配置（引擎只认 cfg）。"""
        self.cfg.portal_cards = [
            n for n in self.cfg.portal_cards
            if self.board_chips.get(n) and self.board_chips[n].get()
        ]
        if self.board_chips.get(self.cfg.session_channel):
            self.cfg.skip_session_by_default = not self.board_chips[
                self.cfg.session_channel
            ].get()
        if self.board_chips.get(self.cfg.dingtalk_news_name):
            self.cfg.dingtalk_news = self.board_chips[
                self.cfg.dingtalk_news_name
            ].get()
        self.cfg.api_read_state = not self.fast_switch.get()
        self.cfg.api_verify = self.verify_switch.get()
        self.cfg.restart_every_n_articles = self.restart_stepper.get()

    async def _run_job(self, dry_run: bool) -> None:
        self._apply_settings()

        probe = LikeEngine(self.cfg, dry_run=True, log=self.log)
        http_ready = await probe.ensure_http()
        if not http_ready:
            # 用后台探测的结果判断，别在这里再花 30 秒把端口扫一遍
            port = self._client_port
            if port is None and self._status_busy:
                self.log("  等客户端检测结果…")
                self._status_event.wait(timeout=40)
                port = self._client_port
            if port is None:
                self.log(
                    "既没有新闻站 token，也没接管客户端。\n"
                    "  想免客户端运行：双击 命令行.bat 执行 "
                    "python cli.py --token <tbea_art_token>\n"
                    "  或者先点「准备并接管客户端」。"
                )
                return
            self.cfg.debug_port = port

        since = self.since_var.get().strip()
        until = "" if self.until_now_var.get() else self.until_var.get().strip()
        for label, value in (("开始日期", since), ("结束日期", until)):
            if value and parse_user_date(value) is None:
                self.log(f"{label}格式不对：{value!r}，请用 YYYY-MM-DD。")
                self.root.after(0, lambda lab=label, val=value: messagebox.showwarning(
                    "日期格式不对",
                    f"{lab}：{val}\n请用 YYYY-MM-DD 格式，例如 2026-09-01。",
                    parent=self.root,
                ))
                return
        self.cfg.filter_since = since
        self.cfg.filter_until = until
        if since or until:
            self.log(f"时间段：{since or '不限'} ~ {until or '今天'}")
        else:
            self.log("时间段：不限（全部文章）")

        engine = LikeEngine(
            self.cfg,
            dry_run=dry_run,
            log=self.log,
            stop_flag=self.stop_flag.is_set,
        )
        results = await engine.run(limit=self.limit_stepper.get())
        self.log("")
        self.log(summarize(results))
        if not dry_run and not self.cfg.api_read_state:
            self.log(
                "提示：本次是「快速模式」（不查历史状态），所以只报了"
                "「已确认点赞」多少篇，分不清其中多少本来就赞过；"
                "想看准确的新点赞数，把快速模式关掉。"
            )

    def _on_stop(self) -> None:
        self.stop_flag.set()
        self.pill.set("正在停止", "warn")
        self.log("已请求停止，正在结束当前文章…")

    # ==================================================================
    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                "还在运行", "任务还没结束，现在关闭会中断当前文章。\n\n确定退出吗？",
                parent=self.root,
            ):
                return
            self.stop_flag.set()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    """告诉 Windows 我们自己缩放，别把整个窗口拉伸成糊的。"""
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass


def run_ui(config: Config | None = None) -> None:
    from .app_icon import set_app_user_model_id, set_window_icon

    _enable_dpi_awareness()
    set_app_user_model_id()   # 必须在 Tk() 之前，否则任务栏显示 python 图标
    root = Tk()
    set_window_icon(root)     # 标题栏左上角 + 任务栏图标
    App(root, config or load_config())
    root.mainloop()
