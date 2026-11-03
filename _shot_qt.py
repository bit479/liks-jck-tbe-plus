#!/usr/bin/env python
"""渲染新版 Qt 界面截图（视觉验收用）。

用法：
    python _shot_qt.py out.png [page] [1280x800] [--scale=1.5] [--demo]

``--demo`` 会按引擎真实日志格式喂入一组演练事件，用来检查运行态样式；
产品运行路径不会注入这些数据。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_args = [a for a in sys.argv[1:]]


def _system_percent() -> int:
    """系统缩放百分比（Windows shcore）；取不到时按 100 处理。"""
    try:
        import ctypes

        return int(ctypes.windll.shcore.GetScaleFactorForDevice(0)) or 100
    except Exception:  # noqa: BLE001
        return 100


# --dpr=1.5 表示目标 DPI 缩放 150%。Windows 下 QT_SCALE_FACTOR 是相对系统 DPI 的
# 乘数，因此换算成 目标 / 系统百分比，才能精确复现 100%/125%/150%/200%。
_scale = 1.0
_target_dpr = 0.0
for _a in list(_args):
    if _a.startswith("--scale="):
        _scale = float(_a.split("=", 1)[1])
        _args.remove(_a)
    elif _a.startswith("--dpr="):
        _target_dpr = float(_a.split("=", 1)[1])
        _args.remove(_a)
_demo = "--demo" in _args
if _demo:
    _args.remove("--demo")
_offscreen = "--offscreen" in _args
if _offscreen:
    _args.remove("--offscreen")
if _target_dpr:
    _scale = round(_target_dpr / (_system_percent() / 100.0), 4)
if abs(_scale - 1.0) > 1e-6:
    # 必须在 QApplication 之前设置，才能模拟 125% / 150% / 200% 缩放
    os.environ["QT_SCALE_FACTOR"] = str(_scale)
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

from PySide6.QtCore import Qt, QTimer  # noqa: E402

from tbeplus.ui_qt import create_app, create_window  # noqa: E402

DEMO_LINES = [
    "客户端已接管，调试端口 9222",
    "板块：新闻资讯",
    "  列表里共 12 篇",
    "  时间段筛选：2026-07-01 至今，跳过 3 篇已处理",
    "[1/12] 09-30 新点赞 集团召开三季度经营分析会",
    "[2/12] 09-30 新点赞 关于推进数字化转型的实施方案",
    "[3/12] 09-29 原本已点赞 第七届职工技能大赛圆满结束",
    "[4/12] 09-29 新点赞 基层党组织建设工作会议召开",
    "[5/12] 09-28 原本已点赞 关于开展安全生产大检查的通知",
    "[6/12] 09-28 新点赞 青年员工座谈会顺利举行",
    "[7/12] 09-27 点赞失败 关于进一步加强安全生产管理的通知",
    "  第 2 轮：重试上一轮没成功的 1 篇",
]


def _feed(win) -> None:
    vm = win.vm
    vm.start("正在准备…")
    for line in DEMO_LINES:
        vm.ingest_line(line)
    vm._started_at = time.monotonic() - 95
    win.sidebar.set_connection("已接管客户端", "ok", "调试端口 9222 · 会话正常")
    win.topbar.set_state("运行中", "run", "01:35")


def main() -> int:
    out = _args[0] if _args else "shot_qml.png"
    page = _args[1] if len(_args) > 1 else "home"
    size = _args[2] if len(_args) > 2 else "1280x800"
    w, h = (int(x) for x in size.lower().split("x"))

    app = create_app([])
    win = create_window()
    if _offscreen:
        # 离屏渲染：窗口不受物理屏幕尺寸限制，可测 4K / 200% 等组合
        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(w, h)
    if _demo:
        _feed(win)
    win.show()
    win.show_page(page)
    app.processEvents()

    def _grab() -> None:
        win.show_page(page)
        app.processEvents()
        pix = win.grab()
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        pix.save(out)
        print(f"saved {out}  {pix.width()}x{pix.height()}  dpr={pix.devicePixelRatio()}"
              f"  factor={_scale}  demo={_demo}")
        app.quit()

    QTimer.singleShot(450, _grab)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
