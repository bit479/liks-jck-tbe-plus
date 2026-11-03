"""TBE+ 新版 Qt 界面（P1：渲染体系 + 首页原型）。

与 Tkinter 版（``tbeplus/ui.py`` + ``app.py``）完全并行：
- 不 import 旧 UI 模块；
- 不修改 ``engine`` / ``client`` / ``newsapi`` / ``config``；
- 通过 ``HomeViewModel`` 接收事件，后续由 Controller 把引擎日志接进来。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from . import theme
from .main_window import MainWindow


def configure_high_dpi() -> None:
    """在创建 QApplication 之前设定缩放策略。

    ``PassThrough`` 让 125% / 150% 等非整数缩放按真实比例渲染，
    而不是四舍五入到 100% 或 200%（Qt 6 默认即 Per-Monitor V2）。
    """
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


def create_app(argv: list[str] | None = None) -> QApplication:
    configure_high_dpi()
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("TBE+ 工作台")
    app.setApplicationDisplayName("TBE+ 工作台")
    app.setFont(theme.base_font())
    app.setStyleSheet(theme.build_qss())
    return app


def create_window(controller=None) -> MainWindow:
    return MainWindow(controller=controller)


def run(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or create_app(argv)
    win = create_window()
    win.resize(1280, 800)
    win.show()
    return app.exec()


__all__ = ["run", "create_app", "create_window", "configure_high_dpi", "theme"]
