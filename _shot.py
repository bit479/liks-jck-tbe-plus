"""开发用截图脚本：把主窗口渲染成 PNG（纯标准库，不依赖第三方库）。

用法：
    python _shot.py 输出.png [page]

page: home / log / settings / connect，默认 home。
用 Tk 自身的窗口句柄截图（按标题 FindWindow 会撞上机器上其它同名窗口）。
"""

from __future__ import annotations

import ctypes
import struct
import sys
import zlib
from ctypes import wintypes


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def shoot(hwnd: int, path: str) -> None:
    user32 = ctypes.WinDLL("user32")
    gdi32 = ctypes.WinDLL("gdi32")

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"bad window size {w}x{h}")

    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, 2)          # PW_RENDERFULLCONTENT

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h                          # 负数 = top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    raw = bytearray(buf.raw)
    for i in range(0, len(raw), 4):           # BGRA -> RGBA
        raw[i], raw[i + 2] = raw[i + 2], raw[i]

    stride = w * 4
    rows = b"".join(b"\x00" + bytes(raw[y * stride:(y + 1) * stride])
                    for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n" +
           chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
           chunk(b"IDAT", zlib.compress(rows, 6)) +
           chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)


def main() -> None:
    from tkinter import Tk

    from tbeplus.config import load_config
    from tbeplus.ui import App, CONNECT, HOME, LOG, SETTINGS

    out = sys.argv[1] if len(sys.argv) > 1 else "shot_home.png"
    page_name = sys.argv[2] if len(sys.argv) > 2 else "home"
    mode = sys.argv[3] if len(sys.argv) > 3 else "old"
    pages = {"home": HOME, "log": LOG, "settings": SETTINGS, "connect": CONNECT}

    try:
        from tbeplus.app_icon import set_app_user_model_id
        set_app_user_model_id()
    except Exception:
        pass

    root = Tk()
    app = App(root, load_config())
    if page_name != "select":
        app._enter_mode(mode)                 # old / dingtalk
        app._show_page(pages.get(page_name, HOME), animate=False)

    def do_shot() -> None:
        root.update_idletasks()
        root.update()
        hwnd = int(root.frame(), 16)
        shoot(hwnd, out)
        print("saved", out)
        root.destroy()

    root.after(1600, do_shot)
    root.mainloop()


if __name__ == "__main__":
    main()
