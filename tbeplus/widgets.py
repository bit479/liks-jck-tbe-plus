"""自绘控件：全部基于标准库 tkinter 的 Canvas，不引入任何第三方库。

为什么不用 ttk？ttk 在 Windows 上用原生主题绘制，颜色几乎改不动，
做不出圆角、悬停态和这套浅色卡片。这里用 Canvas 画形状、自己处理
鼠标事件——代价是代码多几百行，好处是"零依赖"这条承诺还能守住。

控件清单：
    Panel      圆角卡片（内容放 .body）
    Btn        按钮：primary / secondary / ghost / danger
    StatCard   指标卡
    Bar        进度条
    Chip       可选中胶囊（板块开关、日志过滤）
    Switch     开关
    Stepper    数字步进器
    NavItem    左侧导航项
    StatusPill 状态胶囊
    LogView    日志控制台（按级别上色 + 过滤 + 空状态）
"""

from __future__ import annotations

import math
import re
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import BOTH, LEFT, RIGHT, Y
from typing import Callable, Optional

from . import motion
from . import theme as T
from .theme import C

# 逐条明细里出现的状态词（用来把"状态"和"标题"分成两种颜色）
DETAIL_LABELS = (
    "点赞已受理待复查", "新点赞", "已确认点赞", "原本已点赞", "待点赞",
    "点赞失败", "读状态失败", "没看到状态变化",
    "liked", "digg-sent", "already-liked", "dry-run", "like-unconfirmed",
    "digg-unconfirmed", "like-failed", "read-failed", "no-change", "need-login",
)

_font_cache: dict = {}


def measure(spec: tuple, text: str) -> int:
    key = tuple(spec)
    fnt = _font_cache.get(key)
    if fnt is None:
        fnt = tkfont.Font(font=spec)
        _font_cache[key] = fnt
    return fnt.measure(text)


# ---------------------------------------------------------------- 形状
def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """在画布上画圆角矩形。

    Tk Canvas 没有抗锯齿，能做的是两件事：
    1. 圆弧切得足够细（每角 18 段，原来 6 段）；
    2. 1px 边框不用 Tk 的 ``outline``（那是最"毛"的 1px 线算法），改成
       "外扩 1px 的实心多边形打底 + 内层填充"叠出边框，边缘干净很多。
    真正的 AA 仍需要位图或系统级圆角。
    """
    r = max(0.0, min(float(r), (x2 - x1) / 2, (y2 - y1) / 2))

    def _pts(a, b, c, d, rad):
        out: list[float] = []
        steps = 18
        for cx, cy, start in (
            (c - rad, b + rad, -90),
            (c - rad, d - rad, 0),
            (a + rad, d - rad, 90),
            (a + rad, b + rad, 180),
        ):
            for i in range(steps + 1):
                ang = math.radians(start + 90 * i / steps)
                out.extend((cx + rad * math.cos(ang),
                            cy + rad * math.sin(ang)))
        return out

    outline = kw.pop("outline", "")
    width = kw.pop("width", 0)
    fill = kw.get("fill", "")
    if outline and fill not in ("", None) and (width or 0) <= 1 \
            and "stipple" not in kw:
        canvas.create_polygon(_pts(x1 - 1, y1 - 1, x2 + 1, y2 + 1, r + 1),
                              fill=outline, outline="")
        return canvas.create_polygon(_pts(x1, y1, x2, y2, r),
                                     outline="", **kw)
    return canvas.create_polygon(_pts(x1, y1, x2, y2, r),
                                 outline=outline, width=width, **kw)


def blend(color: str, other: str, ratio: float) -> str:
    """把两个 #RRGGBB 混一混，ratio=0 取 color，1 取 other。"""
    def parts(c: str) -> tuple:
        c = c.lstrip("#")
        return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))

    a, b = parts(color), parts(other)
    return "#%02X%02X%02X" % tuple(
        round(a[i] + (b[i] - a[i]) * ratio) for i in range(3)
    )


# ---------------------------------------------------------------- 卡片
class Panel(tk.Canvas):
    """圆角卡片：自己画底，内容放进 .body（一个普通 Frame）。

    子控件在 Tk 里永远浮在 Canvas 绘制物之上，所以"先画圆角底、再摆 Frame"
    的顺序天然成立，圆角不会被 Frame 的直角盖掉。
    """

    def __init__(
        self,
        master,
        radius: int = T.RADIUS_CARD,
        fill: str = C["surface"],
        stroke: str = C["stroke"],
        inset: int = 1,
        bg: Optional[str] = None,
        **kw,
    ):
        # Canvas 默认请求 381x265，会让"自适应高度"的面板把容器撑破
        kw.setdefault("height", 10)
        kw.setdefault("width", 10)
        super().__init__(master, highlightthickness=0, bd=0, bg=bg or C["bg"], **kw)
        self._radius = radius
        self._fill = fill
        self._stroke = stroke
        self._inset = inset
        self.body = tk.Frame(self, bg=fill, highlightthickness=0, bd=0)
        self.bind("<Configure>", self._redraw)

    def fit(self, extra: int = 0) -> None:
        """按内容高度把自己撑到刚好——卡片高度不写死数字，改文案不会挤破。

        刚 pack 完的子控件要等一轮空闲才把"请求高度"传上来，
        所以先算一次，再挂一个 after_idle 补算，避免卡片少了十几像素把内容切掉。
        """
        self._fit_extra = int(extra)
        self._fit_apply()
        if getattr(self, "_fit_job", None) is None:
            self._fit_job = self.after_idle(self._fit_later)

    def _fit_later(self) -> None:
        self._fit_job = None
        if not self.winfo_exists():
            return
        self._fit_apply()
        # 再补一轮：刚重排过的子控件有时要两轮空闲才把请求高度传上来
        self._fit_job = self.after_idle(self._fit_final)

    def _fit_final(self) -> None:
        self._fit_job = None
        if self.winfo_exists():
            self._fit_apply()

    def _fit_apply(self) -> None:
        self.update_idletasks()
        self.configure(
            height=self.body.winfo_reqheight() + 2 * self._inset
            + getattr(self, "_fit_extra", 0)
        )

    def _redraw(self, _event=None) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        self.delete("bgshape")
        round_rect(
            self, 1, 1, w - 1, h - 1, self._radius,
            fill=self._fill, outline=self._stroke, width=1, tags="bgshape",
        )
        self.tag_lower("bgshape")
        i = self._inset
        self.body.place(x=i, y=i, width=w - 2 * i, height=h - 2 * i)


