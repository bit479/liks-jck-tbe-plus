"""接管 TBe+ 桌面客户端。

TBe+ 没有网页版登录入口（门户页只是个"下载客户端"页），新闻站还有 WAF
只放行客户端内部的请求。所以只能接管桌面端：
TBe+ 是 Electron 壳，本质就是 Chromium，带上 --remote-debugging-port 重启后
就能用 CDP 直接驱动它，不需要账号密码、也不用过验证码。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from .cdp import is_debug_port_open, list_targets

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def is_attached(port: int) -> bool:
    """调试端口是否已经开着。"""
    return is_debug_port_open(port)


def find_client_exe(candidates: list[str]) -> Optional[Path]:
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def is_client_running() -> bool:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process TBe -ErrorAction SilentlyContinue).Count"],
            capture_output=True, text=True, timeout=20,
        )
        return (out.stdout or "").strip() not in ("", "0")
    except Exception:
        return False


def stop_client(log: LogFn = _noop) -> None:
    log("正在关闭 TBe+ …")
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process TBe -ErrorAction SilentlyContinue | Stop-Process -Force"],
        capture_output=True, text=True, timeout=30,
    )
    time.sleep(3)


def start_client(exe: Path, port: int, log: LogFn = _noop) -> None:
    log(f"带调试端口启动：{exe}")
    log(f"  --remote-debugging-port={port}")
    subprocess.Popen(
        [str(exe), f"--remote-debugging-port={port}"],
        cwd=str(exe.parent),
        close_fds=True,
    )


def wait_for_port(port: int, timeout: float, log: LogFn = _noop) -> bool:
    """等调试端口就绪。"""
    deadline = time.time() + timeout
    shown = 0
    while time.time() < deadline:
        if is_debug_port_open(port):
            log("调试端口已就绪")
            return True
        waited = int(time.time() - (deadline - timeout))
        if waited // 5 > shown:
            shown = waited // 5
            log(f"  等待客户端启动…{waited}s")
        time.sleep(2)
    return False


def attach(
    candidates: list[str], port: int, restart: bool, timeout: float, log: LogFn = _noop
) -> bool:
    """确保客户端处于"可接管"状态。

    restart=False 时只做检查，不会动正在运行的客户端。
    """
    if is_attached(port):
        log(f"客户端已处于可接管状态（端口 {port}）")
        return True

    if not restart:
        log(f"端口 {port} 上没有调试接口。")
        log("需要先带调试端口重启客户端（界面上的「① 准备」按钮）。")
        return False

    exe = find_client_exe(candidates)
    if exe is None:
        log("没找到 TBe.exe，请在 config.json 里补上 client_exe_candidates")
        return False

    stop_client(log)
    start_client(exe, port, log)
    return wait_for_port(port, timeout, log)


def summarize_targets(port: int) -> list[tuple[str, str]]:
    """返回 [(type, title)]，界面用来显示当前状态。"""
    try:
        return [(t.get("type", ""), t.get("title", "") or "") for t in list_targets(port)]
    except Exception:
        return []
