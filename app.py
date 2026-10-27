#!/usr/bin/env python
"""图形界面入口：双击「启动.bat」跑的就是这个。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _selftest() -> int:
    """`exe --selftest`：不建窗口，把关键路径写进 exe 同目录的 selftest.log。

    打包成 --windowed 后没有 stdout，这是交付后唯一能远程确认
    "配置目录在哪、图标资源在不在、tkinter 有没有打进去" 的办法。
    """
    import tkinter  # noqa: F401

    from tbeplus import __version__
    from tbeplus.app_icon import ICON_PATH
    from tbeplus.config import APP_DIR, CONFIG_FILE

    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent
    lines = [
        f"version      = {__version__}",
        f"frozen       = {bool(getattr(sys, 'frozen', False))}",
        f"executable   = {sys.executable}",
        f"APP_DIR      = {APP_DIR}",
        f"CONFIG_FILE  = {CONFIG_FILE}  (exists={CONFIG_FILE.exists()})",
        f"ICON         = {ICON_PATH}  (exists={ICON_PATH.exists()})",
        f"tkinter      = {tkinter.TkVersion}",
        f"config_json  = {'找到' if CONFIG_FILE.exists() else '无（用默认值）'}",
    ]
    out = base / "selftest.log"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()

    # 图形界面要 tkinter。绝大多数官方 Python 都自带，个别精简版没有。
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print()
        print("  你的 Python 里没有图形界面组件（tkinter），打不开窗口。")
        print()
        print("  两个办法：")
        print("    1) 双击「命令行.bat」用命令行模式，功能完全一样；")
        print("    2) 或者到 https://www.python.org/downloads/ 重装一次官方版本，")
        print("       tkinter 是默认勾选的。")
        print()
        input("  按回车退出…")
        return 1

    from tbeplus.ui import run_ui

    run_ui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
