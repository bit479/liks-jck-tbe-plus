#!/usr/bin/env python
"""图形界面入口：双击「启动.bat」就是跑这个。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("缺少依赖 websockets。请先运行：pip install -r requirements.txt")
        input("\n按回车退出…")
        return 1

    from tbeplus.ui import run_ui

    run_ui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
