#!/usr/bin/env python
"""图形界面入口：双击「启动.bat」跑的就是这个。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
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
