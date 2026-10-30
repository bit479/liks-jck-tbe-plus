"""图形界面：无边框沉浸式外壳 + 版本入口 + 三页工作台。

结构：
    ┌ 自绘标题栏（深色，与左侧导航连成一片）──────────────┐
    │ [图标] TBe+ 新闻资讯自动点赞            —  □  ×  │
    ├ 侧边导航 ┬ 页面标题栏 ──────────────────────────────┤
    │ 概览      │ 概览 / 运行日志 / 任务设置               │
    │ 运行日志  │                                        │
    │ 任务设置  │                                        │
    └──────────┴────────────────────────────────────────┘

进入软件先选版本（老版本 TBe+ / 钉钉版 TBe+），两套板块互不干扰。

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
from . import dingtalk
from . import motion
from . import newsapi
from . import theme as T
from . import widgets as W
from .app_icon import asset
from .config import APP_DIR, Config, load_config
from .engine import LikeEngine, parse_user_date, summarize
from .theme import C

TITLEBAR_H = 42

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
CONNECT = "connect"

PAGE_TITLES = {
    HOME: "任务概览",
    LOG: "运行日志",
    SETTINGS: "任务设置",
    CONNECT: "账号与连接",
}
PAGE_CRUMBS = {
    HOME: "运行中心 / 任务概览",
    LOG: "运行中心 / 运行日志",
    SETTINGS: "配置管理 / 任务设置",
    CONNECT: "配置管理 / 账号与连接",
}

# 两个版本各自管自己的板块，互不影响
MODES = {
    "old": {
        "title": "老版本 TBe+",
        "sub": "TBe+ 桌面端 · 新闻资讯 / 开讲了 / 进出口公司新闻资讯",
        "desc": "传统 TBe+ 桌面客户端。需要重启并接管客户端，\n三个板块一次跑完，已赞过的自动跳过。",
        "tags": ["需要接管客户端", "3 个板块"],
        "boards": ["新闻资讯", "开讲了", "进出口公司新闻资讯"],
        "portal": ["新闻资讯", "开讲了"],
        "session": "进出口公司新闻资讯",
        "empty_log": "还没有日志。先点「准备并接管客户端」，再点「开始点赞」。",
    },
    "dingtalk": {
        "title": "钉钉版 TBe+",
        "sub": "钉钉版 + 新闻资讯 · 自动处理订阅资讯",
        "desc": "新版钉钉套壳客户端左侧的「新闻资讯」，是所有订阅号的\n全部资讯（一万多篇）。建议设起始日期，或用 token 免客户端。",
        "tags": ["可免客户端", "建议设起始日期"],
        "boards": ["钉钉版e+新闻资讯"],
        "portal": [],
        "session": None,
        "empty_log": "还没有日志。建议先在「任务设置」里设一个起始日期，再点「开始点赞」。",
    },
}


class RadioChips(tk.Frame):
    """一组只能选一个的胶囊。solid=True 时选中项为实心蓝底白字（日志筛选）。"""

    def __init__(self, master, options, command=None, bg=C["surface"],
                 solid: bool = False):
        super().__init__(master, bg=bg)
        self._command = command
        self.chips: dict = {}
        for key, text in options:
            chip = W.Chip(self, text, False,
                          command=lambda k=key: self.choose(k), bg=bg,
                          solid=solid)
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
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()

        self.running = False
        self.starting = False
        self.finished_ok = False
        self.started_at = 0.0
        self.last_elapsed = 0.0
        self._dt_token = ""
        self._dt_auto_tried = False
        self._dt_watch_active = False
        self._last_error = ""
        self.mode: str | None = None
        self.cur_section: str | None = None
        self.sections: dict = {}
        self.page = HOME
        motion.init(root)

        self._compact: bool | None = None
        self._resize_job = None
        self._section_labels: list = []
        self._status_busy = False
        self._client_port = None
        self._client_running = False
        self._status_event = threading.Event()
        self._status_event.set()
        self._maximized = False
        self._restore_geo = ""
        self._drag_off = None
        self._resize_info = None
        self._portal_master = list(config.portal_cards)
        self._home_intro_seen: set = set()

        sw, sh = self._work_area()
        w = min(1280, max(820, sw - 40))
        h = min(880, max(620, sh - 60))
        forced = os.environ.get("TBE_UI_SIZE", "")
        if "x" in forced:                       # 调试用：强制窗口尺寸
            try:
                w, h = (int(v) for v in forced.lower().split("x", 1))
            except ValueError:
                pass
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)

        root.title("TBE+ 自动化中心")     # 任务栏/Alt+Tab 显示的名字（不带版本号）
        root.overrideredirect(True)             # 自绘标题栏
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(min(900, w), min(640, h))
        root.configure(bg=C["nav"])
        self._imgs = {
            "ico24": tk.PhotoImage(file=str(asset("app_24.png"))),
            "ico44": tk.PhotoImage(file=str(asset("app_44.png"))),
            "ico64": tk.PhotoImage(file=str(asset("app_64.png"))),
            "mode_old": tk.PhotoImage(file=str(asset("mode_old.png"))),
            "mode_dingtalk": tk.PhotoImage(file=str(asset("mode_dingtalk.png"))),
        }

        self._build_titlebar()
        self.body = tk.Frame(root, bg=C["bg"])
        self.body.pack(fill=BOTH, expand=True)
        self._build_mode_page()
        self._build_shell()
        self._show_mode_select()

        self._install_grips()
        self._make_taskbar_window()
        root.bind("<Map>", self._on_map)
        root.bind("<Configure>", self._on_root_configure)
        root.after(80, self._drain)
        root.after(500, self._tick)
        root.after(300, self._sync_window_size)
        self._refresh_adv()
        self._refresh_status()
        root.after(60, lambda: motion.fade_window(root))

    # ==================================================================
    # 窗口外壳（无边框）
    # ==================================================================
    def _work_area(self) -> tuple:
        """真实可用桌面尺寸（去掉任务栏）。拿不到就退回 Tk 的屏幕尺寸。"""
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                    0x0030, 0, ctypes.byref(rect), 0):
                w, h = rect.right - rect.left, rect.bottom - rect.top
                if w > 200 and h > 200:
                    return w, h
        except Exception:  # noqa: BLE001
            pass
        return (self.root.winfo_screenwidth(), self.root.winfo_screenheight())

    def _make_taskbar_window(self) -> None:
        """无边框窗口默认不出现在任务栏，补一个 WS_EX_APPWINDOW。"""
        try:
            import ctypes

            u = ctypes.windll.user32
            hwnd = u.GetParent(self.root.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE,
                             (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)  # SWP_NOMOVE|NOSIZE|NOZORDER|FRAMECHANGED
        except Exception:  # noqa: BLE001
            pass

    def _sync_window_size(self) -> None:
        """把 Tk 的尺寸模型对回系统实际客户区（某些 DPI 环境会让两者不一致）。"""
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
        except Exception:  # noqa: BLE001
            pass

    def _build_titlebar(self) -> None:
        # 标题栏左右分色：左段与侧边栏同宽、同深色（放品牌，和侧边栏连成一列，
        # 品牌只出现一次）；右段浅底，放窗口按钮 —— 对齐设计稿。
        bar = tk.Frame(self.root, bg=C["bg"], height=TITLEBAR_H)
        bar.pack(fill=X)
        bar.pack_propagate(False)
        self.titlebar = bar

        dark = tk.Frame(bar, bg=C["nav"], width=T.SIDEBAR_W)
        dark.pack(side=LEFT, fill=Y)
        dark.pack_propagate(False)

        left = tk.Frame(dark, bg=C["nav"])
        left.pack(side=LEFT, padx=(16, 0), pady=(TITLEBAR_H - 24) // 2)
        logo = tk.Canvas(left, width=24, height=24, bg=C["nav"],
                         highlightthickness=0, bd=0)
        logo.pack(side=LEFT)
        W.round_rect(logo, 0, 0, 23, 23, 7, fill=C["nav_sel"],
                     outline="", width=0)
        logo.create_text(11.5, 8.5, text="TBe", font=T.f(5, True),
                         fill=C["on_dark"])
        logo.create_text(11.5, 16.5, text="+", font=T.f(7, True),
                         fill=C["on_dark"])
        tk.Label(left, text="TBE+ 自动化中心", font=T.f(10, True),
                 fg=C["nav_text"], bg=C["nav"]).pack(side=LEFT, padx=(9, 0))

        # 右侧窗口按钮（浅底，图标用深色）
        clos = W.IconBtn(bar, "\uE8BB", self._on_close, C["bg"],
                         C["surface_hover"], danger=True, fg=C["text2"])
        clos.pack(side=RIGHT, padx=(0, 9))
        self.btn_max = W.IconBtn(bar, "\uE922", self._toggle_max, C["bg"],
                                 C["surface_hover"], danger=False, fg=C["text2"])
        self.btn_max.pack(side=RIGHT)
        W.IconBtn(bar, "\uE921", self._minimize, C["bg"], C["surface_hover"],
                  danger=False, fg=C["text2"]).pack(side=RIGHT)

        for widget in (bar, dark, left, logo):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<Double-Button-1>", lambda _e: self._toggle_max())
        for child in left.winfo_children():
            child.bind("<ButtonPress-1>", self._drag_start)
            child.bind("<B1-Motion>", self._drag_move)
            child.bind("<Double-Button-1>", lambda _e: self._toggle_max())

    def _drag_start(self, event) -> None:
        self._drag_off = (event.x_root - self.root.winfo_x(),
                          event.y_root - self.root.winfo_y())

    def _drag_move(self, event) -> None:
        if self._maximized or self._drag_off is None:
            return
        x = event.x_root - self._drag_off[0]
        y = event.y_root - self._drag_off[1]
        self.root.geometry(f"+{x}+{y}")

    def _toggle_max(self) -> None:
        if self._maximized:
            if self._restore_geo:
                self.root.geometry(self._restore_geo)
            self._maximized = False
            self.btn_max.set_glyph("\uE922")
        else:
            self._restore_geo = (f"{self.root.winfo_width()}x"
                                 f"{self.root.winfo_height()}+"
                                 f"{self.root.winfo_x()}+{self.root.winfo_y()}")
            sw, sh = self._work_area()
            self.root.geometry(f"{sw}x{sh}+0+0")
            self._maximized = True
            self.btn_max.set_glyph("\uE923")
        self.root.after(80, self._debounce_compact)

    def _minimize(self) -> None:
        self.root.overrideredirect(False)
        self.root.iconify()

    def _on_map(self, _event=None) -> None:
        if self.root.state() == "normal" and not self.root.overrideredirect():
            self.root.after(30, lambda: self.root.overrideredirect(True))

    def _install_grips(self) -> None:
        """无边框窗口的边缘拖拽热区。

        热区是真实控件，颜色必须与它盖住的那块背景一致，否则会在标题栏、
        深色侧栏上露白。左右两边的高度、底边的分段都按实际布局算。
        """
        self._grips: dict = {}
        cursors = {
            "n": "size_ns", "s": "size_ns", "s_left": "size_ns",
            "w": "size_we", "w_top": "size_we",
            "e": "size_we",
            "nw": "size_nw_se", "ne": "size_ne_sw",
            "sw": "size_ne_sw", "se": "size_nw_se",
        }
        for name, cursor in cursors.items():
            frame = tk.Frame(self.root, bg=C["bg"], cursor=cursor)
            frame.bind("<ButtonPress-1>",
                       lambda e, n=name: self._resize_start(e, n))
            frame.bind("<B1-Motion>", self._resize_move)
            self._grips[name] = frame

    def _place_grips(self) -> None:
        if not getattr(self, "_grips", None):
            return
        g = 4
        w, h = self.root.winfo_width(), self.root.winfo_height()
        side = T.SIDEBAR_W if self.mode else g
        side_color = C["nav"] if self.mode else C["bg"]
        # 名称 -> (x, y, 宽, 高, 颜色)。标题栏永远深色，侧栏只在实际显示时深色。
        pos = {
            "n": (g, 0, max(0, w - 2 * g), g, C["nav"]),
            "w_top": (0, 0, g, TITLEBAR_H, C["nav"]),
            "w": (0, TITLEBAR_H, g, max(0, h - TITLEBAR_H - g), side_color),
            # 标题栏右侧不做热区：会把关闭按钮最右边 4px 压成深色
            "e": (w - g, TITLEBAR_H, g, max(0, h - TITLEBAR_H - g), C["bg"]),
            "s_left": (0, h - g, side, g, side_color),
            "s": (side, h - g, max(0, w - side - g), g, C["bg"]),
            "nw": (0, 0, g, g, C["nav"]), "ne": (w - g, 0, g, g, C["nav"]),
            "sw": (0, h - g, g, g, side_color), "se": (w - g, h - g, g, g, C["bg"]),
        }
        for name, frame in self._grips.items():
            x, y, fw, fh, color = pos[name]
            if str(frame.cget("bg")) != color:
                frame.configure(bg=color)
            frame.place(x=x, y=y, width=fw, height=fh)
            frame.lift()

    def _resize_start(self, event, edge: str) -> None:
        self._resize_info = (event.x_root, event.y_root, self.root.winfo_x(),
                             self.root.winfo_y(), self.root.winfo_width(),
                             self.root.winfo_height(), edge)

    def _resize_move(self, event) -> None:
        if not self._resize_info or self._maximized:
            return
        x0, y0, ox, oy, ow, oh, edge = self._resize_info
        dx, dy = event.x_root - x0, event.y_root - y0
        minw, minh = 900, 640
        x, y, w, h = ox, oy, ow, oh
        if "e" in edge:
            w = max(minw, ow + dx)
        if "s" in edge:
            h = max(minh, oh + dy)
        if "w" in edge:
            w = max(minw, ow - dx)
            x = ox + (ow - w)
        if "n" in edge:
            h = max(minh, oh - dy)
            y = oy + (oh - h)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ==================================================================
    # 版本入口页
    # ==================================================================
    def _build_mode_page(self) -> None:
        page = tk.Frame(self.body, bg=C["bg"])
        self.mode_page = page
        center = tk.Frame(page, bg=C["bg"])
        center.place(relx=0.5, rely=0.46, anchor="center")

        head = tk.Frame(center, bg=C["bg"])
        head.pack(pady=(0, 6))
        tk.Label(head, image=self._imgs["ico44"], bg=C["bg"], bd=0).pack()
        tk.Label(head, text="选择要运行的版本", font=T.f(15, True),
                 fg=C["text"], bg=C["bg"]).pack(pady=(10, 4))
        tk.Label(head, text="两个版本的板块与登录方式不同，请按实际使用的客户端选择。",
                 font=T.f(10), fg=C["text2"], bg=C["bg"]).pack()

        cards = tk.Frame(center, bg=C["bg"])
        cards.pack(pady=(20, 0))
        old_card = W.ModeCard(cards, self._imgs["mode_old"], "老版本 TBe+",
                              MODES["old"]["desc"], MODES["old"]["tags"],
                              command=lambda: self._enter_mode("old"))
        old_card.pack(side=LEFT, padx=(0, 14))
        dt_card = W.ModeCard(cards, self._imgs["mode_dingtalk"], "钉钉版 TBe+",
                             MODES["dingtalk"]["desc"], MODES["dingtalk"]["tags"],
                             command=lambda: self._enter_mode("dingtalk"))
        dt_card.pack(side=LEFT)
        self.mode_cards = [old_card, dt_card]

        tk.Label(center, text="进入后可以随时在「任务设置」里切换版本",
                 font=T.f(9), fg=C["text3"], bg=C["bg"]).pack(pady=(18, 0))

    def _show_mode_select(self) -> None:
        self.shell.pack_forget()
        self.mode_page.pack(fill=BOTH, expand=True)
        self._place_grips()
        self.root.after(80, self._reveal_mode_cards)

    def _reveal_mode_cards(self) -> None:
        if not motion.is_enabled():
            return
        for i, card in enumerate(getattr(self, "mode_cards", [])):
            card.reveal(delay=i * motion.STAGGER)

    def _enter_mode(self, mode: str) -> None:
        self.mode = mode
        self.mode_page.pack_forget()
        self.shell.pack(fill=BOTH, expand=True)
        self._place_grips()
        self._build_board_chips()
        self._sync_mode_actions()
        self._dt_token = newsapi.remembered_token(APP_DIR / "state.json")
        first = HOME
        if mode == "dingtalk" and not self._dt_token:
            first = CONNECT
        for name in self.nav:
            self.nav[name].set_active(name == first)
        self._show_page(first)
        self._sync_sections()
        self._refresh_summary()
        self.sub_var.set(MODES[mode]["sub"])
        self.log("")
        self.log(f"===== 已选择：{MODES[mode]['title']} =====")
        if mode == "dingtalk":
            self.log("  钉钉版只使用 TBeHome / token，不会启动旧版 TBe+。")
            if not self._dt_token and not self._dt_auto_tried:
                self._dt_auto_tried = True
                self.root.after(700, self._on_dingtalk_prepare)
        if mode not in self._home_intro_seen:
            self._home_intro_seen.add(mode)
            self.root.after(90, self._intro_home)
        self.root.after(80, self._debounce_compact)
        self._status_busy = False
        self._refresh_status()

    def _sync_mode_actions(self) -> None:
        if not getattr(self, "btn_attach", None):
            return
        if self.mode == "dingtalk":
            self._btn_attach_idle = "启动钉钉版 e+"
            self.btn_attach.set_text(self._btn_attach_idle, "\uE8A7")
            self.btn_run.set_text("开始任务", "\uE768")
            self.btn_dry.set_text("试运行 · 只看不点赞", "\uE9CE")
        else:
            self._btn_attach_idle = "准备并接管客户端"
            self.btn_attach.set_text(self._btn_attach_idle, "\uE72C")
            self.btn_run.set_text("开始点赞", "\uE768")
            self.btn_dry.set_text(self._btn_dry_idle, "\uE9CE")

    def _intro_home(self) -> None:
        """概览首屏分层出现：统计卡以极短交错淡入，只播一次。"""
        if not motion.is_enabled():
            return
        for i, card in enumerate(getattr(self, "_cards", [])):
            card.reveal(delay=i * motion.STAGGER)

    def _switch_mode(self) -> None:
        if self.running:
            messagebox.showinfo("正在运行", "任务进行中不能切换版本，请先停止。",
                                parent=self.root)
            return
        if not messagebox.askyesno(
                "切换版本",
                "切换后当前勾选的板块与进度会重置，已点过的赞不受影响。\n\n继续吗？",
                parent=self.root):
            return
        self.mode = None
        self.cur_section = None
        self.sections = {}
        self._show_mode_select()

    # ==================================================================
    # 主骨架（侧边栏 + 标题栏 + 页面）
    # ==================================================================
    def _build_shell(self) -> None:
        shell = tk.Frame(self.body, bg=C["bg"])
        self.shell = shell

        bar = tk.Frame(shell, bg=C["nav"], width=T.SIDEBAR_W)
        bar.pack(side=LEFT, fill=Y)
        bar.pack_propagate(False)
        self.sidebar = bar

        self.nav: dict = {}
        # 品牌已在顶部标题栏统一展示，这里不再重复（原先这里会再画一遍
        # logo + "TBE+ 自动化中心"，导致左上角出现两个图标两个标题）。
        top = tk.Frame(bar, bg=C["nav"])
        top.pack(fill=X, pady=(10, 0))
        for group, items in (
            ("运行中心", ((HOME, "任务概览", "\uE80F"), (LOG, "运行日志", "\uE81C"))),
            ("配置管理", ((SETTINGS, "任务设置", "\uE713"),
                          (CONNECT, "账号与连接", "\uE77B"))),
        ):
            tk.Label(top, text=group, font=T.f(8), fg=C["nav_group"], bg=C["nav"],
                     anchor="w").pack(fill=X, padx=22, pady=(10, 4))
            box = tk.Frame(top, bg=C["nav"])
            box.pack(fill=X, padx=14)
            for key, text, glyph in items:
                item = W.NavItem(box, text, glyph,
                                 command=lambda k=key: self._show_page(k),
                                 active=(key == HOME))
                item.pack(fill=X, pady=1)
                self.nav[key] = item

        footer = tk.Frame(bar, bg=C["nav"])
        footer.pack(side="bottom", fill=X, padx=14, pady=14)

        card = W.Panel(footer, radius=9, fill=C["nav_active"],
                       stroke=C["nav_line"], bg=C["nav"])
        card.pack(fill=X)
        self.status_card = card
        body = card.body
        head = tk.Frame(body, bg=C["nav_active"])
        head.pack(fill=X, padx=12, pady=(9, 0))
        tk.Label(head, text="客户端状态", font=T.f(8), fg=C["nav_muted"],
                 bg=C["nav_active"], anchor="w").pack(side=LEFT)
        W.Btn(head, "", lambda: self._refresh_status(), variant="ghost",
              icon="\uE72C", width=22, height=19, pad=3, bg=C["nav_active"],
              ).pack(side=RIGHT)
        line = tk.Frame(body, bg=C["nav_active"])
        line.pack(fill=X, padx=12, pady=(5, 0))
        self.dot = tk.Canvas(line, width=9, height=9, bg=C["nav_active"],
                             highlightthickness=0, bd=0)
        self.dot.pack(side=LEFT)
        self._draw_dot(C["text3"])
        self.status_var = StringVar(value="正在检查…")
        tk.Label(line, textvariable=self.status_var, font=T.f(10, True),
                 fg=C["on_dark"], bg=C["nav_active"], anchor="w").pack(
            side=LEFT, padx=(6, 0))
        self.status_note = StringVar(value="\u00a0\n\u00a0")
        tk.Label(body, textvariable=self.status_note, font=T.f(8),
                 fg=C["nav_muted"], bg=C["nav_active"], anchor="w",
                 justify="left", wraplength=166).pack(
            fill=X, padx=12, pady=(4, 2))
        card.fit(extra=10)

        W.Btn(footer, "切换版本", self._switch_mode, variant="ghost",
              icon="\uE8AB", width=T.NAV_W, height=30, size=9,
              bg=C["nav"]).pack(fill=X, pady=(8, 0))
        W.Btn(footer, "打开程序目录", self._open_output, variant="ghost",
              icon="\uE8B7", width=T.NAV_W, height=30, size=9,
              bg=C["nav"]).pack(fill=X, pady=(2, 0))

        self.main = tk.Frame(shell, bg=C["bg"])
        self.main.pack(side=LEFT, fill=BOTH, expand=True)
        self._build_header()
        self._build_pages()

    def _draw_dot(self, color: str) -> None:
        self.dot.delete("all")
        self.dot.create_oval(1, 1, 8, 8, fill=color, outline="")

    def _build_header(self) -> None:
        # 高度按内容定：16pt 标题在 125% 缩放下行高约 42px，留足两行的空间
        header = tk.Frame(self.main, bg=C["bg"], height=94)
        header.pack(fill=X)
        header.pack_propagate(False)
        inner = tk.Frame(header, bg=C["bg"])
        inner.pack(fill=BOTH, expand=True, padx=T.GAP_L, pady=(12, 10))

        left = tk.Frame(inner, bg=C["bg"])
        left.pack(side=LEFT)
        self.crumb_var = StringVar(value=PAGE_CRUMBS[HOME])
        tk.Label(left, textvariable=self.crumb_var, font=T.f(8), fg=C["text3"],
                 bg=C["bg"], anchor="w").pack(anchor="w")
        self.title_var = StringVar(value="任务概览")
        self.sub_var = StringVar(value="")
        tk.Label(left, textvariable=self.title_var, font=T.f(18, True),
                 fg=C["text"], bg=C["bg"], anchor="w").pack(anchor="w", pady=(2, 0))
        tk.Label(left, textvariable=self.sub_var, font=T.f(9), fg=C["text2"],
                 bg=C["bg"], anchor="w").pack(anchor="w", pady=(3, 0))

        right = tk.Frame(inner, bg=C["bg"])
        right.pack(side=RIGHT)
        self.pill = W.StatusPill(right, "空闲", "idle", bg=C["bg"])
        self.pill.pack(side=LEFT)
        tk.Frame(right, bg=C["stroke"], width=1, height=18).pack(
            side=LEFT, fill=Y, padx=12)
        self.elapsed_var = StringVar(value="")
        tk.Label(right, textvariable=self.elapsed_var, font=(T.MONO, 9),
                 fg=C["text2"], bg=C["bg"]).pack(side=LEFT)
        tk.Label(right, text="\uE77B", font=T.icon(11), fg=C["text2"],
                 bg=C["accent_soft"], width=2).pack(side=LEFT, padx=(12, 0),
                                                    ipady=3)
        W.Btn(right, "", lambda: self._show_page(SETTINGS), variant="ghost",
              icon="\uE713", width=30, height=28, pad=4, bg=C["bg"]).pack(
            side=LEFT, padx=(10, 0))

    def _build_pages(self) -> None:
        host = tk.Frame(self.main, bg=C["bg"])
        host.pack(fill=BOTH, expand=True, padx=T.GAP_L, pady=(0, T.GAP_L))
        self.page_host = host
        self.pages: dict = {}
        for key, builder in ((HOME, self._page_home), (LOG, self._page_log),
                             (SETTINGS, self._page_settings),
                             (CONNECT, self._page_connect)):
            frame = tk.Frame(host, bg=C["bg"])
            self.pages[key] = frame
            builder(frame)

    def _show_page(self, key: str, animate: bool = True) -> None:
        self.page = key
        for name, frame in self.pages.items():
            if name == key:
                frame.place(x=0, y=0, relwidth=1, relheight=1)
                if animate:
                    motion.slide_in(frame)
            else:
                frame.place_forget()
        for name, item in self.nav.items():
            item.set_active(name == key)
        self.title_var.set(PAGE_TITLES[key])
        self.crumb_var.set(PAGE_CRUMBS[key])
        if key == CONNECT:
            self._refresh_connect()
        self.root.after(60, self._debounce_compact)

    # ==================================================================
    # 自适应
    # ==================================================================
    def _on_root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        self._place_grips()
        self._debounce_compact()

    def _on_page_configure(self, _event) -> None:
        self._debounce_compact()
        self.root.after_idle(self._layout_vertical)

    def _layout_vertical(self) -> None:
        """窗口矮时先保核心：收起「处理进度/运行质量」，把高度留给日志。"""
        mid = getattr(self, "mid_row", None)
        logp = getattr(self, "log_panel", None)
        if mid is None or logp is None:
            return
        host = getattr(self, "page_host", None)
        if host is None:
            return
        tight = host.winfo_height() < 620
        managed = bool(mid.winfo_manager())
        if tight and managed:
            mid.pack_forget()
        elif not tight and not managed:
            mid.pack(fill=X, pady=(10, 0), before=logp)

    def _debounce_compact(self) -> None:
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(120, self._sync_compact)

    def _sync_compact(self) -> None:
        width = self._cards_host.winfo_width()
        if width <= 1:
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
        if getattr(self, "btn_attach", None):
            if compact:
                if self.btn_attach.winfo_manager():
                    self.btn_attach.pack_forget()
            elif not self.btn_attach.winfo_manager():
                self.btn_attach.pack(side=LEFT)   # 追加到按钮组最右
        if getattr(self, "log_subtitle", None):
            if compact:
                self.log_subtitle.pack_forget()
                self.home_quick.pack_forget()
            else:
                if not self.log_subtitle.winfo_manager():
                    self.log_subtitle.pack(side=LEFT, padx=10)
                if not self.home_quick.winfo_manager():
                    self.home_quick.pack(side=RIGHT, padx=(0, 8),
                                         before=self.pause_chip)

    def _layout_cards(self, compact: bool) -> None:
        # 仪表盘固定四列：窄窗口下变成 2x2 会把下面日志挤没，
        # 150px 宽仍能放下数字与 /总数，说明文字在卡片里自适应隐藏。
        cols = 4
        for card in self._cards:
            card.grid_forget()
        for c in range(4):
            self._cards_host.grid_columnconfigure(
                c, weight=1 if c < cols else 0, uniform="stat" if c < cols else "")
        for i, card in enumerate(self._cards):
            r, c = divmod(i, cols)
            card.grid(row=r, column=c, sticky="nsew",
                      padx=(0 if c == 0 else 6, 0 if c == cols - 1 else 6),
                      pady=0)

    def _layout_actions(self, compact: bool) -> None:
        self._btn_attach_idle = "准备接管" if compact else "准备并接管客户端"
        self._btn_dry_idle = "试运行" if compact else "试运行 · 只看不点赞"
        if not self.running:
            self.btn_attach.set_text(self._btn_attach_idle, "\uE72C")
            self.btn_dry.set_text(self._btn_dry_idle, "\uE9CE")

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

        # ---------------- 核心指标 ----------------
        cards = tk.Frame(f, bg=C["bg"])
        cards.pack(fill=X)
        self.card_processed = W.StatCard(cards, "已处理", C["slate"], "\uE73E")
        self.card_liked = W.StatCard(cards, "新点赞", C["accent"], "\uEB51")
        self.card_already = W.StatCard(cards, "原本已赞", C["ok"], "\uEB52")
        self.card_failed = W.StatCard(cards, "未成功", C["err"], "\uE7BA")
        self._cards = [self.card_processed, self.card_liked,
                       self.card_already, self.card_failed]
        self._cards_host = cards
        self._layout_cards(False)

        # ---------------- 执行任务 ----------------
        panel = W.Panel(f)
        panel.pack(fill=X, pady=(10, 0))
        panel.pack_propagate(False)
        self.exec_panel = panel
        body = panel.body
        head = self._card_head(body, "执行任务", pad=(20, 8))
        self.stage_var = StringVar(value="就绪")
        tk.Label(head, textvariable=self.stage_var, font=T.f(9), fg=C["text3"],
                 bg=C["surface"]).pack(side=RIGHT)

        row = tk.Frame(body, bg=C["surface"])
        row.pack(fill=X, padx=20, pady=(4, 0))
        left = tk.Frame(row, bg=C["surface"])
        left.pack(side=LEFT, fill=X, expand=True)
        line = tk.Frame(left, bg=C["surface"])
        line.pack(fill=X)
        self.run_state = tk.Label(line, text="● 就绪", font=T.f(9),
                                  fg=C["text3"], bg=C["surface"], anchor="w")
        self.run_state.pack(side=LEFT)
        self.prog_note = StringVar(value="等待开始")
        tk.Label(line, textvariable=self.prog_note, font=T.f(9), fg=C["text3"],
                 bg=C["surface"]).pack(side=RIGHT, padx=(10, 0))
        self.home_bar = W.Bar(line, height=8)
        self.home_bar.pack(side=LEFT, fill=X, expand=True, padx=(14, 0))
        self.prog_var = StringVar(value="0 / 0 篇")
        tk.Label(left, textvariable=self.prog_var, font=T.f(16, True),
                 fg=C["text"], bg=C["surface"], anchor="w").pack(
            anchor="w", pady=(6, 0))

        right = tk.Frame(row, bg=C["surface"])
        right.pack(side=RIGHT, padx=(16, 0))
        self._btn_attach_idle = "准备并接管客户端"
        self._btn_dry_idle = "试运行 · 只看不点赞"
        self.btn_run = W.Btn(right, "开始点赞", self._on_run, variant="primary",
                             icon="\uE768", bold=True, width=128, height=34,
                             size=9)
        self.btn_run.pack(side=LEFT)
        self.btn_dry = W.Btn(right, self._btn_dry_idle, self._on_dry_run,
                             variant="outline", icon="\uE9CE", height=34,
                             size=9)
        self.btn_dry.pack(side=LEFT, padx=(8, 0))
        self.btn_stop = W.Btn(right, "停止", self._on_stop, variant="danger",
                              icon="\uE71A", width=64, height=34, size=9)
        self.btn_stop.pack(side=LEFT, padx=(8, 0))
        self.btn_stop.set_enabled(False)
        self.btn_attach = W.Btn(right, self._btn_attach_idle, self._on_prepare,
                                variant="secondary", icon="\uE72C", height=34,
                                size=9)
        self.btn_attach.pack(side=LEFT, padx=(8, 0))

        self.steps = W.Steps(body, ("连接客户端", "读取资讯", "执行点赞"))
        self.steps.pack(fill=X, padx=20, pady=(4, 0))

        self.sections_strip = tk.Frame(body, bg=C["surface"])
        self.sections_strip.pack(fill=X, padx=20, pady=(0, 0))

        cur = tk.Frame(body, bg=C["surface"])
        cur.pack(fill=X, padx=20, pady=(2, 6))
        tk.Label(cur, text="\uE7C3", font=T.icon(8), fg=C["text3"],
                 bg=C["surface"]).pack(side=LEFT)
        self.current_var = StringVar(value="还没有开始，点「开始点赞」或先「试运行」。")
        tk.Label(cur, textvariable=self.current_var, font=T.f(9), fg=C["text2"],
                 bg=C["surface"], anchor="w").pack(side=LEFT, padx=(8, 0),
                                                  fill=X, expand=True)
        self.current_time = StringVar(value="")
        tk.Label(cur, textvariable=self.current_time, font=(T.MONO, 8),
                 fg=C["text3"], bg=C["surface"]).pack(side=RIGHT)
        panel.fit(extra=4)

        # ---------------- 处理进度 + 运行质量 ----------------
        mid = tk.Frame(f, bg=C["bg"], height=136)
        mid.pack(fill=X, pady=(10, 0))
        mid.pack_propagate(False)
        self.mid_row = mid

        p1 = W.Panel(mid)
        p1.pack(side=LEFT, fill=BOTH, expand=True)
        b1 = p1.body
        self._card_head(b1, "处理进度", "按已处理文章的实际结果分布", pad=(18, 12))
        self.seg = W.SegBar(b1, height=14)
        self.seg.pack(fill=X, padx=18, pady=(10, 0))
        legend = tk.Frame(b1, bg=C["surface"])
        legend.pack(fill=X, padx=18, pady=(12, 0))
        self.legend_vars = {}
        for key, label, color in (
            ("liked", "已点赞", C["ok"]),
            ("already", "原本已赞", C["slate"]),
            ("pending", "待处理", C["stroke_hi"]),
            ("failed", "失败", C["err"]),
        ):
            cell = tk.Frame(legend, bg=C["surface"])
            cell.pack(side=LEFT, expand=True, fill=X)
            dot = tk.Canvas(cell, width=8, height=8, bg=C["surface"],
                            highlightthickness=0, bd=0)
            dot.create_oval(1, 1, 7, 7, fill=color, outline="")
            dot.pack(side=LEFT)
            var = StringVar(value="0")
            tk.Label(cell, text=label, font=T.f(8), fg=C["text3"],
                     bg=C["surface"]).pack(side=LEFT, padx=(6, 0))
            tk.Label(cell, textvariable=var, font=T.f(11, True), fg=C["text"],
                     bg=C["surface"]).pack(side=LEFT, padx=(6, 0))
            self.legend_vars[key] = var
        p1.fit(extra=6)

        p2 = W.Panel(mid, width=318)
        p2.pack(side=RIGHT, fill=Y, padx=(10, 0))
        p2.pack_propagate(False)
        b2 = p2.body
        self._card_head(b2, "运行质量", "取真实运行结果", pad=(18, 12))
        qrow = tk.Frame(b2, bg=C["surface"])
        qrow.pack(fill=BOTH, expand=True, padx=18, pady=(12, 0))
        self.quality_vars = {}
        for i, (key, label, glyph) in enumerate((
            ("rate", "成功率", "\uE73E"),
            ("speed", "平均耗时", "\uE916"),
            ("batch", "当前批次", "\uE8EF"),
        )):
            if i:
                tk.Frame(qrow, bg=C["stroke"], width=1).pack(
                    side=LEFT, fill=Y, padx=8)
            col = tk.Frame(qrow, bg=C["surface"])
            col.pack(side=LEFT, expand=True, fill=X)
            tk.Label(col, text=glyph, font=T.icon(9), fg=C["accent"],
                     bg=C["surface"], anchor="w").pack(anchor="w")
            var = StringVar(value="—")
            tk.Label(col, textvariable=var, font=T.f(14, True), fg=C["text"],
                     bg=C["surface"], anchor="w").pack(anchor="w", pady=(4, 0))
            tk.Label(col, text=label, font=T.f(8), fg=C["text3"],
                     bg=C["surface"], anchor="w").pack(anchor="w")
            self.quality_vars[key] = var

        # ---------------- 实时日志 ----------------
        logp = W.Panel(f)
        logp.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.log_panel = logp
        lb = logp.body
        lhead = self._card_head(lb, "实时日志", pad=(18, 10))
        self.log_subtitle = tk.Label(lhead, text="连接、读取、点赞全过程",
                                     font=T.f(9), fg=C["text3"], bg=C["surface"])
        self.log_subtitle.pack(side=LEFT, padx=10)
        W.Btn(lhead, "查看全部日志", lambda: self._show_page(LOG),
              variant="link", icon="\uE76C", height=26, size=9,
              pad=6, bg=C["surface"]).pack(side=RIGHT)
        self.pause_chip = W.Switch(lhead, False, command=self._on_pause_toggle,
                                   bg=C["surface"])
        self.pause_chip.pack(side=RIGHT, padx=(0, 10))
        tk.Label(lhead, text="暂停滚动", font=T.f(9), fg=C["text2"],
                 bg=C["surface"]).pack(side=RIGHT, padx=(0, 6))
        self.log_search_var = StringVar(value="")
        search = W.entry(lhead, self.log_search_var, width=18, size=9)
        search.pack(side=RIGHT, padx=(0, 6), ipady=3)
        self._log_search_ph(search, "搜索资讯标题或内容…")
        search.bind("<KeyRelease>", lambda _e: self._on_log_search())
        tk.Label(lhead, text="\uE721", font=T.icon(8), fg=C["text3"],
                 bg=C["surface"]).pack(side=RIGHT, padx=(0, 4))
        self.home_quick = RadioChips(
            lhead, (("all", "全部"), ("key", "关键"), ("issue", "问题")),
            command=self._on_home_filter, solid=True)
        self.home_quick.pack(side=RIGHT, padx=(0, 8))
        self.home_quick.choose("all", notify=False)

        # 列头与 Text 的制表位对齐
        colhead = tk.Canvas(lb, bg=C["term"], height=20,
                            highlightthickness=0, bd=0)
        colhead.pack(fill=X, padx=(0, 10), pady=(4, 0))
        for text, x in (("时间", 0), ("级别", 64), ("进度", 118),
                        ("状态", 176), ("资讯标题", 276)):
            colhead.create_text(x, 10, text=text, font=T.f(8),
                                fill=C["text3"], anchor="w")

        self.home_log = W.LogTable(lb, empty_title="还没有日志",
                                   empty_hint="点「开始点赞」或「试运行」，这里会显示每篇资讯")
        self.home_log.pack(fill=BOTH, expand=True, padx=(0, 6), pady=(0, 6))

        self.footer_var = StringVar(value="")
        tk.Label(f, textvariable=self.footer_var, font=T.f(8), fg=C["text3"],
                 bg=C["bg"], anchor="w").pack(fill=X, pady=(4, 0))

    def _log_search_ph(self, box, placeholder: str) -> None:
        """搜索框占位文案：空且未聚焦时显示灰色提示。"""
        state = {"ph": False}

        def show(_e=None):
            if not self.log_search_var.get():
                state["ph"] = True
                box.configure(fg=C["text3"])
                self.log_search_var.set(placeholder)

        def hide(_e=None):
            if state["ph"]:
                state["ph"] = False
                self.log_search_var.set("")
            box.configure(fg=C["text"])

        def on_press(_e):
            if state["ph"]:
                hide()

        box.bind("<FocusIn>", hide, add="+")
        box.bind("<FocusOut>", lambda e: (show() if not self.log_search_var.get()
                                          else None), add="+")
        box.bind("<KeyPress>", on_press, add="+")
        show()

    def _on_log_search(self) -> None:
        q = self.log_search_var.get()
        self.home_log.set_search("" if q == "搜索资讯标题或内容…" else q)

    def _on_pause_toggle(self) -> None:
        on = self.pause_chip.get()
        self.home_log.set_autoscroll(not on)
        self.full_log.set_autoscroll(not on)

    def _on_home_filter(self, key: str) -> None:
        self.home_log.set_mode(key)
        self.log_quick.choose(key, notify=False)
        self.full_log.set_mode(key)

    # ==================================================================
    # 页：日志
    # ==================================================================
    def _page_log(self, f: tk.Frame) -> None:
        panel = W.Panel(f)
        panel.pack(fill=BOTH, expand=True)
        body = panel.body

        head = self._card_head(body, "运行日志", pad=(20, 14))
        W.Btn(head, "清空", self._clear_log, variant="ghost", width=62,
              height=28, size=9, bg=C["surface"]).pack(side=RIGHT)
        W.Btn(head, "复制", self._copy_log, variant="secondary",
              icon="\uE8C8", width=76, height=28, size=9).pack(
            side=RIGHT, padx=(0, 8))
        W.Btn(head, "导出", self._export_log, variant="secondary", icon="\uE896",
              width=80, height=28, size=9).pack(side=RIGHT, padx=(0, 8))
        self.log_quick = RadioChips(
            head, (("all", "全部"), ("key", "只看关键"), ("issue", "只看问题")),
            command=self._on_log_filter, solid=True)
        self.log_quick.pack(side=RIGHT, padx=(0, 14))
        self.log_quick.choose("all", notify=False)

        W.divider(body).pack(fill=X, padx=20, pady=(10, 0))
        self.full_log = W.LogView(
            body, empty_title="还没有日志",
            empty_hint="回到概览页点「开始点赞」，这里会记录全过程")
        self.full_log.pack(fill=BOTH, expand=True, padx=10, pady=8)

    def _on_log_filter(self, key: str) -> None:
        self.full_log.set_mode(key)
        self.home_quick.choose(key, notify=False)
        self.home_log.set_mode(key)

    def _clear_log(self) -> None:
        self.full_log.clear()
        self.home_log.clear()

    def _copy_log(self) -> None:
        text = self.full_log.copy_visible()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log(f"已复制 {len(text.splitlines())} 行日志到剪贴板")

    def _export_log(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出运行日志", defaultextension=".txt",
            initialfile=f"点赞日志-{date.today():%Y%m%d}.txt",
            filetypes=[("文本文件", "*.txt")])
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
        area = W.ScrollArea(f)
        area.pack(fill=BOTH, expand=True)
        holder = area.inner
        holder.bind("<Configure>", self._on_settings_resize)
        self._setting_cards: list = []
        self.settings_area = area

        # ---- 版本 ----
        p_ver = W.Panel(holder, fill=C["accent_soft"], stroke=C["accent_line"])
        p_ver.pack(fill=X)
        self._setting_cards.append(p_ver)
        b = p_ver.body
        head = self._card_head(b, "版本", bg=C["accent_soft"], pad=(20, 12))
        self.mode_label = StringVar(value="")
        tk.Label(head, textvariable=self.mode_label, font=T.f(9),
                 fg=C["accent_hi"], bg=C["accent_soft"]).pack(side=LEFT, padx=10)
        W.Btn(head, "切换版本", self._switch_mode, variant="secondary",
              icon="\uE8AB", width=104, height=26, size=9).pack(side=RIGHT)
        self.ver_var = StringVar(value=f"TBE+ 自动化中心  v{VERSION}")
        tk.Label(b, textvariable=self.ver_var, font=(T.MONO, 9), fg=C["text2"],
                 bg=C["accent_soft"], anchor="w").pack(fill=X, padx=20,
                                                       pady=(4, 12))
        p_ver.fit(extra=10)

        # ---- 新闻时间段 ----
        p_date = W.Panel(holder)
        p_date.pack(fill=X, pady=(10, 0))
        self._setting_cards.append(p_date)
        p_date.pack_propagate(False)
        b = p_date.body
        head = self._card_head(b, "新闻时间段")
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
            command=self._on_range)
        self.range_quick.pack(anchor="w", padx=20, pady=(0, 12))
        self.range_quick.choose("none", notify=False)
        p_date.fit()

        # ---- 板块 ----
        p_board = W.Panel(holder)
        p_board.pack(fill=X, pady=(10, 0))
        self._setting_cards.append(p_board)
        p_board.pack_propagate(False)
        b = p_board.body
        head = self._card_head(b, "板块")
        tk.Label(head, text="取消勾选 = 本次跳过", font=T.f(9), fg=C["text3"],
                 bg=C["surface"]).pack(side=LEFT, padx=10)
        rowb = tk.Frame(b, bg=C["surface"])
        rowb.pack(fill=X, padx=20, pady=(10, 0))
        self.chip_row1 = tk.Frame(rowb, bg=C["surface"])
        self.chip_row1.pack(fill=X)
        self.chip_row2 = tk.Frame(rowb, bg=C["surface"])
        self.chip_row2.pack(fill=X)
        self.board_chips: dict = {}
        self.board_panel = p_board

        # ---- 运行参数 ----
        p_run = W.Panel(holder)
        p_run.pack(fill=X, pady=(10, 0))
        self._setting_cards.append(p_run)
        p_run.pack_propagate(False)
        b = p_run.body
        head = self._card_head(b, "运行参数")
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
            r3, value=self.cfg.restart_every_n_articles, from_=0, to=500, step=10)
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
        head = self._card_head(b, "连接信息")
        tk.Label(head, text="来自 config.json", font=T.f(9), fg=C["text3"],
                 bg=C["surface"]).pack(side=LEFT, padx=10)
        W.Btn(head, "重新读取配置", self._reload_config, variant="secondary",
              icon="\uE72C", width=122, height=26, size=9).pack(side=RIGHT)
        W.Btn(head, "打开目录", self._open_output, variant="ghost", width=88,
              height=26, size=9, bg=C["surface"]).pack(side=RIGHT, padx=(0, 8))
        self.adv_var = StringVar(value="")
        tk.Label(b, textvariable=self.adv_var, font=(T.MONO, 9), fg=C["text2"],
                 bg=C["surface"], justify="left", anchor="w").pack(
            fill=X, padx=20, pady=(6, 12))
        p_adv.fit(extra=10)

        # ---- 本次计划 ----
        p_sum = W.Panel(holder, fill=C["surface_alt"], stroke=C["stroke"])
        p_sum.pack(fill=X, pady=(10, 0))
        self._setting_cards.append(p_sum)
        p_sum.pack_propagate(False)
        b = p_sum.body
        tk.Label(b, text="本次计划", font=T.f(11, True), fg=C["text2"],
                 bg=C["surface_alt"], anchor="w").pack(fill=X, padx=20, pady=(10, 2))
        self.sum_boards = StringVar(value="")
        self.sum_plan = StringVar(value="")
        tk.Label(b, textvariable=self.sum_boards, font=T.f(9), fg=C["text"],
                 bg=C["surface_alt"], anchor="w").pack(fill=X, padx=20)
        tk.Label(b, textvariable=self.sum_plan, font=T.f(9), fg=C["text2"],
                 bg=C["surface_alt"], anchor="w").pack(fill=X, padx=20, pady=(3, 0))
        p_sum.fit(extra=10)

        self.since_entry.bind("<KeyRelease>", lambda _e: self._refresh_summary())
        self.until_entry.bind("<KeyRelease>", lambda _e: self._refresh_summary())
        self.root.after(200, area.refresh)

    # ==================================================================
    # 页：账号与连接
    # ==================================================================
    def _page_connect(self, f: tk.Frame) -> None:
        area = W.ScrollArea(f)
        area.pack(fill=BOTH, expand=True)
        holder = area.inner
        self.connect_area = area

        # ---- 状态总览 ----
        p_stat = W.Panel(holder)
        p_stat.pack(fill=X)
        self.connect_stat = p_stat
        b = p_stat.body
        self._card_head(b, "连接概览", "版本与登录状态", pad=(20, 12))
        grid = tk.Frame(b, bg=C["surface"])
        grid.pack(fill=X, padx=20, pady=(8, 2))
        self.connect_vars = {}
        for i, (key, label) in enumerate((
            ("mode", "当前版本"),
            ("client", "客户端"),
            ("token", "新闻站 token"),
            ("way", "执行方式"),
        )):
            r, c = divmod(i, 2)
            cell = tk.Frame(grid, bg=C["surface"])
            cell.grid(row=r, column=c, sticky="w", padx=(0, 30), pady=5)
            tk.Label(cell, text=label, font=T.f(8), fg=C["text3"],
                     bg=C["surface"], width=10, anchor="w").pack(side=LEFT)
            var = StringVar(value="—")
            tk.Label(cell, textvariable=var, font=T.f(9), fg=C["text"],
                     bg=C["surface"], anchor="w").pack(side=LEFT)
            self.connect_vars[key] = var
        tk.Label(b, text="钉钉版只使用新版客户端的 token，绝不启动、也绝不读取旧版 TBe+ 客户端。",
                 font=T.f(8), fg=C["warn"], bg=C["surface"], anchor="w").pack(
            fill=X, padx=20, pady=(6, 12))
        p_stat.fit(extra=8)

        # ---- 钉钉版引导 ----
        self.connect_dt = W.Panel(holder, fill=C["accent_soft"],
                                  stroke=C["accent_line"])
        self.connect_dt.pack(fill=X, pady=(10, 0))
        b = self.connect_dt.body
        self._card_head(b, "钉钉版 e+ 使用引导", "首次使用只需做一次", bg=C["accent_soft"],
                        pad=(20, 12))
        steps = tk.Frame(b, bg=C["accent_soft"])
        steps.pack(fill=X, padx=20, pady=(6, 0))
        self.dt_steps = []
        for i, text in enumerate((
            "点击「一键准备」，程序自动启动钉钉版 e+（已启动则直接复用）。",
            "如果还没有登录态，在弹出的 e+ 里打开「新闻资讯」列表一次。",
            "程序每秒检查一次 token，读到后自动接管并显示绿色「已接管」。",
        )):
            row = tk.Frame(steps, bg=C["accent_soft"])
            row.pack(fill=X, pady=3)
            tk.Label(row, text=str(i + 1), font=T.f(8, True), fg=C["on_dark"],
                     bg=C["accent"], width=2).pack(side=LEFT)
            lab = tk.Label(row, text=text, font=T.f(9), fg=C["text2"],
                           bg=C["accent_soft"], anchor="w")
            lab.pack(side=LEFT, padx=(8, 0), fill=X, expand=True)
            self.dt_steps.append(lab)
        self.dt_state_var = StringVar(value="等待准备")
        tk.Label(b, textvariable=self.dt_state_var, font=T.f(9), fg=C["accent_hi"],
                 bg=C["accent_soft"], anchor="w").pack(fill=X, padx=20, pady=(8, 0))
        brow = tk.Frame(b, bg=C["accent_soft"])
        brow.pack(fill=X, padx=20, pady=(8, 12))
        self.btn_dt_prepare = W.Btn(brow, "一键准备（启动并接管）",
                                    self._on_dingtalk_prepare,
                                    variant="primary", icon="\uE768",
                                    height=34, size=9, width=190)
        self.btn_dt_prepare.pack(side=LEFT)
        W.Btn(brow, "打开新闻资讯", self._on_open_news, variant="secondary",
              icon="\uE8A7", height=34, size=9).pack(side=LEFT, padx=(8, 0))
        W.Btn(brow, "打开客户端目录", self._open_home_dir, variant="ghost",
              height=34, size=9, bg=C["accent_soft"]).pack(side=LEFT, padx=(8, 0))
        self.connect_dt.fit(extra=8)

        # ---- 旧版连接（只在老版本模式显示） ----
        self.connect_old = W.Panel(holder)
        self.connect_old.pack(fill=X, pady=(10, 0))
        b = self.connect_old.body
        self._card_head(b, "老版本 TBe+ 接管", "仅老版本模式使用", pad=(20, 12))
        self.old_state_var = StringVar(value="等待检测")
        tk.Label(b, textvariable=self.old_state_var, font=T.f(9), fg=C["text2"],
                 bg=C["surface"], anchor="w").pack(fill=X, padx=20, pady=(6, 0))
        orow = tk.Frame(b, bg=C["surface"])
        orow.pack(fill=X, padx=20, pady=(10, 12))
        W.Btn(orow, "准备并接管客户端", self._on_attach, variant="secondary",
              icon="\uE72C", height=32, size=9).pack(side=LEFT)
        W.Btn(orow, "打开程序目录", self._open_output, variant="ghost",
              height=32, size=9, bg=C["surface"]).pack(side=LEFT, padx=(8, 0))
        self.connect_old.fit(extra=8)

        # ---- 手动 token ----
        p_man = W.Panel(holder)
        p_man.pack(fill=X, pady=(10, 0))
        b = p_man.body
        self._card_head(b, "手动填写 token", "自动接管失败时的兜底", pad=(20, 12))
        self.dt_token_var = StringVar(value="")
        mrow = tk.Frame(b, bg=C["surface"])
        mrow.pack(fill=X, padx=20, pady=(8, 4))
        tk.Label(mrow, text="tbea_art_token", font=T.f(9), fg=C["text2"],
                 bg=C["surface"]).pack(side=LEFT)
        W.entry(mrow, self.dt_token_var, width=34, size=9).pack(
            side=LEFT, padx=(10, 8), ipady=4)
        W.Btn(mrow, "保存并校验", self._save_dt_token, variant="primary",
              height=32, size=9).pack(side=LEFT)
        self.dt_manual_note = StringVar(value="")
        tk.Label(b, textvariable=self.dt_manual_note, font=T.f(8), fg=C["text3"],
                 bg=C["surface"], anchor="w").pack(fill=X, padx=20, pady=(2, 12))
        p_man.fit(extra=8)

        self.root.after(200, area.refresh)

    def _refresh_connect(self) -> None:
        if not hasattr(self, "connect_vars"):
            return
        mode = self.mode or "old"
        self.connect_vars["mode"].set("钉钉版 TBe+" if mode == "dingtalk"
                                      else "老版本 TBe+")
        token = getattr(self, "_dt_token", "") or newsapi.remembered_token(
            APP_DIR / "state.json")
        if mode == "dingtalk":
            self.connect_vars["client"].set(
                "已启动" if dingtalk.is_home_running() else "未启动")
            self.connect_vars["token"].set(
                f"{token[:6]}…{token[-4:]}" if len(token) > 12 else
                ("已缓存" if token else "未接管"))
            self.connect_vars["way"].set("纯 HTTP · 客户端可关闭" if token
                                         else "需要先接管 token")
            if self.connect_old.winfo_manager():
                self.connect_old.pack_forget()
            if not self.connect_dt.winfo_manager():
                self.connect_dt.pack(fill=X, pady=(10, 0),
                                     after=self.connect_stat)
        else:
            self.connect_vars["client"].set(
                f"调试端口 {self._client_port}" if self._client_port else "未接管")
            self.connect_vars["token"].set(
                f"{token[:6]}…{token[-4:]}" if token else "未设置")
            self.connect_vars["way"].set("客户端会话")
            if self.connect_dt.winfo_manager():
                self.connect_dt.pack_forget()
            if not self.connect_old.winfo_manager():
                self.connect_old.pack(fill=X, pady=(10, 0),
                                      after=self.connect_stat)
        if self.dt_state_var.get() in ("", "等待准备"):
            self.dt_state_var.set("token 已接管，可以直接开始任务" if token
                                  else "尚未接管 token")
        if hasattr(self, "old_state_var"):
            self.old_state_var.set(
                f"已连接（端口 {self._client_port}）" if self._client_port
                else ("TBe+ 在运行，但未接管调试端口" if self._client_running
                      else "未检测到 TBe+ 客户端"))
        if self.dt_token_var.get() == "" and token:
            self.dt_token_var.set(token)

    def _on_prepare(self) -> None:
        if self.mode == "dingtalk":
            self._on_dingtalk_prepare()
        else:
            self._on_attach()

    def _on_dingtalk_prepare(self) -> None:
        if self.starting:
            return
        self.stop_flag.clear()
        self.starting = True
        self._busy(True)
        self.btn_stop.set_enabled(False)
        self.pill.set("连接中", "run")
        self.stage_var.set("正在准备钉钉版 e+")
        self.dt_state_var.set("正在启动客户端并读取 token…")
        self.log("")
        self.log("===== 准备：钉钉版 e+ =====")

        def work() -> None:
            try:
                token = dingtalk.auto_prepare(
                    APP_DIR / "state.json",
                    [self.cfg.dingtalk_exe] if self.cfg.dingtalk_exe else [],
                    log=self.log,
                    status=lambda text: self.ui_queue.put(("dt_status", text)),
                    stop_event=self.stop_flag,
                )
            except Exception as exc:  # noqa: BLE001
                self.log(f"[出错] {exc}")
                token = ""
            self.ui_queue.put(("dt_ready", token))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _dt_ready(self, token: str) -> None:
        if self.mode != "dingtalk":
            return
        self.starting = False
        self.worker = None
        self._busy(False)
        self.btn_stop.set_enabled(False)
        self._dt_token = token or ""
        if token:
            self.pill.set("已完成", "ok")
            self.stage_var.set("token 已接管")
            self.dt_state_var.set("已接管 token，可以开始任务（客户端可关闭）")
            self.dt_manual_note.set("token 校验通过，已写入 state.json")
            self.log("准备完成：token 已接管，之后可以纯 HTTP 运行。")
            self._show_page(HOME)
        else:
            self.pill.set("空闲", "idle")
            self.stage_var.set("等待登录后重试")
            self.dt_state_var.set("还没读到 token：请在 e+ 里打开「新闻资讯」后点重试")
            self.log("准备未完成：请在钉钉版 e+ 里打开「新闻资讯」，程序会在后台自动接管。")
            self._show_page(CONNECT)
            self._start_dt_watch()
        self._refresh_status()
        self._refresh_connect()

    def _start_dt_watch(self) -> None:
        if self._dt_watch_active:
            return
        self._dt_watch_active = True

        def work() -> None:
            token = dingtalk.watch_token(
                APP_DIR / "state.json", timeout=300, log=self.log,
                status=lambda text: self.ui_queue.put(("dt_status_watch", text)),
                stop_event=self.stop_flag,
            )
            self.ui_queue.put(("dt_found", token))

        threading.Thread(target=work, daemon=True).start()

    def _dt_found(self, token: str) -> None:
        self._dt_watch_active = False
        if self.mode != "dingtalk" or not token:
            return
        self._dt_token = token
        if hasattr(self, "dt_state_var"):
            self.dt_state_var.set("已接管 token，可以开始任务（客户端可关闭）")
        if hasattr(self, "dt_manual_note"):
            self.dt_manual_note.set("token 校验通过，已写入 state.json")
        self.pill.set("已接管", "ok")
        self.stage_var.set("token 已接管")
        self.log("后台已接管 token，可以开始任务。")
        self._refresh_status()
        self._refresh_connect()

    def _on_open_news(self) -> None:
        exe = dingtalk.find_home_exe(
            [self.cfg.dingtalk_exe] if self.cfg.dingtalk_exe else [], self.log)
        if exe is None:
            self.dt_state_var.set("没有找到钉钉版 e+，请先安装或在配置里指定")
            return
        if not dingtalk.is_home_running() and not dingtalk.launch_home(exe, self.log):
            self.dt_state_var.set("钉钉版 e+ 启动失败，请手动打开")
            return
        dingtalk.bring_home_to_front()
        self.dt_state_var.set("已把钉钉版 e+ 带到前台，请点开「e+」→「新闻资讯」")
        self.log("已把钉钉版 e+ 带到前台。打开「e+ → 新闻资讯」后，程序会自动接管 token。")
        self._start_dt_watch()

    def _open_home_dir(self) -> None:
        exe = dingtalk.find_home_exe(
            [self.cfg.dingtalk_exe] if self.cfg.dingtalk_exe else [], self.log)
        if exe is None:
            messagebox.showinfo("没找到客户端", "没有找到 TBeHome.exe。", parent=self.root)
            return
        try:
            os.startfile(str(exe.parent))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("打开目录", str(exc), parent=self.root)

    def _save_dt_token(self) -> None:
        token = self.dt_token_var.get().strip()
        if len(token) != 32 or any(c not in "0123456789abcdefABCDEF" for c in token):
            self.dt_manual_note.set("格式不对：应为 32 位十六进制")
            return
        self.dt_manual_note.set("正在校验…")
        self.root.update_idletasks()
        if not dingtalk.token_valid(token.lower()):
            self.dt_manual_note.set("校验失败：token 无效或已过期")
            return
        newsapi.remember_token(APP_DIR / "state.json", token.lower(), "手动填写")
        self.cfg.news_token = token.lower()
        self._dt_token = token.lower()
        self.dt_manual_note.set("校验通过，已保存")
        self.dt_state_var.set("已接管 token，可以开始任务")
        self.log("已手动保存并验证 token。")
        self._refresh_status()
        self._refresh_connect()

    def _on_settings_resize(self, event) -> None:
        pad = max(0, event.width - 880)
        for card in getattr(self, "_setting_cards", []):
            card.pack_configure(padx=(0, pad))

    # ==================================================================
    # 板块
    # ==================================================================
    def _mode_boards(self) -> list:
        """当前版本要跑的板块名（与引擎实际使用的名字一致）。"""
        if not self.mode:
            return []
        names = []
        for name in MODES[self.mode]["boards"]:
            if name == "钉钉版e+新闻资讯":
                names.append(self.cfg.dingtalk_news_name)
            else:
                names.append(name)
        return names

    def _build_board_chips(self) -> None:
        for row in (self.chip_row1, self.chip_row2):
            for child in row.winfo_children():
                child.destroy()
        self.board_chips = {}
        names = self._mode_boards()
        for name in names:
            self.board_chips[name] = W.Chip(
                self.chip_row1.master, name, True, command=self._on_board_change)
        self._layout_board_chips(self._compact or False)
        self.mode_label.set(f"当前：{MODES[self.mode]['title']}")
        self.ver_var.set(f"TBE+ 自动化中心  v{VERSION}")

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
        if not getattr(self, "sum_boards", None):
            return
        boards = [n for n, chip in self.board_chips.items() if chip.get()]
        self.sum_boards.set(
            "板块：" + ("、".join(boards) if boards else "一个都没选 —— 跑起来不会做任何事"))
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
    # 状态 / 日志
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
            elif kind == "dt_status":
                if hasattr(self, "dt_state_var"):
                    self.dt_state_var.set(payload)
                self.stage_var.set(payload)
            elif kind == "dt_ready":
                self._dt_ready(payload)
            elif kind == "dt_status_watch":
                if hasattr(self, "dt_state_var") and self.mode == "dingtalk":
                    self.dt_state_var.set(payload)
            elif kind == "dt_found":
                self._dt_found(payload)
        self.root.after(80, self._drain)

    def _refresh_status(self) -> None:
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
                if self.mode == "dingtalk":
                    running = dingtalk.is_home_running()
                else:
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
        if self.mode == "dingtalk":
            running = dingtalk.is_home_running()
            self._client_running = bool(running)
            token = getattr(self, "_dt_token", "") or newsapi.remembered_token(
                APP_DIR / "state.json")
            self._dt_token = token
            if token:
                self.status_var.set("已接管")
                self.status_note.set("token 已缓存\n可离线执行，无需客户端")
                self._draw_dot(C["ok"])
                if not self.running:
                    self.pill.set("已接管", "ok")
            elif running:
                self.status_var.set("已启动")
                self.status_note.set("钉钉版 e+ 已启动\n请打开 e+ → 新闻资讯")
                self._draw_dot(C["warn"])
                if not self.running:
                    self.pill.set("已启动", "run")
            else:
                self.status_var.set("未连接")
                self.status_note.set("未检测到钉钉版 e+\n点「启动钉钉版 e+」")
                self._draw_dot(C["text3"])
                if not self.running:
                    self.pill.set("未连接", "idle")
            self.status_card.fit(extra=10)
            self._sync_attach_button()
            self._refresh_adv()
            self._refresh_connect()
            return
        if port:
            self.cfg.debug_port = port
            title = (targets[0][1] if targets else "") or ""
            title = title[:12] + ("…" if len(title) > 12 else "")
            self.status_var.set("已连接")
            self.status_note.set(f"端口 {port} · 视图 {len(targets)} 个\n{title}")
            self._draw_dot(C["ok"])
        elif running:
            self.status_var.set("未接管")
            self.status_note.set("TBe+ 在运行，无调试端口\n点「准备」重启并接管")
            self._draw_dot(C["warn"])
        else:
            self.status_var.set("未连接")
            self.status_note.set("没检测到 TBe+ 客户端\n可用 token 免客户端运行")
            self._draw_dot(C["text3"])
        self.status_card.fit(extra=10)
        self._refresh_adv()
        self._sync_attach_button()

    def _sync_attach_button(self) -> None:
        if self.running or self.starting:
            return
        if self.mode == "dingtalk":
            if getattr(self, "_dt_token", ""):
                self.btn_attach.set_text("重新接管 token", "\uE72C")
            elif self._client_running:
                self.btn_attach.set_text("重新准备", "\uE72C")
            else:
                self.btn_attach.set_text("启动钉钉版 e+", "\uE8A7")
            return
        if self._client_port:
            self.btn_attach.set_text("重新接管客户端", "\uE72C")
        else:
            self.btn_attach.set_text(self._btn_attach_idle, "\uE72C")

    def _refresh_adv(self) -> None:
        token = ((self.cfg.news_token or "").strip()
                 or newsapi.remembered_token(APP_DIR / "state.json"))
        token_text = (f"{token[:6]}…{token[-4:]}" if len(token) > 12
                      else ("未设置" if not token else token))
        if self.mode == "dingtalk":
            self.adv_var.set(
                f"执行方式 纯 HTTP    令牌 {token_text}    "
                f"接口限速 {self.cfg.api_rate}/秒    并发 {self.cfg.api_concurrency}")
        else:
            self.adv_var.set(
                f"调试端口 {self.cfg.debug_port}    接口限速 {self.cfg.api_rate}/秒    "
                f"并发 {self.cfg.api_concurrency}    令牌 {token_text}")

    def _reload_config(self) -> None:
        self.cfg = load_config()
        self._portal_master = list(self.cfg.portal_cards)
        self.fast_switch.set(not self.cfg.api_read_state)
        self.verify_switch.set(self.cfg.api_verify)
        self._build_board_chips()
        self._sync_sections()
        self.log("已重新读取 config.json")
        self._refresh_status()

    def _open_output(self) -> None:
        try:
            os.startfile(str(APP_DIR))  # noqa: S606  (Windows 专用)
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("程序目录", f"{APP_DIR}\n\n({exc})", parent=self.root)

    # ==================================================================
    # 板块进度
    # ==================================================================
    def _sync_sections(self) -> None:
        if self.running or not self.mode:
            return
        names = [n for n, chip in self.board_chips.items() if chip.get()]
        host = getattr(self, "sections_strip", None)
        if host is None:
            return
        for child in host.winfo_children():
            child.destroy()
        self.sections = {}
        self._section_labels = []
        for i, name in enumerate(names):
            if i:
                tk.Frame(host, bg=C["stroke"], width=1, height=14).pack(
                    side=LEFT, padx=10)
            box = tk.Frame(host, bg=C["surface"])
            box.pack(side=LEFT)
            tk.Label(box, text=name, font=T.f(8), fg=C["text3"],
                     bg=C["surface"]).pack(side=LEFT)
            count = tk.Label(box, text="—", font=(T.MONO, 8), fg=C["text2"],
                             bg=C["surface"])
            count.pack(side=LEFT, padx=(6, 0))
            flag = tk.Label(box, text="待处理", font=T.f(8), fg=C["text3"],
                            bg=C["surface"])
            flag.pack(side=LEFT, padx=(6, 0))
            self.sections[name] = {
                "row": box, "count": count, "flag": flag,
                "total": 0, "last": 0,
                "c": {"liked": 0, "already": 0, "dry": 0,
                      "unclear": 0, "failed": 0},
            }
        if not names:
            tk.Label(host, text="没有勾选任何板块，请到「任务设置」里勾选。",
                     font=T.f(8), fg=C["text3"], bg=C["surface"]).pack(
                side=LEFT)
        if getattr(self, "exec_panel", None):
            self.exec_panel.fit(extra=12)
        self._recalc()

    def _add_section_row(self, name: str) -> None:
        host = getattr(self, "sections_strip", None)
        if host is None:
            return
        box = tk.Frame(host, bg=C["surface"])
        box.pack(side=LEFT, padx=(0, 10))
        tk.Label(box, text=name, font=T.f(8), fg=C["text3"],
                 bg=C["surface"]).pack(side=LEFT)
        count = tk.Label(box, text="—", font=(T.MONO, 8), fg=C["text2"],
                         bg=C["surface"])
        count.pack(side=LEFT, padx=(6, 0))
        flag = tk.Label(box, text="待处理", font=T.f(8), fg=C["text3"],
                        bg=C["surface"])
        flag.pack(side=LEFT, padx=(6, 0))
        self.sections[name] = {
            "row": box, "count": count, "flag": flag,
            "total": 0, "last": 0,
            "c": {"liked": 0, "already": 0, "dry": 0, "unclear": 0, "failed": 0},
        }

    def _fit_sections_panel(self) -> None:
        pass

    def _recalc(self) -> None:
        total = sum(s["total"] for s in self.sections.values())
        done = sum(s["last"] for s in self.sections.values())
        agg = {"liked": 0, "already": 0, "dry": 0, "unclear": 0, "failed": 0}
        for sec in self.sections.values():
            for k in agg:
                agg[k] += sec["c"][k]

        frac = (done / total) if total else 0.0
        self.home_bar.set(frac)
        if total:
            pct = done * 100 // total
            self.prog_var.set(f"{done} / {total} 篇")
            self.prog_note.set(f"{pct}%")
        else:
            self.prog_var.set(f"0 / 0 篇")
            self.prog_note.set("等待开始" if not self.running else "正在统计")

        self.card_processed.set(done, f"{int(frac * 100)}%" if total else "等待运行",
                                total=total or 0)
        self.card_liked.set(agg["liked"], f"待点赞 {agg['dry']}" if agg["dry"] else "",
                            accent=C["accent"])
        self.card_already.set(agg["already"], "已赞过，自动跳过", accent=C["ok"])
        self.card_failed.set(agg["failed"], "可重跑补齐" if agg["failed"] else "",
                             accent=C["err"] if agg["failed"] else C["text3"])

        # 处理进度分布
        pending = max(total - done, 0)
        if hasattr(self, "seg"):
            self.seg.set([agg["liked"], agg["already"], pending, agg["failed"]],
                         [C["ok"], C["slate"], C["stroke_hi"], C["err"]])
            for key, value in (("liked", agg["liked"]),
                               ("already", agg["already"]),
                               ("pending", pending),
                               ("failed", agg["failed"])):
                self.legend_vars[key].set(str(value))

        # 运行质量：只用真实数据，没有数据就显示 —
        elapsed = (time.monotonic() - self.started_at) if self.running else \
            getattr(self, "last_elapsed", 0.0)
        if done:
            rate = (agg["liked"] + agg["already"]) * 100.0 / done
            self.quality_vars["rate"].set(f"{rate:.0f}%")
            self.quality_vars["speed"].set(f"{elapsed / done:.1f}s")
        else:
            self.quality_vars["rate"].set("—")
            self.quality_vars["speed"].set("—")
        self.quality_vars["batch"].set(f"{done}/{total}" if total else "—")

        # 流程步骤
        if not self.running and getattr(self, "_last_error", ""):
            if getattr(self, "steps", None):
                self.steps.set_states(["done", "error", "idle"])
            self.run_state.configure(text="● 失败", fg=C["err"])
        elif self.running:
            connect = "done" if (self._client_port or self.mode == "dingtalk") else "active"
            read = "done" if total else "active"
            like = "active" if done else "idle"
            if getattr(self, "steps", None):
                self.steps.set_states([connect, read, like])
            self.run_state.configure(text="● 运行中", fg=C["accent"])
        elif done:
            if getattr(self, "steps", None):
                self.steps.set_states(["done", "done", "done"])
            self.run_state.configure(text="● 已完成", fg=C["ok"])
        else:
            if getattr(self, "steps", None):
                self.steps.set_states(["idle", "idle", "idle"])
            self.run_state.configure(text="● 就绪", fg=C["text3"])

        if self.running:
            prefix = f"正在处理 {self.cur_section}" if self.cur_section else "正在准备"
            self.stage_var.set(f"{prefix} · {done}/{total} 篇" if total else prefix)
        elif done and not self.stage_var.get().startswith("已完成"):
            self.stage_var.set("已完成")

        if getattr(self, "footer_var", None) is not None:
            mode_text = "钉钉版 e+ · 纯 HTTP 模式" if self.mode == "dingtalk" \
                else "老版本 TBe+ · 客户端会话模式"
            self.footer_var.set(
                f"TBE+ 自动化中心  v{VERSION}    {mode_text}    "
                "仅供内部资讯处理使用"
            )

    def _ingest(self, line: str) -> None:
        s = line.strip()
        m = re.match(r"^板块：(.+)$", s) or re.match(r"^板块（会话）：(.+)$", s)
        if m:
            name = m.group(1).strip()
            self.cur_section = name
            if name not in self.sections:
                self._add_section_row(name)
            self.current_var.set(f"正在处理：{name}")
            self.stage_var.set(f"正在处理 {name}")
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
            title = re.sub(r"^\([^)]*\)\s*", "", title)   # 去掉 "(赞数 12)" 这类尾巴
            sec["c"][kind] = sec["c"].get(kind, 0) + 1
            sec["count"].configure(
                text=f"{idx}/{sec['total']}" if sec["total"] else str(idx),
                fg=C["text2"])
            tones = {"liked": C["accent"], "already": C["ok"],
                     "dry": C["warn"], "unclear": C["warn"], "failed": C["err"]}
            sec["flag"].configure(text="处理中", fg=tones.get(kind, C["text3"]))
            if title:
                self.current_var.set(title[:52] + ("…" if len(title) > 52 else ""))
            self._recalc()
            return

        if s.startswith("本板块小结") and self.cur_section in self.sections:
            sec = self.sections[self.cur_section]
            sec["flag"].configure(text="已完成", fg=C["ok"])
            self._recalc()
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
            self.current_time.set(time.strftime("%H:%M:%S"))
        self.root.after(500, self._tick)

    def _busy(self, busy: bool) -> None:
        for button in (self.btn_attach, self.btn_dry, self.btn_run):
            button.set_enabled(not busy)
        self.btn_stop.set_enabled(busy)

    def _reset_progress(self) -> None:
        self.cur_section = None
        self.last_elapsed = 0.0
        self._sync_sections()
        self.current_var.set("正在准备…")
        self.current_time.set("")
        self.elapsed_var.set("用时 00:00")

    # ==================================================================
    # 任务调度
    # ==================================================================
    def _start(self, title: str, coro_factory, *, prepare: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在运行", "上一个任务还没结束，请稍等。",
                                parent=self.root)
            return
        if not self.mode:
            return
        self.stop_flag.clear()
        self._busy(True)
        self.starting = True
        self._reset_progress()
        self.running = True
        self.finished_ok = False
        self._last_error = ""
        self.started_at = time.monotonic()
        self.last_elapsed = 0.0
        self.current_time.set(time.strftime("%H:%M:%S"))
        self.pill.set("运行中" if not prepare else "连接中", "run")
        self.stage_var.set("正在接管客户端" if prepare else "正在准备")
        if prepare:
            self.btn_attach.set_text("正在接管…", "\uE72C")
        self.log("")
        self.log(f"===== {title} =====")

        def worker() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coro_factory())
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
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
        self.starting = False
        self._busy(False)
        self._refresh_status()
        if self._last_error:
            self.pill.set("失败", "err")
            self.stage_var.set("任务失败")
            self.current_var.set(self._last_error[:90])
        elif self.stop_flag.is_set():
            self.pill.set("已停止", "warn")
            self.stage_var.set("已停止")
            self.current_var.set("已停止。已经点上的赞不会重复。")
        else:
            self.pill.set("已完成", "ok")
            self.stage_var.set("已完成")
            self.current_var.set("完成。可以再跑一次补上没成功的文章。")
        self.finished_ok = not bool(self._last_error)
        if self.started_at:
            self.last_elapsed = time.monotonic() - self.started_at
            sec = int(self.last_elapsed)
            self.elapsed_var.set(f"用时 {sec // 60:02d}:{sec % 60:02d}")
        self._recalc()
        self._sync_attach_button()

    # ==================================================================
    # 三个按钮
    # ==================================================================
    def _on_attach(self) -> None:
        if self.starting:
            return
        if not messagebox.askyesno(
                "准备接管",
                "这一步会关闭正在运行的 TBe+ 客户端，再带调试端口重新启动。\n\n"
                "登录状态会保留，但正在进行的对话窗口会被中断。\n\n确定继续吗？",
                parent=self.root):
            return
        self._start("准备：重启并接管 TBe+", self._attach_job, prepare=True)

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
            chosen = self._ask_client_exe()
            if not chosen:
                self.log("没有选择 TBe.exe，已取消。")
                return
            exe = Path(chosen)
            if client_mod.save_client_exe(exe, self.cfg.client_exe_candidates):
                self.log(f"已经记到 config.json，下次会自动用它：{exe}")
            self.cfg.client_exe_candidates = [str(exe)] + list(
                self.cfg.client_exe_candidates)

        used = client_mod.restart_client(
            exe, preferred, self.cfg.restart_wait_seconds, self.log)
        if not used:
            self.log("准备失败。")
            return
        self.cfg.debug_port = used
        if used != preferred:
            client_mod.save_debug_port(used)
        self.log(f"准备完成（调试端口 {used}），可以点「试运行」或「开始点赞」了。")

    def _ask_client_exe(self) -> str:
        result: dict = {}
        done = threading.Event()

        def ask() -> None:
            try:
                result["path"] = filedialog.askopenfilename(
                    parent=self.root,
                    title="请选择 TBe.exe（在 TBe+ 的安装目录里）",
                    filetypes=[("TBe 主程序", "TBe.exe"),
                               ("可执行文件", "*.exe"), ("所有文件", "*.*")])
            finally:
                done.set()

        self.root.after(0, ask)
        done.wait(timeout=600)
        return result.get("path") or ""

    def _need_token(self) -> bool:
        """钉钉版没有 token 时，直接去引导页，绝不回退到旧版客户端。"""
        if self.mode != "dingtalk":
            return False
        token = getattr(self, "_dt_token", "") or newsapi.remembered_token(
            APP_DIR / "state.json")
        if token:
            return False
        self._show_page(CONNECT)
        self.dt_state_var.set("还没有接管 token，先点「一键准备」")
        self.log("钉钉版需要先接管 token；本模式不会启动旧版 TBe+。")
        return True

    def _on_dry_run(self) -> None:
        if self._need_token():
            return
        self._start("试运行（只看不点）", lambda: self._run_job(True))

    def _on_run(self) -> None:
        if self._need_token():
            return
        if not messagebox.askyesno(
                "开始点赞",
                "将对所选板块里所有未点赞的文章执行点赞。\n\n确定继续吗？",
                parent=self.root):
            return
        self._start("正式点赞", lambda: self._run_job(False))

    def _apply_settings(self) -> None:
        mode = self.mode
        if mode == "old":
            self.cfg.portal_cards = [n for n in self._portal_master
                                     if self.board_chips.get(n)
                                     and self.board_chips[n].get()]
            if self.board_chips.get(self.cfg.session_channel):
                self.cfg.skip_session_by_default = not self.board_chips[
                    self.cfg.session_channel].get()
            self.cfg.dingtalk_news = False
        else:
            self.cfg.portal_cards = []
            self.cfg.skip_session_by_default = True
            name = self.cfg.dingtalk_news_name
            self.cfg.dingtalk_news = bool(
                self.board_chips[name].get()) if name in self.board_chips else True
        self.cfg.api_read_state = not self.fast_switch.get()
        self.cfg.api_verify = self.verify_switch.get()
        self.cfg.restart_every_n_articles = self.restart_stepper.get()

    async def _run_job(self, dry_run: bool) -> None:
        self._apply_settings()

        probe = LikeEngine(self.cfg, dry_run=True, log=self.log)
        http_ready = await probe.ensure_http()
        if not http_ready:
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
                    "  或者先点「准备并接管客户端」。")
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
                    parent=self.root))
                return
        self.cfg.filter_since = since
        self.cfg.filter_until = until
        self.log(f"时间段：{since or '不限'} ~ {until or '今天'}"
                 if (since or until) else "时间段：不限（全部文章）")

        engine = LikeEngine(self.cfg, dry_run=dry_run, log=self.log,
                            stop_flag=self.stop_flag.is_set)
        results = await engine.run(limit=self.limit_stepper.get())
        self.log("")
        self.log(summarize(results))
        if not dry_run and not self.cfg.api_read_state:
            self.log("提示：本次是「快速模式」（不查历史状态），只报了"
                     "「已确认点赞」多少篇，分不清其中多少本来就赞过。")

    def _on_stop(self) -> None:
        if not self.running:
            return
        self.stop_flag.set()
        self.pill.set("正在停止", "warn")
        self.stage_var.set("正在停止")
        self.log("已请求停止，正在结束当前文章…")

    # ==================================================================
    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                    "还在运行", "任务还没结束，现在关闭会中断当前文章。\n\n确定退出吗？",
                    parent=self.root):
                return
            self.stop_flag.set()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
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
    set_window_icon(root)
    App(root, config or load_config())
    root.mainloop()
