"""统一动效：时长、缓动、单一定时器调度、减少动态效果。

tkinter 没有 CSS opacity/transform，也没有可用的原生组件过渡。本模块只提供
一个很小的动画时钟，具体绘制仍由各 Canvas 控件完成：

- 全部动画共用一个 ``root.after`` 循环，空闲时完全停止；
- 不为按钮、卡片或日志各建长期定时器；
- 动画只改表现，不触发任务、网络或配置写入；
- Windows「关闭动画效果」、TBE_REDUCED_MOTION=1 / TBE_NO_ANIM=1 时，
  所有动画直接落到最终状态，功能与真实数据更新不受影响。
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional

# 统一时长（毫秒）。同类交互不各自发明速度。
HOVER = 150
PRESS = 100
CARD = 190
PAGE = 220
STATUS = 190
PROGRESS = 280
LOG = 150
WINDOW = 180
STAGGER = 40

_root = None
_enabled = True
_job: Optional[str] = None
_animations: set = set()


def _system_allows_animation() -> bool:
    """读取 Windows 的「动画效果」开关；读不到时默认允许。"""
    if os.name != "nt":
        return True
    try:
        import ctypes

        value = ctypes.c_bool(True)
        # SPI_GETCLIENTAREAANIMATION = 0x1042
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042, 0, ctypes.byref(value), 0
        )
        return bool(value.value) if ok else True
    except Exception:  # noqa: BLE001
        return True


def init(root) -> bool:
    """在创建 Tk 根窗口后调用一次。返回当前是否允许动效。"""
    global _root, _enabled
    _root = root
    forced_off = os.environ.get("TBE_REDUCED_MOTION", "") == "1" or \
        os.environ.get("TBE_NO_ANIM", "") == "1"
    _enabled = not forced_off and _system_allows_animation()
    return _enabled


def is_enabled() -> bool:
    return bool(_enabled and _root is not None)


def set_enabled(value: bool) -> None:
    """供测试或设置项使用；关闭时正在播放的动画立即完成。"""
    global _enabled
    _enabled = bool(value)
    if not _enabled:
        for anim in list(_animations):
            anim.finish()


def ease(t: float) -> float:
    """cubic-bezier(0.22, 1, 0.36, 1)，输入进度，输出缓动后的进度。

    用二分法由 x 求参数 t，避免把"时间"误当成曲线的横坐标。动画每帧只有
    少量控件，8 次迭代足够，且比维护一张查找表更直观。
    """
    t = max(0.0, min(1.0, float(t)))
    if t in (0.0, 1.0):
        return t
    x1, y1, x2, y2 = 0.22, 1.0, 0.36, 1.0
    lo, hi = 0.0, 1.0
    u = t
    for _ in range(8):
        x = 3 * (1 - u) ** 2 * u * x1 + 3 * (1 - u) * u * u * x2 + u ** 3
        if x < t:
            lo = u
        else:
            hi = u
        u = (lo + hi) / 2
    y = 3 * (1 - u) ** 2 * u * y1 + 3 * (1 - u) * u * u * y2 + u ** 3
    return max(0.0, min(1.0, y))


@dataclass(eq=False)
class Handle:
    """一个可取消的短动画。只在主线程创建和修改。"""

    duration: int
    update: Callable[[float], None]
    done: Optional[Callable] = None
    delay: int = 0
    easing: Callable[[float], float] = ease
    start: float = 0.0
    active: bool = True

    def cancel(self) -> None:
        self.active = False
        _animations.discard(self)
        _stop_if_idle()

    def finish(self) -> None:
        if not self.active:
            return
        self.active = False
        _animations.discard(self)
        try:
            self.update(1.0)
        finally:
            if self.done:
                self.done()


def _stop_if_idle() -> None:
    global _job
    if not _animations and _job is not None and _root is not None:
        try:
            _root.after_cancel(_job)
        except Exception:  # noqa: BLE001
            pass
        _job = None


def animate(duration: int, update: Callable[[float], None],
            done: Optional[Callable] = None, *, delay: int = 0,
            easing: Callable[[float], float] = ease) -> Handle:
    """启动一个短动画。减少动态效果时同步完成，不排队、不延迟任务。"""
    anim = Handle(duration, update, done, delay, easing)
    if not is_enabled():
        anim.finish()
        return anim
    anim.start = time.monotonic() + delay / 1000.0
    _animations.add(anim)
    _ensure_ticking()
    return anim


def _ensure_ticking() -> None:
    global _job
    if _job is None and _root is not None:
        _job = _root.after(16, _tick)


def _tick() -> None:
    global _job
    _job = None
    now = time.monotonic()
    for anim in list(_animations):
        if not anim.active:
            continue
        if now < anim.start:
            continue
        raw = min(1.0, (now - anim.start) * 1000.0 / max(1, anim.duration))
        try:
            anim.update(anim.easing(raw))
        except Exception:  # noqa: BLE001 - 控件可能刚好被销毁
            anim.active = False
            _animations.discard(anim)
            continue
        if raw >= 1.0:
            anim.active = False
            _animations.discard(anim)
            if anim.done:
                try:
                    anim.done()
                except Exception:  # noqa: BLE001
                    pass
    if _animations:
        _ensure_ticking()


def fade_window(root, duration: int = WINDOW) -> None:
    """原生整窗淡入，只用于程序第一次显示，绝不用于页面切换。"""
    if not is_enabled():
        try:
            root.attributes("-alpha", 1.0)
        except Exception:  # noqa: BLE001
            pass
        return
    try:
        root.attributes("-alpha", 0.0)
        animate(duration, lambda t: root.attributes("-alpha", t),
                done=lambda: root.attributes("-alpha", 1.0))
    except Exception:  # noqa: BLE001
        try:
            root.attributes("-alpha", 1.0)
        except Exception:  # noqa: BLE001
            pass


def slide_in(widget, distance: int = 8, duration: int = PAGE,
             delay: int = 0) -> Handle:
    """用 place 做极轻纵向位移；调用前控件已由 place 管理。"""
    if not is_enabled():
        widget.place_configure(y=0)
        return animate(0, lambda _t: None)

    widget.place_configure(y=distance)
    return animate(duration, lambda t: widget.place_configure(
        y=round(distance * (1.0 - t))), done=lambda: widget.place_configure(y=0),
        delay=delay)


def active_count() -> int:
    """测试与性能排查用。"""
    return len(_animations)
