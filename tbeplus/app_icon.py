# -*- coding: utf-8 -*-
"""窗口/任务栏图标设置（TBe+ 客户端）。

两个要点（实测踩过）：
1. 任务栏：Windows 默认按 python.exe 归组显示 Python 图标，必须在本进程
   创建任何窗口**之前**调用 SetCurrentProcessExplicitAppUserModelID 声明
   自己的 AppUserModelID，任务栏才会用我们的图标；
2. 标题栏：tkinter 的 iconbitmap 只有 ``-default`` 形式才接受 .ico，
   普通形式只认 .xbm，传 .ico 会静默失败。
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

APP_USER_MODEL_ID = "TBePlus.AutoLike.1"


def _icon_path() -> Path:
    """源码运行取仓库 assets/；打包后取 PyInstaller 解包目录里的 assets/。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[1]
    return base / "assets" / "app.ico"


ICON_PATH = _icon_path()


def set_app_user_model_id() -> None:
    """必须在 tk.Tk() 之前调用。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:  # noqa: BLE001 - 非 Windows 或接口缺失时静默跳过
        pass


def set_window_icon(root) -> None:
    """设置窗口左上角图标（同时作为后续窗口默认）。"""
    if not ICON_PATH.exists():
        return
    try:
        root.iconbitmap(default=str(ICON_PATH))
    except Exception:  # noqa: BLE001
        pass