# ---------------------------------------------------------------- 按钮
class Btn(tk.Canvas):
    """按钮。variant：
        primary   主操作（蓝底白字）
        secondary 次要操作（白底细边框）
        ghost     无边框（工具栏、行内操作）
        danger    高风险（红字细边框，只用于停止/删除这类操作）
    """

    PAIRS = {
        "primary": (C["accent"], C["accent_hi"], C["on_dark"], C["accent"]),
        "secondary": (C["surface"], C["surface_hi"], C["text"], C["stroke_hi"]),
        "ghost": (None, C["surface_hi"], C["text2"], None),
        "danger": (C["surface"], C["err_soft"], C["err"], C["err_line"]),
        # 设计稿：试运行=蓝色描边次按钮；查看全部日志=蓝色文字链接
        "outline": (C["surface"], C["accent_soft"], C["accent"], C["accent_line"]),
        "link": (None, C["accent_soft"], C["accent"], None),
    }

    def __init__(
        self,
        master,
        text: str,
        command: Optional[Callable] = None,
        variant: str = "primary",
        icon: str = "",
        width: Optional[int] = None,
        height: int = 36,
        radius: int = T.RADIUS_CTRL,
        size: int = 10,
        bold: bool = False,
        bg: Optional[str] = None,
        pad: int = 16,
    ):
        self._font = T.f(size, bold)
        self._icon_font = T.icon(size - 1)
        self._text = text
        self._icon = icon
        self._pad = pad
        self._h = height
        self._radius = radius
        self._variant = variant
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self._hover_mix = 0.0
        self._press_mix = 0.0
        self._hover_anim = None
        self._press_anim = None
        base_bg = bg
        if base_bg is None:
            try:
                base_bg = master.cget("bg")
            except tk.TclError:
                base_bg = C["surface"]
        self._base_bg = base_bg
        w = width or self._natural_width()
        self._cw = w
        super().__init__(
            master, width=w, height=height, bg=base_bg,
            highlightthickness=0, bd=0, cursor="hand2", takefocus=1,
        )
        self._command = command
        # 键盘也能用：Tab 聚焦 + 回车/空格触发
        self.bind("<Return>", lambda _e: self._invoke())
        self.bind("<space>", lambda _e: self._invoke())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._draw())
        self.bind("<FocusOut>", lambda _e: self._draw())
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _natural_width(self) -> int:
        w = measure(self._font, self._text)
        if self._icon:
            w += measure(self._icon_font, self._icon) + 7
        return w + self._pad * 2

    # -- 对外
    def set_text(self, text: str, icon: Optional[str] = None) -> None:
        self._text = text
        if icon is not None:
            self._icon = icon
        self._cw = self._natural_width()
        self.configure(width=self._cw)
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self._state = "normal" if enabled else "disabled"
        self.configure(cursor="hand2" if enabled else "arrow")
        self._hover_mix = 0.0
        self._press_mix = 0.0
        self._draw()

    def _on_destroy(self, _e) -> None:
        for anim in (self._hover_anim, self._press_anim):
            if anim:
                anim.cancel()

    def _animate_hover(self) -> None:
        if self._hover_anim:
            self._hover_anim.cancel()
        start = self._hover_mix
        target = 1.0 if (self._hover and self._state == "normal") else 0.0

        def update(t: float) -> None:
            self._hover_mix = start + (target - start) * t
            self._draw()

        self._hover_anim = motion.animate(motion.HOVER, update)

    def _animate_press(self) -> None:
        if self._press_anim:
            self._press_anim.cancel()
        start = self._press_mix
        target = 1.0 if self._pressed else 0.0

        def update(t: float) -> None:
            self._press_mix = start + (target - start) * t
            self._draw()

        self._press_anim = motion.animate(motion.PRESS, update)

    # -- 事件
    def _on_enter(self, _e) -> None:
        self._hover = True
        self._animate_hover()

    def _on_leave(self, _e) -> None:
        self._hover = False
        self._pressed = False
        self._animate_hover()
        self._animate_press()

    def _on_press(self, _e) -> None:
        if self._state != "normal":
            return
        self._pressed = True
        self._animate_press()

    def _on_release(self, e) -> None:
        if self._state != "normal":
            return
        self._pressed = False
        self._animate_press()
        if 0 <= e.x <= self._cw and 0 <= e.y <= self._h:
            self._invoke()

    def _invoke(self) -> None:
        if self._state == "normal" and self._command:
            self._command()

    # -- 绘制
    def _draw(self) -> None:
        self.delete("all")
        w, h = int(self._cw), self._h
        base, hover, fg, edge = self.PAIRS.get(self._variant, self.PAIRS["secondary"])

        if base is None:                      # ghost 与容器同色
            base = self._base_bg
        fill = blend(base, hover, self._hover_mix)
        if self._press_mix:
            fill = blend(fill, C["ink"], 0.08 * self._press_mix)
        if self._state == "disabled":
            fill = blend(base, self._base_bg, 0.5)
            fg = C["text3"]
            edge = C["stroke"]

        inset = round(self._press_mix)       # 约 2% 的按压感，不改变控件布局尺寸
        round_rect(
            self, 1 + inset, 1 + inset, w - 1 - inset, h - 1 - inset,
            self._radius, fill=fill, outline=edge or fill,
            width=1 if edge else 0,
        )
        if self.focus_get() is self:      # 键盘焦点提示
            round_rect(self, 0, 0, w, h, self._radius + 1,
                       fill="", outline=C["accent"], width=2)

        total = measure(self._font, self._text)
        if self._icon:
            total += measure(self._icon_font, self._icon) + 7
        x = (w - total) / 2 + inset
        cy = h / 2 + 0.5 + inset
        if self._icon:
            self.create_text(x, cy, text=self._icon, font=self._icon_font,
                             fill=fg, anchor="w")
            x += measure(self._icon_font, self._icon) + 7
        self.create_text(x, cy, text=self._text, font=self._font, fill=fg,
                         anchor="w")


# ---------------------------------------------------------------- 指标卡
class StatCard(tk.Canvas):
    """指标卡：小标题 + 大数字 + 右侧说明。宽度随容器拉伸。"""

    def __init__(
        self,
        master,
        title: str,
        accent: str = C["accent"],
        glyph: str = "\uE8A5",
        unit: str = "篇",
        note: str = "等待运行",
    ):
        super().__init__(master, width=10, height=86, bg=C["bg"],
                         highlightthickness=0, bd=0)
        self._title = title
        self._accent = accent
        self._glyph = glyph
        self._unit = unit
        self._note = note
        self._value = 0
        self._total: Optional[int] = None
        self._hover = False
        self._hover_mix = 0.0
        self._pulse = 0.0
        self._last_pulse = 0.0
        self._hover_anim = None
        self._pulse_anim = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Destroy>", self._on_destroy)
        self.after(30, self._draw)

    def _on_destroy(self, _e) -> None:
        for anim in (self._hover_anim, self._pulse_anim):
            if anim:
                anim.cancel()

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        if self._hover_anim:
            self._hover_anim.cancel()
        start = self._hover_mix
        target = 1.0 if on else 0.0

        def update(t: float) -> None:
            self._hover_mix = start + (target - start) * t
            self._draw()

        self._hover_anim = motion.animate(motion.CARD, update)

    def reveal(self, delay: int = 0) -> None:
        """首屏用：在同一 Canvas 上以网点遮罩做轻淡入，不改布局。"""
        if not motion.is_enabled():
            return
        self._draw()
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 4 or h <= 4:
            return
        mask = round_rect(self, 1, 1, w - 1, h - 1, T.RADIUS_CARD,
                          fill=C["surface"], outline=C["surface"],
                          stipple="gray75", tags="intro-mask")
        steps = ("gray75", "gray50", "gray25", "gray12")

        def update(t: float) -> None:
            self.itemconfigure(mask, stipple=steps[min(3, int(t * 4))])

        motion.animate(motion.CARD, update,
                       done=lambda: self.delete(mask), delay=delay)

    def set(self, value: int, note: Optional[str] = None,
            accent: Optional[str] = None,
            total: Optional[int] = None) -> None:
        value = int(value)
        changed = value != self._value
        self._value = value
        if total is not None:
            self._total = int(total)
        if note is not None:
            self._note = note
        if accent:
            self._accent = accent
        now = time.monotonic()
        if changed and now - self._last_pulse > 0.35:
            self._last_pulse = now
            if self._pulse_anim:
                self._pulse_anim.cancel()
            self._pulse = 1.0

            def update(t: float) -> None:
                self._pulse = 1.0 - t
                self._draw()

            self._pulse_anim = motion.animate(motion.STATUS, update)
        self._draw()

    def _draw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 4 or h <= 4:
            return
        self.delete("all")
        border = blend(C["stroke"], self._accent, 0.28 * self._hover_mix)
        round_rect(self, 1, 1, w - 1, h - 1, T.RADIUS_CARD,
                   fill=C["surface"], outline=border, width=1)
        # 左侧 3px 强调条：颜色对应指标语义，不抢文字
        round_rect(self, 1, 18, 4, h - 18, 1.5,
                   fill=self._accent, outline="", width=0)

        self.create_text(20, 24, text=self._glyph, font=T.icon(9),
                         fill=self._accent, anchor="w")
        self.create_text(38, 24, text=self._title, font=T.f(10),
                         fill=C["text2"], anchor="w")

        num_font = T.f(20, True)
        label = f"{self._value:,}"
        self.create_text(20, 52, text=label, font=num_font,
                         fill=blend(C["text"], self._accent, self._pulse), anchor="w")
        nw = measure(num_font, label)
        x = 24 + nw
        if self._total is not None:
            total_font = T.f(9)
            total_label = f"/ {self._total:,} {self._unit}"
            self.create_text(x, 56, text=total_label, font=total_font,
                             fill=C["text3"], anchor="w")
            if self._total:
                frac = min(1.0, self._value / self._total)
                round_rect(self, 20, h - 8, w - 20, h - 4, 2,
                           fill=C["surface_hi"], outline="", width=0)
                round_rect(self, 20, h - 8, 20 + (w - 40) * frac, h - 4, 2,
                           fill=self._accent, outline="", width=0)
        else:
            self.create_text(x, 56, text=self._unit, font=T.f(9),
                             fill=C["text3"], anchor="w")
        if self._note and (self._total is not None or w >= 190) and w >= 150:
            note_y = 24 if self._total is not None else 54
            self.create_text(w - 16, note_y, text=self._note, font=T.f(9),
                             fill=C["text3"], anchor="e")


