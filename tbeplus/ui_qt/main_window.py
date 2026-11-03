"""新版主窗口：无边框窗口 + 标题栏 + 侧栏 + 顶部工具栏 + 页面栈。

与 ``app.py``（Tkinter 版）互相独立，可并行启动对比。
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QVBoxLayout,
    QWidget,
)

from .. import __version__
from . import icons, paint, theme
from .components.sidebar import Sidebar
from .components.topbar import TopBar
from .components.button import WindowButton
from .dialogs import Dialog
from .pages.overview import OverviewPage
from .pages.placeholder import PlaceholderPage
from .viewmodel import HomeViewModel

NAV_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("工作空间", [
        ("home", "任务概览", "nav-dashboard"),
        ("log", "运行日志", "nav-log"),
    ]),
    ("自动化管理", [
        ("settings", "任务设置", "nav-sliders"),
        ("config", "配置管理", "nav-folder"),
        ("connect", "账号与连接", "nav-link"),
    ]),
    ("系统", [
        ("version", "版本信息", "nav-info"),
        ("prefs", "偏好设置", "nav-gear"),
    ]),
]

PAGE_META: dict[str, tuple[str, str]] = {
    "home": ("任务概览", "运行状态、阶段进度与实时事件"),
    "log": ("运行日志", "连接、读取、点赞全过程"),
    "settings": ("任务设置", "板块、时间段与运行参数"),
    "config": ("配置管理", "配置文件与运行参数"),
    "connect": ("账号与连接", "客户端接管与 token"),
    "version": ("版本信息", "当前版本与运行环境"),
    "prefs": ("偏好设置", "动效、日志与界面偏好"),
}

PLACEHOLDER_HINT = {
    "log": "完整日志页将在页面迁移阶段接入（P3）。首页已在下方展示实时事件。",
    "settings": "任务设置页将在页面迁移阶段接入（P3），复用原有配置读写逻辑。",
    "config": "配置管理页将在页面迁移阶段接入（P3）。",
    "connect": "账号与连接页将在页面迁移阶段接入（P3）。",
    "version": "版本信息页将在页面迁移阶段接入（P3）。",
    "prefs": "偏好设置页将在页面迁移阶段接入（P3）。",
}


class TitleBar(QWidget):
    """自绘标题栏：品牌、拖拽、窗口控制。"""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__()
        self._win = win
        self._drag: QPoint | None = None
        self.setFixedHeight(theme.TITLEBAR_H)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 10, 0)
        lay.setSpacing(0)

        self.brand = _Brand()
        lay.addWidget(self.brand)
        lay.addStretch(1)

        self.btn_min = WindowButton("minus")
        self.btn_max = WindowButton("maximize")
        self.btn_close = WindowButton("x", "close")
        self.btn_min.clicked.connect(win.showMinimized)
        self.btn_max.clicked.connect(win.toggle_max)
        self.btn_close.clicked.connect(win.close)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            lay.addWidget(b)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self._win.windowHandle()
            if handle is not None:
                handle.startSystemMove()
            self._drag = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if not self._win.isMaximized():
                self._win.move(e.globalPosition().toPoint() - self._drag)
            e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag = None

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._win.toggle_max()
            e.accept()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        p.fillRect(self.rect(), paint.qcolor("bg_sidebar"))
        p.setPen(QPen(paint.qcolor("border"), 1.0))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


class _Brand(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(theme.TITLEBAR_H)
        self.setFixedWidth(180)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        paint.quality(p)
        box = QRectF(0, (self.height() - 20) / 2, 20, 20)
        paint.surface(p, box, radius=6, fill="accent")
        p.setFont(theme.f(12, 700))
        p.setPen(paint.qcolor("on_accent"))
        p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), "T")

        p.setFont(theme.f(13, 600))
        p.setPen(paint.qcolor("text"))
        p.drawText(QRectF(28, 0, 60, self.height()),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   "TBE+")
        p.setFont(theme.f(11))
        p.setPen(paint.qcolor("text3"))
        p.drawText(QRectF(66, 0, 100, self.height()),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   "工作台")


class ResizeFilter(QObject):
    """窗口边缘按下 → 交给系统原生 resize（保留 Win11 snap）。"""

    _MARGIN = 6

    def __init__(self, win: QMainWindow) -> None:
        super().__init__()
        self._win = win

    def eventFilter(self, obj, ev):  # noqa: ANN001
        if ev.type() != QEvent.Type.MouseButtonPress:
            return False
        if ev.button() != Qt.MouseButton.LeftButton or self._win.isMaximized():
            return False
        handle = self._win.windowHandle()
        if handle is None:
            return False
        g = ev.globalPosition().toPoint()
        r = self._win.frameGeometry()
        m = self._MARGIN
        edges = 0
        if g.x() <= r.left() + m:
            edges |= 0x1
        if g.x() >= r.right() - m:
            edges |= 0x4
        if g.y() <= r.top() + m:
            edges |= 0x2
        if g.y() >= r.bottom() - m:
            edges |= 0x8
        if edges:
            handle.startSystemResize(Qt.Edges(edges))
            return True
        return False


class MainWindow(QMainWindow):
    def __init__(self, controller=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.vm = HomeViewModel(self)
        self.setWindowTitle("TBE+ 工作台")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(980, 660)
        self._build()
        self._resize_filter = ResizeFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._resize_filter)
        self._apply_dwm()
        self.show_page("home")

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        root_widget = QWidget()
        root_widget.setObjectName("appRoot")
        self.setCentralWidget(root_widget)

        root = QVBoxLayout(root_widget)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.titlebar = TitleBar(self)
        root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        self.sidebar = Sidebar(NAV_GROUPS)
        self.sidebar.page_selected.connect(self.show_page)
        self.sidebar.version.clicked.connect(self._on_version_click)
        self.sidebar.refresh_btn.clicked.connect(self._on_refresh_conn)
        self.sidebar.footer.setText(f"v{__version__} · Windows 11")
        body.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        body.addLayout(right, 1)

        self.topbar = TopBar()
        self.topbar.settings_btn.clicked.connect(lambda: self.show_page("settings"))
        right.addWidget(self.topbar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("pageHost")
        right.addWidget(self.stack, 1)

        self.home = OverviewPage(self.vm)
        self.home.start_requested.connect(self._on_start)
        self.home.dry_requested.connect(self._on_dry)
        self.home.stop_requested.connect(self._on_stop)
        self.home.attach_requested.connect(self._on_attach)
        self.home.open_log_requested.connect(lambda: self.show_page("log"))
        self.stack.addWidget(self.home)

        for key, _title, icon in [
            (k, t, i) for _g, items in NAV_GROUPS for (k, t, i) in items
            if k != "home"
        ]:
            page = PlaceholderPage(PAGE_META[key][0],
                                   PLACEHOLDER_HINT.get(key, ""), icon=icon,
                                   stage="待迁移 · P3")
            self.stack.addWidget(page)
            page.setProperty("key", key)

        # 连接状态：原型阶段给出真实的环境事实（未连接业务层），不假装已连接
        self.sidebar.set_connection("未接管客户端", "warn",
                                    "原型阶段：业务引擎将在 P2 接入")
        self.topbar.set_state("空闲", "idle", "00:00")

        self._toast = QLabel(self)
        self._toast.setFont(theme.f(12))
        self._toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._toast.setStyleSheet(
            f"background: {theme.C['bg_elevated']}; color: {theme.C['text']};"
            f"border: 1px solid {theme.C['border_strong']}; border-radius: 8px;"
            "padding: 7px 14px;")
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)

    # ------------------------------------------------------------------ 导航
    def page_widget(self, key: str) -> QWidget | None:
        for i in range(self.stack.count()):
            w = self.stack.widget(i)
            if w.property("key") == key:
                return w
        return None

    def show_page(self, key: str) -> None:
        if key == "home":
            target = self.home
        else:
            target = self.page_widget(key)
        if target is None:
            return
        self.stack.setCurrentWidget(target)
        self.sidebar.set_page(key)
        title, sub = PAGE_META.get(key, (key, ""))
        self.topbar.set_page(title, sub)

    # ------------------------------------------------------------------ 交互
    def _on_version_click(self) -> None:
        if self._controller is not None and hasattr(self._controller, "switch_mode"):
            self._controller.switch_mode()
            return
        self._info("版本选择", "当前为界面原型（P1），版本切换将在 P2 接入原有切换逻辑。")

    def _on_refresh_conn(self) -> None:
        if self._controller is not None and hasattr(self._controller, "refresh_status"):
            self._controller.refresh_status()
            return
        self.sidebar.set_connection("未接管客户端", "warn",
                                    "原型阶段：业务引擎将在 P2 接入")

    def _on_start(self) -> None:
        if self._controller is not None and hasattr(self._controller, "start_run"):
            self._controller.start_run()
            return
        self._info("开始点赞", "当前为界面原型（P1）：按钮已就位，任务引擎将在 P2 接入后执行真实点赞。")

    def _on_dry(self) -> None:
        if self._controller is not None and hasattr(self._controller, "start_dry_run"):
            self._controller.start_dry_run()
            return
        self._info("试运行", "当前为界面原型（P1）：试运行将在 P2 接入。")

    def _on_stop(self) -> None:
        if self._controller is not None and hasattr(self._controller, "stop_run"):
            self._controller.stop_run()
            return

    def _on_attach(self) -> None:
        if self._controller is not None and hasattr(self._controller, "attach_client"):
            self._controller.attach_client()
            return
        self._info("准备并接管客户端",
                   "当前为界面原型（P1）：客户端接管沿用原有机制，将在 P2 接入。")

    def _info(self, title: str, text: str) -> None:
        dlg = Dialog(title, text, self)
        dlg.add_action("知道了", variant="primary", accept=True)
        dlg.exec()

    # ------------------------------------------------------------------ 窗口
    def show_toast(self, text: str) -> None:
        """轻量提示：不打断操作，1.8 秒后自动消失。"""
        self._toast.setText(text)
        self._toast.adjustSize()
        self._place_toast()
        self._toast.show()
        self._toast.raise_()
        self._toast_timer.start(1800)

    def _place_toast(self) -> None:
        if not hasattr(self, "_toast"):
            return
        w = self._toast.width()
        self._toast.move((self.width() - w) // 2, self.height() - 76)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place_toast()

    def toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.titlebar.btn_max.set_icon("maximize")
        else:
            self.showMaximized()
            self.titlebar.btn_max.set_icon("square")

    def _apply_dwm(self) -> None:
        try:
            hwnd = int(self.winId())
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:  # noqa: BLE001
            pass
