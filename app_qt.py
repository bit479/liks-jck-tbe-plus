#!/usr/bin/env python
"""新版 Qt 界面入口（与 app.py 的 Tkinter 版并行，互不影响）。

用法：
    python app_qt.py            # 1280x800
    python app_qt.py 1600x1000  # 指定初始窗口尺寸
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tbeplus.ui_qt import create_app, create_window  # noqa: E402


def _parse_size(arg: str) -> tuple[int, int] | None:
    if "x" not in arg:
        return None
    try:
        w, h = arg.lower().split("x", 1)
        return int(w), int(h)
    except ValueError:
        return None


def main() -> int:
    app = create_app(sys.argv)
    size = next((s for s in (_parse_size(a) for a in sys.argv[1:]) if s), (1280, 800))
    win = create_window()
    win.resize(*size)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