# ---------------------------------------------------------------- 进度条
class Bar(tk.Canvas):
    """细进度条：轨道 + 圆角填充。frac 0~1。"""

    def __init__(self, master, height: int = 6, color: str = C["accent"],
                 track: str = C["stroke"], bg: Optional[str] = None):
        # Canvas 默认要 381px 宽，放进 pack(expand) 里会把后面的标签挤出界
        super().__init__(master, width=8, height=height,
                         bg=bg or C["surface"], highlightthickness=0, bd=0)
        self._h = height
        self._color = color
        self._track = track
        self._frac = 0.0                 # 真实目标值，立即更新
        self._display_frac = 0.0         # 画布上的平滑显示值
        self._anim = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Destroy>", lambda _e: self._anim and self._anim.cancel())

    def set(self, frac: float, immediate: bool = False) -> None:
        frac = max(0.0, min(1.0, float(frac)))
        if abs(frac - self._frac) < 0.002 and not immediate:
            return
        if self._anim:
            self._anim.cancel()
        self._frac = frac
        start = self._display_frac
        if immediate or frac == 0.0:
            self._display_frac = frac
            self._draw()
            return

        def update(t: float) -> None:
            self._display_frac = start + (frac - start) * t
            self._draw()

        self._anim = motion.animate(motion.PROGRESS, update)

    def set_color(self, color: str) -> None:
        self._color = color
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w <= 4:
            return
        h = self._h
        r = h / 2
        round_rect(self, 0, 0, w, h, r, fill=self._track, outline="", width=0)
        if self._display_frac > 0:
            round_rect(self, 0, 0, max(h, w * self._display_frac), h, r,
                       fill=self._color, outline="", width=0)


# ---------------------------------------------------------------- 流程步骤
class Steps(tk.Canvas):
    """三/四步流程：连接 → 读取 → 点赞。active=蓝，done=绿，error=红。"""

    def __init__(self, master, labels, height: int = 38,
                 bg: Optional[str] = None):
        super().__init__(master, width=10, height=height,
                         bg=bg or C["surface"], highlightthickness=0, bd=0)
        self._labels = list(labels)
        self._states = ["idle"] * len(labels)
        self._ch = height
        self.bind("<Configure>", lambda _e: self._draw())

    def set_states(self, states) -> None:
        states = list(states)[:len(self._labels)]
        states += ["idle"] * (len(self._labels) - len(states))
        if states == self._states:
            return
        self._states = states
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w <= 10:
            return
        n = len(self._labels)
        col = w / n
        cy = 12
        for i, label in enumerate(self._labels):
            state = self._states[i]
            cx = col * i + 14
            color = {
                "idle": C["stroke_hi"], "active": C["accent"],
                "done": C["ok"], "error": C["err"],
            }.get(state, C["stroke_hi"])
            if state == "active":
                self.create_oval(cx - 8, cy - 8, cx + 8, cy + 8,
                                 fill=C["accent_soft"], outline="")
            self.create_oval(cx - 6, cy - 6, cx + 6, cy + 6,
                             fill=color, outline="")
            if state == "done":
                self.create_text(cx, cy, text="\uE73E", font=T.icon(6),
                                 fill=C["on_dark"])
            elif state == "error":
                self.create_text(cx, cy, text="\uE711", font=T.icon(6),
                                 fill=C["on_dark"])
            else:
                self.create_text(cx, cy, text=str(i + 1), font=T.f(7, True),
                                 fill=C["on_dark"] if state != "idle" else C["text3"])
            self.create_text(cx + 12, cy, text=label, font=T.f(9),
                             fill=C["text"] if state != "idle" else C["text3"],
                             anchor="w")
            if i < n - 1:
                x1 = cx + 12 + measure(T.f(9), label) + 8
                x2 = col * (i + 1) + 2
                if x2 > x1:
                    self.create_line(x1, cy, x2, cy, fill=C["stroke"], width=1)


# ---------------------------------------------------------------- 堆叠分布条
class SegBar(tk.Canvas):
    """处理进度分布：已点赞 / 原本已赞 / 待处理 / 失败。"""

    def __init__(self, master, height: int = 14, bg: Optional[str] = None):
        super().__init__(master, width=10, height=height,
                         bg=bg or C["surface"], highlightthickness=0, bd=0)
        self._ch = height
        self._values: list = []
        self._colors: list = []
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, values, colors) -> None:
        self._values = [max(0.0, float(v)) for v in values]
        self._colors = list(colors)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w <= 6:
            return
        total = sum(self._values)
        if total <= 0:
            round_rect(self, 0, 2, w, self._ch - 2, (self._ch - 4) / 2,
                       fill=C["surface_hi"], outline="", width=0)
            return
        x = 0.0
        for value, color in zip(self._values, self._colors):
            if value <= 0:
                continue
            span = w * value / total
            if span < 3:
                x += span
                continue
            round_rect(self, x, 2, min(w, x + span), self._ch - 2,
                       (self._ch - 4) / 2, fill=color, outline="", width=0)
            x += span


# ---------------------------------------------------------------- 胶囊 / 开关
class Chip(tk.Canvas):
    """可选中的小胶囊（板块开关、日志过滤）。"""

    def __init__(
        self,
        master,
        text: str,
        value: bool = False,
        command: Optional[Callable] = None,
        height: int = 28,
        size: int = 9,
        bg: Optional[str] = None,
        solid: bool = False,
    ):
        self._text = text
        self._font = T.f(size)
        self._value = value
        self._command = command
        self._h = height
        self._solid = solid
        self._hover = False
        self._mix = 1.0 if value else 0.0
        self._hover_mix = 0.0
        self._anim = None
        self._hover_anim = None
        w = measure(self._font, text) + 32
        super().__init__(master, width=w, height=height,
                         bg=bg or C["surface"], highlightthickness=0, bd=0,
                         cursor="hand2")
        self._cw = w
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._click)
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _on_destroy(self, _e) -> None:
        for anim in (self._anim, self._hover_anim):
            if anim:
                anim.cancel()

    def _animate(self, hover: bool = False) -> None:
        attr = "_hover_anim" if hover else "_anim"
        old = getattr(self, attr)
        if old:
            old.cancel()
        start = self._hover_mix if hover else self._mix
        target = (1.0 if self._hover else 0.0) if hover else \
            (1.0 if self._value else 0.0)

        def update(t: float) -> None:
            value = start + (target - start) * t
            if hover:
                self._hover_mix = value
            else:
                self._mix = value
            self._draw()

        setattr(self, attr, motion.animate(
            motion.HOVER if hover else motion.STATUS, update))

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        self._animate(hover=True)

    def _click(self, _e) -> None:
        self._value = not self._value
        self._animate()
        if self._command:
            self._command()

    def get(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = bool(value)
        self._animate()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self._cw, self._h
        if self._solid:
            # 设计稿的日志筛选：选中=实心蓝底白字，未选中=白底灰字细边
            off_fill = blend(C["surface"], C["surface_hover"], self._hover_mix)
            off_edge = blend(C["stroke"], C["stroke_hi"], self._hover_mix)
            fill = blend(off_fill, C["accent"], self._mix)
            fill = blend(fill, C["accent_hi"], self._hover_mix * self._mix)
            edge = blend(off_edge, C["accent"], self._mix)
            fg = blend(C["slate"], C["on_dark"], self._mix)
            round_rect(self, 1, 1, w - 1, h - 1, h / 2,
                       fill=fill, outline=edge, width=1)
            self.create_text(w / 2, h / 2, text=self._text, font=self._font,
                             fill=fg, anchor="center")
            return
        off_fill = blend(C["surface"], C["surface_hover"], self._hover_mix)
        off_edge = blend(C["stroke"], C["stroke_hi"], self._hover_mix)
        fill = blend(off_fill, C["accent_soft"], self._mix)
        edge = blend(off_edge, C["accent_line"], self._mix)
        fg = blend(C["slate"], C["accent_hi"], self._mix)
        dot = blend(C["stroke_hi"], C["accent"], self._mix)
        round_rect(self, 1, 1, w - 1, h - 1, h / 2,
                   fill=fill, outline=edge, width=1)
        self.create_oval(11, h / 2 - 2.5, 16, h / 2 + 2.5, fill=dot, outline="")
        self.create_text(22, h / 2, text=self._text, font=self._font,
                         fill=fg, anchor="w")


class Switch(tk.Canvas):
    """滑动开关。"""

    def __init__(
        self,
        master,
        value: bool = False,
        command: Optional[Callable] = None,
        bg: Optional[str] = None,
    ):
        super().__init__(master, width=38, height=22, bg=bg or C["surface"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self._value = bool(value)
        self._command = command
        self._mix = 1.0 if value else 0.0
        self._anim = None
        self.bind("<Button-1>", self._click)
        self.bind("<Destroy>", lambda _e: self._anim and self._anim.cancel())
        self._draw()

    def _animate(self) -> None:
        if self._anim:
            self._anim.cancel()
        start = self._mix
        target = 1.0 if self._value else 0.0

        def update(t: float) -> None:
            self._mix = start + (target - start) * t
            self._draw()

        self._anim = motion.animate(motion.STATUS, update)

    def _click(self, _e) -> None:
        self._value = not self._value
        self._animate()
        if self._command:
            self._command()

    def get(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = bool(value)
        self._animate()

    def _draw(self) -> None:
        self.delete("all")
        track = blend(C["stroke_hi"], C["accent"], self._mix)
        round_rect(self, 1, 3, 37, 19, 8, fill=track, outline="", width=0)
        cx = 9 + 20 * self._mix
        self.create_oval(cx - 7, 4, cx + 7, 18, fill=C["on_dark"],
                         outline=C["stroke"] if self._mix < 0.5 else "")


class Stepper(tk.Canvas):
    """数字步进器： −  12  +  """

    def __init__(
        self,
        master,
        value: int = 0,
        from_: int = 0,
        to: int = 999,
        step: int = 1,
        command: Optional[Callable] = None,
        width: int = 92,
        bg: Optional[str] = None,
    ):
        super().__init__(master, width=width, height=30, bg=bg or C["surface"],
                         highlightthickness=0, bd=0)
        self._value = int(value)
        self._from, self._to, self._step = from_, to, step
        self._command = command
        self._cw = width
        self._hover_zone = 0
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(0))
        self._draw()

    def get(self) -> int:
        return self._value

    def set(self, value: int) -> None:
        self._value = max(self._from, min(self._to, int(value)))
        self._draw()

    def _zone(self, x: int) -> int:
        if x < 28:
            return -1
        if x > self._cw - 28:
            return 1
        return 0

    def _bump(self, delta: int) -> None:
        new = max(self._from, min(self._to, self._value + delta * self._step))
        if new != self._value:
            self._value = new
            self._draw()
            if self._command:
                self._command()

    def _click(self, e) -> None:
        zone = self._zone(e.x)
        if zone:
            self._bump(zone)

    def _set_hover(self, zone: int) -> None:
        self._hover_zone = zone
        self.configure(cursor="hand2" if zone else "xterm")
        self._draw()

    def _motion(self, e) -> None:
        zone = self._zone(e.x)
        if zone != self._hover_zone:
            self._set_hover(zone)

    def _draw(self) -> None:
        self.delete("all")
        w, h = self._cw, 30
        round_rect(self, 1, 1, w - 1, h - 1, T.RADIUS_CTRL,
                   fill=C["surface"], outline=C["stroke_hi"], width=1)
        for zone, x, glyph in ((-1, 15, "\uE738"), (1, w - 15, "\uE710")):
            if self._hover_zone == zone:
                self.create_oval(x - 10, 5, x + 10, h - 5,
                                 fill=C["surface_hi"], outline="")
            self.create_text(x, h / 2, text=glyph, font=T.icon(7),
                             fill=C["text2"])
        self.create_text(w / 2, h / 2, text=str(self._value), font=T.f(10, True),
                         fill=C["text"])


# ---------------------------------------------------------------- 导航
class NavItem(tk.Canvas):
    """左侧深色导航项：整行高亮 + 左侧强调条。"""

    def __init__(
        self,
        master,
        text: str,
        glyph: str,
        command: Optional[Callable] = None,
        active: bool = False,
        width: int = T.NAV_W,
        height: int = 40,
    ):
        super().__init__(master, width=width, height=height,
                         bg=C["nav"], highlightthickness=0, bd=0,
                         cursor="hand2")
        self._text = text
        self._glyph = glyph
        self._command = command
        self._active = active
        self._hover = False
        self._active_mix = 1.0 if active else 0.0
        self._hover_mix = 0.0
        self._active_anim = None
        self._hover_anim = None
        self._cw, self._ch = width, height
        self.bind("<Enter>", lambda _e: self._hover_in(True))
        self.bind("<Leave>", lambda _e: self._hover_in(False))
        self.bind("<Button-1>", self._click)
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _on_destroy(self, _e) -> None:
        for anim in (self._active_anim, self._hover_anim):
            if anim:
                anim.cancel()

    def _animate(self, hover: bool = False) -> None:
        attr = "_hover_anim" if hover else "_active_anim"
        old = getattr(self, attr)
        if old:
            old.cancel()
        start = self._hover_mix if hover else self._active_mix
        target = (1.0 if self._hover else 0.0) if hover else \
            (1.0 if self._active else 0.0)

        def update(t: float) -> None:
            value = start + (target - start) * t
            if hover:
                self._hover_mix = value
            else:
                self._active_mix = value
            self._draw()

        setattr(self, attr, motion.animate(
            motion.HOVER if hover else motion.STATUS, update))

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._animate()

    def _hover_in(self, on: bool) -> None:
        self._hover = on
        self._animate(hover=True)

    def _click(self, _e) -> None:
        if self._command:
            self._command()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self._cw, self._ch
        # 未选中：nav -> nav_hover -> nav_active 的灰阶过渡；
        # 选中：整块混到品牌蓝（设计稿的蓝色选中态），悬停时再亮一档。
        hover_bg = blend(C["nav"], C["nav_hover"], self._hover_mix)
        off_bg = blend(hover_bg, C["nav_active"], self._active_mix)
        bg = blend(off_bg, C["nav_sel"], self._active_mix)
        if self._active_mix > 0.01:
            bg = blend(bg, C["nav_sel_hover"], self._hover_mix * 0.5)
        if self._active_mix > 0.01 or self._hover_mix > 0.01:
            round_rect(self, 0, 2, w, h - 2, 8, fill=bg, outline="", width=0)
        fg = blend(C["nav_muted"], C["on_dark"], self._active_mix)
        icon_fg = blend(C["nav_icon"], C["on_dark"], self._active_mix)
        if not self._active and self._hover_mix:
            fg = blend(fg, C["nav_text"], self._hover_mix)
            icon_fg = blend(icon_fg, C["nav_muted"], self._hover_mix)
        self.create_text(20, h / 2, text=self._glyph, font=T.icon(11), fill=icon_fg)
        self.create_text(42, h / 2, text=self._text,
                         font=T.f(10, self._active), fill=fg, anchor="w")


class StatusPill(tk.Canvas):
    """状态胶囊：● 空闲 / ● 运行中。"""

    TONES = {
        "idle": (C["text3"], C["surface_hi"], C["text2"]),
        "run": (C["accent"], C["accent_soft"], C["accent_hi"]),
        "ok": (C["ok"], C["ok_soft"], C["ok_deep"]),
        "warn": (C["warn"], C["warn_soft"], C["warn_deep"]),
        "err": (C["err"], C["err_soft"], C["err_deep"]),
    }

    def __init__(self, master, text: str = "空闲", tone: str = "idle",
                 bg: Optional[str] = None):
        super().__init__(master, width=90, height=26, bg=bg or C["bg"],
                         highlightthickness=0, bd=0)
        self._text = text
        self._tone = tone
        self._colors = self.TONES.get(tone, self.TONES["idle"])
        self._anim = None
        self._cw = measure(T.f(9), text) + 34
        self.configure(width=self._cw)
        self.bind("<Destroy>", lambda _e: self._anim and self._anim.cancel())
        self._draw()

    def set(self, text: str, tone: str = "idle") -> None:
        old = self._colors
        new = self.TONES.get(tone, self.TONES["idle"])
        self._text = text
        self._tone = tone
        self._cw = measure(T.f(9), text) + 34
        self.configure(width=self._cw)
        if self._anim:
            self._anim.cancel()

        def update(t: float) -> None:
            self._colors = tuple(blend(old[i], new[i], t) for i in range(3))
            self._draw()

        self._anim = motion.animate(motion.STATUS, update)

    def _draw(self) -> None:
        self.delete("all")
        w, h = int(self._cw), 26
        dot, fill, fg = self._colors
        round_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=fill,
                   outline=dot if self._tone != "idle" else C["stroke"], width=1)
        self.create_oval(12, h / 2 - 3, 18, h / 2 + 3, fill=dot, outline="")
        self.create_text(24, h / 2, text=self._text, font=T.f(9), fill=fg,
                         anchor="w")


# ---------------------------------------------------------------- 滚动条
class SlimScroll(tk.Canvas):
    """细滚动条：Windows 原生滚动条改不了颜色，浅色卡片上太扎眼。"""

    def __init__(self, master, target: tk.Text, width: int = 8,
                 bg: str = C["term"]):
        super().__init__(master, width=width, bg=bg, highlightthickness=0, bd=0)
        self._target = target
        self._first, self._last = 0.0, 1.0
        self._cw = width
        self._drag = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))
        self._draw()

    def set(self, first, last) -> None:
        self._first, self._last = float(first), float(last)
        self._draw()

    def _thumb(self) -> tuple:
        h = self.winfo_height()
        if h <= 10:
            return 0, 0
        span = self._last - self._first
        th = max(28, h * span)
        y = (h - th) * (self._first / max(1e-9, 1 - span)) if span < 1 else 0
        return y, th

    def _draw(self) -> None:
        self.delete("all")
        h = self.winfo_height()
        if h <= 10:
            return
        y, th = self._thumb()
        round_rect(self, 2, y + 2, self._cw - 2, y + th - 2,
                   (self._cw - 4) / 2, fill=C["stroke_hi"], outline="", width=0)

    def _press(self, e) -> None:
        y, th = self._thumb()
        if y <= e.y <= y + th:
            self._drag = e.y - y
        else:
            self._drag = th / 2
            self._move(e)

    def _move(self, e) -> None:
        h = self.winfo_height()
        _, th = self._thumb()
        if h - th <= 0:
            return
        offset = (e.y - (self._drag or 0)) / (h - th)
        self._target.yview_moveto(max(0.0, min(1.0, offset)))


# ---------------------------------------------------------------- 滚动区
class ScrollArea(tk.Frame):
    """可纵向滚动的容器：内容放 .inner。

    窗口高度不够时（笔记本小屏很常见）让内容自己滚，而不是把卡片压扁、
    把文字切掉。内容装得下时滚动条自动隐藏。
    """

    def __init__(self, master, bg: str = C["bg"]):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self._bg = bg
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0,
                                width=10, height=10)
        self.scroll = SlimScroll(self, self.canvas, bg=bg)
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.scroll.pack(side=RIGHT, fill=Y)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._sync)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.bind("<Enter>", lambda _e: self.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda _e: self.unbind_all("<MouseWheel>"))

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._win, width=event.width)
        self._sync()

    def _sync(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        need = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 2
        if need and not self.scroll.winfo_ismapped():
            self.scroll.pack(side=RIGHT, fill=Y)
        elif not need and self.scroll.winfo_ismapped():
            self.scroll.pack_forget()
            self.canvas.yview_moveto(0)

    def refresh(self) -> None:
        self.after_idle(self._sync)

    def _wheel(self, event) -> str:
        if self.scroll.winfo_ismapped():
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"


# ---------------------------------------------------------------- 日志
class LogView(tk.Frame):
    """事件查看器：时间 + 级别 + 消息 三列，紧凑、可筛选、可暂停滚动。

    时间取"界面收到这一行的时刻"（引擎不产出时间戳，不虚构服务端时间）；
    级别由内容推导；明细行可折叠。
    """

    LEVELS = {
        "dim": C["text3"],
        "text2": C["slate"],
        "text": C["text"],
        "accent": C["accent_hi"],
        "ok": C["ok"],
        "warn": C["warn"],
        "err": C["err"],
        "like": C["accent"],
    }
    CODES = {"dim": "INFO", "text2": "INFO", "text": "INFO", "accent": "INFO",
             "like": "INFO", "ok": " OK ", "warn": "WARN", "err": "ERR "}
    CODE_COLORS = {"INFO": C["text3"], " OK ": C["ok"],
                   "WARN": C["warn"], "ERR ": C["err"]}

    def __init__(self, master, bg: str = C["term"], size: int = 9,
                 empty_title: str = "还没有日志",
                 empty_hint: str = "点「准备并接管客户端」，或直接点「开始点赞」"):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self._bg = bg
        self.text = tk.Text(
            self, bg=bg, fg=C["text"], font=(T.MONO, size),
            relief="flat", bd=0, highlightthickness=0, wrap="word",
            state="disabled", insertwidth=0, padx=14, pady=10, height=6,
            spacing1=2, spacing3=2, tabs=(60, 100),
            selectbackground=C["accent_soft"], selectforeground=C["text"],
        )
        self.scroll = SlimScroll(self, self.text, bg=bg)
        self.text.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y", padx=(0, 4))

        for name, color in self.LEVELS.items():
            self.text.tag_configure(name, foreground=color)
        for code, color in self.CODE_COLORS.items():
            self.text.tag_configure(code, foreground=color)
        self.text.tag_configure("head", font=(T.MONO, size, "bold"))
        self.text.tag_configure("detail", elide=False)
        self.text.tag_configure("quiet", elide=False)

        # 空状态：一个小图标 + 两行说明，不大面积留白
        self.empty = tk.Frame(self, bg=bg)
        box = tk.Frame(self.empty, bg=bg)
        box.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(box, text="\uE7C3", font=T.icon(16), fg=C["text3"],
                 bg=bg).pack()
        tk.Label(box, text=empty_title, font=T.f(10), fg=C["text2"],
                 bg=bg).pack(pady=(6, 2))
        tk.Label(box, text=empty_hint, font=T.f(9), fg=C["text3"],
                 bg=bg).pack()
        self.empty.place(relx=0, rely=0, relwidth=1, relheight=1)

        self._autoscroll = True
        self._has_content = False
        self._mode = "all"
        self._fade_recent: list = []
        self._fade_seq = 0
        self._fade_anim: set = set()
        self.text.bind("<MouseWheel>", self._wheel)
        self.scroll.bind("<MouseWheel>", self._wheel)

    # -- 分类
    @staticmethod
    def classify(line: str) -> tuple:
        """返回 (颜色级别, 过滤分组)。分组：detail=逐条明细，quiet=常规信息。"""
        s = line.strip()
        if not s:
            return "text2", "quiet"
        if set(s) <= {"="} or set(s) <= {"-"}:
            return "dim", "quiet"
        if s.startswith("板块：") or s.startswith("板块（会话）"):
            return "accent", "quiet"
        if "本板块小结" in s:
            return "ok", "quiet"
        if any(k in s for k in ("没成功", "失败", "出错", "[出错]", "⚠", "错误")):
            return "err", "quiet"
        if any(k in s for k in ("重试", "降速", "配额", "缓一缓")):
            return "warn", "quiet"
        if s.startswith("[") and "/" in s.split("]")[0]:
            if any(k in s for k in ("新点赞", "已确认点赞", "liked(", "digg-sent")):
                return "like", "detail"
            if any(k in s for k in ("原本已点赞", "already-liked")):
                return "dim", "detail"
            if any(k in s for k in ("待点赞", "dry-run")):
                return "warn", "detail"
            return "err", "detail"
        if "提示" in s or "注意" in s:
            return "warn", "quiet"
        return "text2", "quiet"

    # -- 写入
    def append(self, line: str, stamp: Optional[str] = None) -> None:
        import time as _time

        level, group = self.classify(line)
        tags = [level, group]
        if level == "accent":
            tags.append("head")
        at_bottom = self.text.yview()[1] >= 0.999
        now = stamp or _time.strftime("%H:%M:%S")
        start_index = self.text.index("end-1c")
        self.text.configure(state="normal")
        self.text.insert("end", f"{now}\t", (group,))
        self.text.insert("end", f"{self.CODES.get(level, 'INFO')}\t",
                         (self.CODES.get(level, "INFO"), group))
        self._insert(line, tuple(tags), group)
        line_count = int(self.text.index("end-1c").split(".")[0])
        if line_count > 3000:
            self.text.delete("1.0", "800.0")
        self.text.configure(state="disabled")
        if at_bottom and self._autoscroll:
            self.text.see("end")
        if not self._has_content:
            self._has_content = True
            self.empty.place_forget()
        self._fade_new_line(start_index, level, group)

    def _fade_new_line(self, start: str, level: str, group: str) -> None:
        """新行轻淡入。高频时自动降级、限制并发，不设长期定时器。

        只对新增的那一行加一个临时 tag，用同一动效时钟把前景色从底色
        过渡到级别色；结束后删掉 tag，级别色立刻归位。用户在看旧日志时
        不强制滚动，淡入不改变消息顺序与内容。
        """
        if not motion.is_enabled():
            return
        now = time.monotonic()
        self._fade_recent = [t for t in self._fade_recent if now - t < 0.5]
        if len(self._fade_recent) >= 8 or len(self._fade_anim) >= 12:
            return
        self._fade_recent.append(now)
        self._fade_seq += 1
        tag = f"_fade{self._fade_seq}"
        color = self.LEVELS.get(level, C["text"])
        self.text.tag_configure(tag, foreground=color)
        end = self.text.index("end-1c")
        self.text.tag_add(tag, start, end)
        handle = {}

        def update(t: float) -> None:
            try:
                self.text.tag_configure(tag, foreground=blend(self._bg, color, t))
            except tk.TclError:
                handle["a"] and handle["a"].cancel()

        def done() -> None:
            self._fade_anim.discard(handle["a"])
            try:
                self.text.tag_delete(tag)
            except tk.TclError:
                pass

        anim = motion.animate(motion.LOG, update, done=done)
        handle["a"] = anim
        self._fade_anim.add(anim)

    def _insert(self, line: str, tags: tuple, group: str) -> None:
        """逐条明细里，状态用彩色、标题用灰色——整行都染色太吵。"""
        if group == "detail":
            for label in DETAIL_LABELS:
                pos = line.find(label)
                if pos > 0:
                    cut = pos + len(label)
                    m = re.match(r"\([^)]*\)", line[cut:])
                    if m:
                        cut += m.end()
                    self.text.insert("end", line[:cut], tags)
                    self.text.insert("end", line[cut:] + "\n", ("text2", group))
                    return
        self.text.insert("end", line + "\n", tags)

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._has_content = False
        self.empty.place(relx=0, rely=0, relwidth=1, relheight=1)

    def dump(self) -> str:
        return self.text.get("1.0", "end-1c")

    def copy_visible(self) -> str:
        """复制当前筛选下看得见的内容（折叠的行不复制）。"""
        out = []
        for tag_ranges in [("1.0", "end")]:
            pass
        # Text 无法直接取"未折叠"文本，按行判断分组标签
        total = int(self.text.index("end-1c").split(".")[0])
        for i in range(1, total + 1):
            line = self.text.get(f"{i}.0", f"{i}.end")
            tags = self.text.tag_names(f"{i}.0")
            if "detail" in tags and self._mode in ("key", "issue"):
                continue
            if "quiet" in tags and self._mode == "issue":
                continue
            out.append(line)
        return "\n".join(out)

    def set_mode(self, mode: str) -> None:
        """all=全部 / key=只看关键 / issue=只看问题。"""
        self._mode = mode
        self.text.tag_configure("detail", elide=mode in ("key", "issue"))
        self.text.tag_configure("quiet", elide=mode == "issue")

    def set_autoscroll(self, on: bool) -> None:
        self._autoscroll = bool(on)
        if self._autoscroll:
            self.text.see("end")

    def _wheel(self, e) -> str:
        self.text.yview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"


# ---------------------------------------------------------------- 日志表格
class LogTable(tk.Frame):
    """概览页的实时日志表：时间 / 级别 / 进度 / 状态 / 资讯标题。

    用 Text 的五列制表位实现，避免几千行 Treeview 的样式与性能问题。
    系统消息把内容放「资讯标题」列，进度和状态显示“—”。
    """

    TABS = (64, 118, 176, 276)
    HEADERS = ("时间", "级别", "进度", "状态", "资讯标题")

    # 级别徽标：浅底 + 语义色文字（设计稿的 INFO/OK/WARN/ERR 小胶囊）
    BADGE = {
        "INFO": (C["accent_hi"], C["accent_soft"]),
        " OK ": (C["ok_deep"], C["ok_soft"]),
        "WARN": (C["warn_deep"], C["warn_soft"]),
        "ERR ": (C["err"], C["err_soft"]),
    }
    # 状态小图标：已点赞=绿勾、已跳过=中性圈、处理中=蓝点、失败=红叉
    STATUS_ICON = {
        "新点赞": "✓ ", "已确认点赞": "✓ ", "liked": "✓ ", "digg-sent": "✓ ",
        "原本已点赞": "○ ", "already-liked": "○ ",
        "待点赞": "● ", "dry-run": "● ",
        "点赞已受理待复查": "● ", "like-unconfirmed": "● ",
        "digg-unconfirmed": "● ",
        "点赞失败": "✕ ", "读状态失败": "✕ ", "没看到状态变化": "✕ ",
        "like-failed": "✕ ", "read-failed": "✕ ", "no-change": "✕ ",
        "need-login": "✕ ",
    }

    STATUS_COLOR = {
        "新点赞": C["ok"], "已确认点赞": C["ok"], "liked": C["ok"],
        "digg-sent": C["ok"],
        "原本已点赞": C["text3"], "already-liked": C["text3"],
        "待点赞": C["accent"], "dry-run": C["accent"],
        "点赞失败": C["err"], "读状态失败": C["err"], "没看到状态变化": C["err"],
        "like-failed": C["err"], "read-failed": C["err"], "no-change": C["err"],
        "need-login": C["err"],
        "点赞已受理待复查": C["warn"], "like-unconfirmed": C["warn"],
        "digg-unconfirmed": C["warn"],
        "板块": C["accent_hi"], "小结": C["ok_deep"], "问题": C["err"],
        "提示": C["warn"],
    }

    def __init__(self, master, bg: str = C["term"], size: int = 9,
                 empty_title: str = "还没有日志",
                 empty_hint: str = "点上方「开始任务」或「试运行」"):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self._bg = bg
        self._rows = 0
        self._search = ""
        self._has_content = False
        self._autoscroll = True
        self._mode = "all"
        self.text = tk.Text(
            self, bg=bg, fg=C["text"], font=(T.MONO, size),
            relief="flat", bd=0, highlightthickness=0, wrap="none",
            state="disabled", insertwidth=0, padx=0, pady=8, height=6,
            spacing1=3, spacing3=3, tabs=self.TABS,
            selectbackground=C["accent_soft"], selectforeground=C["text"],
        )
        self.scroll = SlimScroll(self, self.text, bg=bg)
        self.text.pack(side=LEFT, fill=BOTH, expand=True)
        self.scroll.pack(side=RIGHT, fill=Y, padx=(0, 4))

        for name, color in LogView.LEVELS.items():
            self.text.tag_configure(name, foreground=color)
        for code, color in LogView.CODE_COLORS.items():
            self.text.tag_configure(code, foreground=color)
        for code, (fg, bgc) in self.BADGE.items():
            self.text.tag_configure(f"badge-{code.strip()}",
                                    foreground=fg, background=bgc)
        self.text.tag_configure("head", font=(T.MONO, size, "bold"))
        self.text.tag_configure("detail", elide=False)
        self.text.tag_configure("quiet", elide=False)
        self.text.tag_configure("hide", elide=True)
        self.text.tag_configure("hit", background=C["warn_soft"])
        self.text.tag_configure("status-like", foreground=C["ok"])
        self.text.tag_configure("status-skip", foreground=C["text3"])
        self.text.tag_configure("status-fail", foreground=C["err"])
        self.text.tag_configure("status-run", foreground=C["accent"])
        self.text.bind("<MouseWheel>", self._wheel)

        self.empty = tk.Frame(self, bg=bg)
        box = tk.Frame(self.empty, bg=bg)
        box.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(box, text="\uE7C3", font=T.icon(16), fg=C["text3"],
                 bg=bg).pack()
        tk.Label(box, text=empty_title, font=T.f(10), fg=C["text2"],
                 bg=bg).pack(pady=(6, 2))
        tk.Label(box, text=empty_hint, font=T.f(9), fg=C["text3"],
                 bg=bg).pack()
        self.empty.place(relx=0, rely=0, relwidth=1, relheight=1)

    @staticmethod
    def parse(line: str) -> tuple:
        level, group = LogView.classify(line)
        s = line.strip()
        progress, status, title = "—", "—", s
        m = re.search(r"\[(\d+)\s*/\s*(\d+)\]", s)
        if m:
            progress = f"{m.group(1)}/{m.group(2)}"
            rest = s[m.end():].strip()
            rest = re.sub(r"^\d{2}-\d{2}\s*", "", rest)
            for label in DETAIL_LABELS:
                pos = rest.find(label)
                if pos >= 0:
                    status = label
                    after = rest[pos + len(label):]
                    after = re.sub(r"^\([^)]*\)\s*", "", after)
                    title = after.strip() or rest
                    break
            else:
                title = rest
        elif group == "quiet" and level in ("accent", "ok", "warn", "err"):
            status = {"accent": "板块", "ok": "小结",
                      "warn": "提示", "err": "问题"}[level]
            title = s
        return level, group, progress, status, title

    def append(self, line: str, stamp: Optional[str] = None) -> None:
        import time as _time

        level, group, progress, status, title = self.parse(line)
        at_bottom = self.text.yview()[1] >= 0.999
        now = stamp or _time.strftime("%H:%M:%S")
        self._rows += 1
        row = f"row{self._rows}"
        code = LogView.CODES.get(level, "INFO")
        self.text.configure(state="normal")
        self.text.insert("end", f"{now}\t", (group, row))
        self.text.insert("end", f" {code.strip()} \t",
                         (f"badge-{code.strip()}", group, row))
        self.text.insert("end", f"{progress}\t", ("text2", group, row))
        status_tag = self.STATUS_COLOR.get(status, C["text2"])
        self.text.insert("end", f"{self.STATUS_ICON.get(status, '')}{status}\t",
                         (group, row))
        self.text.insert("end", f"{title}\n", (level if group == "quiet" else "text2", group, row))
        # 状态列单独上色（逐行独立标签，只染这一列，标题保持中性）
        ranges = self.text.tag_ranges(row)
        cell = f"{self.STATUS_ICON.get(status, '')}{status}"
        if ranges and status != "—":
            idx = self.text.search(f"\t{cell}\t", str(ranges[0]), str(ranges[1]))
            if idx:
                tag = f"st{self._rows}"
                self.text.tag_configure(tag, foreground=status_tag)
                self.text.tag_add(tag, idx, f"{idx}+{len(cell)}c")
        if self._search:
            self._apply_search_row(row, line)
        self.text.configure(state="disabled")
        if at_bottom and self._autoscroll:
            self.text.see("end")
        self._has_content = True
        if self.empty.winfo_ismapped():
            self.empty.place_forget()

    def _apply_search_row(self, row: str, line: str) -> None:
        ranges = self.text.tag_ranges(row)
        if not ranges:
            return
        start, end = str(ranges[0]), str(ranges[1])
        try:
            self.text.tag_remove("hide", start, end)
            self.text.tag_remove("hit", start, end)
            if self._search.lower() in line.lower():
                self.text.tag_add("hit", start, end)
            else:
                self.text.tag_add("hide", start, f"{end}+1c")
        except tk.TclError:
            pass

    def set_search(self, query: str) -> None:
        self._search = query.strip().lower()
        for i in range(1, self._rows + 1):
            row = f"row{i}"
            ranges = self.text.tag_ranges(row)
            if ranges:
                line = self.text.get(str(ranges[0]), str(ranges[1]))
                self._apply_search_row(row, line)
        if not self._search:
            self.text.tag_remove("hide", "1.0", "end")
            self.text.tag_remove("hit", "1.0", "end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._rows = 0
        self._has_content = False
        self.empty.place(relx=0, rely=0, relwidth=1, relheight=1)

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.text.tag_configure("detail", elide=mode in ("key", "issue"))
        self.text.tag_configure("quiet", elide=mode == "issue")

    def set_autoscroll(self, on: bool) -> None:
        self._autoscroll = bool(on)
        if self._autoscroll:
            self.text.see("end")

    def _wheel(self, e) -> str:
        self.text.yview_scroll(-1 if e.delta > 0 else 1, "units")
        return "break"


# ---------------------------------------------------------------- 窗口控制按钮
class IconBtn(tk.Canvas):
    """标题栏上的窗口控制按钮（最小化 / 最大化 / 关闭）。"""

    def __init__(self, master, glyph: str, command, bg: str,
                 hover_bg: str, width: int = 44, height: int = 30,
                 danger: bool = False, size: int = 8, fg: str | None = None):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._glyph = glyph
        self._command = command
        self._bg = bg
        self._hover_bg = hover_bg
        self._danger = danger
        self._cw, self._ch = width, height
        self._size = size
        self._fg = fg or C["nav_text"]
        self._hover = False
        self.bind("<Enter>", lambda _e: self._paint(True))
        self.bind("<Leave>", lambda _e: self._paint(False))
        self.bind("<Button-1>", lambda _e: command())
        self._paint(False)

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self._paint(self._hover)

    def _paint(self, hover: bool) -> None:
        self._hover = hover
        self.delete("all")
        if hover:
            self.create_rectangle(0, 0, self._cw, self._ch,
                                  fill=C["err"] if self._danger else self._hover_bg,
                                  outline="")
        color = C["on_dark"] if (hover and self._danger) else self._fg
        self.create_text(self._cw / 2, self._ch / 2, text=self._glyph,
                         font=T.icon(self._size), fill=color)


# ---------------------------------------------------------------- 版本入口卡
class ModeCard(tk.Canvas):
    """入口页的大卡片：图标 + 名称 + 说明 + 标签，整块可点。"""

    def __init__(self, master, image, title: str, desc: str, tags: list,
                 command, width: int = 330, height: int | None = None,
                 bg: str = C["bg"]):
        # 说明文字要按实际字号换行，高度就不能写死——先在不可见 Label 上量一次
        if height is None:
            probe = tk.Label(master, text=desc, font=T.f(9),
                             wraplength=width - 56, justify="left")
            desc_h = probe.winfo_reqheight()
            probe.destroy()
            height = 76 + desc_h + 26 + 28
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._img = image
        self._title = title
        self._desc = desc
        self._tags = tags
        self._command = command
        self._cw, self._ch = width, height
        self._hover = False
        self._hover_mix = 0.0
        self._anim = None
        self.bind("<Enter>", lambda _e: self._animate_hover(True))
        self.bind("<Leave>", lambda _e: self._animate_hover(False))
        self.bind("<Button-1>", lambda _e: command())
        self.bind("<Destroy>", lambda _e: self._anim and self._anim.cancel())
        self._paint()

    def _animate_hover(self, on: bool) -> None:
        self._hover = on
        if self._anim:
            self._anim.cancel()
        start = self._hover_mix
        target = 1.0 if on else 0.0

        def update(t: float) -> None:
            self._hover_mix = start + (target - start) * t
            self._paint()

        self._anim = motion.animate(motion.CARD, update)

    def reveal(self, delay: int = 0) -> None:
        """入口卡淡入（同画布网点遮罩）。"""
        if not motion.is_enabled():
            return
        self._paint()
        w, h = self._cw, self._ch
        mask = round_rect(self, 1, 1, w - 1, h - 1, T.RADIUS_CARD + 2,
                          fill=C["surface"], outline=C["surface"],
                          stipple="gray75", tags="intro-mask")
        steps = ("gray75", "gray50", "gray25", "gray12")

        def update(t: float) -> None:
            self.itemconfigure(mask, stipple=steps[min(3, int(t * 4))])

        motion.animate(motion.CARD, update,
                       done=lambda: self.delete(mask), delay=delay)

    def _paint(self) -> None:
        self.delete("all")
        w, h = self._cw, self._ch
        mix = self._hover_mix
        outline = blend(C["stroke"], C["accent"], mix)
        round_rect(self, 1, 1, w - 1, h - 1, T.RADIUS_CARD + 2,
                   fill=blend(C["surface"], C["accent_soft"], mix * 0.5),
                   outline=outline, width=1 + mix)
        self.create_image(34, 40, image=self._img, anchor="center")
        self.create_text(66, 32, text=self._title, font=T.f(13, True),
                         fill=C["text"], anchor="w")
        # 说明按宽度折行
        self.create_text(28, 78, text=self._desc, font=T.f(9), fill=C["text2"],
                         anchor="nw", width=w - 56, justify="left")
        y = h - 34
        x = 28
        for text in self._tags:
            tw = measure(T.f(8), text) + 20
            round_rect(self, x, y - 11, x + tw, y + 11, 11,
                       fill=blend(C["surface_hi"], C["accent_soft"], mix),
                       outline="", width=0)
            self.create_text(x + 10, y, text=text, font=T.f(8),
                             fill=blend(C["text2"], C["accent_hi"], mix),
                             anchor="w")
            x += tw + 8


# ---------------------------------------------------------------- 文本 / 输入
def label(master, text: str, size: int = 10, color: str = C["text2"],
          bold: bool = False, bg: Optional[str] = None, **kw) -> tk.Label:
    return tk.Label(master, text=text, font=T.f(size, bold), fg=color,
                    bg=bg or C["surface"], anchor="w", **kw)


def entry(master, textvariable=None, width: int = 12, size: int = 10) -> tk.Entry:
    """浅色输入框：白底 + 1px 边框，聚焦时边框变主色。"""
    return tk.Entry(
        master, textvariable=textvariable, width=width, font=T.f(size),
        bg=C["surface"], fg=C["text"], insertbackground=C["text"],
        relief="flat", bd=0, highlightthickness=1,
        highlightbackground=C["stroke_hi"], highlightcolor=C["accent"],
        disabledbackground=C["surface_alt"], disabledforeground=C["text3"],
    )


def divider(master, pad: int = 0, bg: Optional[str] = None) -> tk.Frame:
    return tk.Frame(master, bg=C["stroke"], height=1)
