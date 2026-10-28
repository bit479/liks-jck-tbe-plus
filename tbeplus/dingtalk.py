"""钉钉版 e+（新版 TBeHome）的启动与 token 接管。

和旧版 TBe.exe 完全分开：

- 只查找、只启动 ``TBeHome.exe``，代码里没有任何回退到 TBe.exe 的分支；
- 不依赖 CDP：新版是钉钉 CEF 壳，实测不接受 ``--remote-debugging-port``；
- 登录态已经在客户端里时，直接从 TBeHome 进程内存读取 ``tbea_art_token``，
  校验有效后写入 state.json，之后点赞走纯 HTTP，客户端可以关掉；
- 首次未登录时，把客户端窗口带到前台，等用户点开「e+ → 新闻资讯」，
  后台每 2 秒重试一次，读到 token 就自动接管。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from . import newsapi
from .newsapi import NewsApi

LogFn = Callable[[str], None]
StatusFn = Callable[[str], None]
_noop = lambda *_: None  # noqa: E731

HOME_IMAGE = "TBeHome.exe"


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def _run_powershell(script: str, timeout: float = 20.0) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, creationflags=flags,
        )
        return (result.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def is_home_running() -> bool:
    return bool(newsapi._pids(HOME_IMAGE))


def home_pids() -> List[int]:
    return newsapi._pids(HOME_IMAGE)


# ---------------------------------------------------------------------------
# 查找 TBeHome.exe
# ---------------------------------------------------------------------------
def _valid(path: Optional[Path]) -> Optional[Path]:
    """硬约束：只接受 TBeHome.exe，绝不返回旧版 TBe.exe。"""
    if path is None:
        return None
    try:
        path = path.resolve()
    except OSError:
        return None
    if not path.is_file() or path.name.lower() != HOME_IMAGE.lower():
        return None
    return path


def _from_configured(candidates: Iterable[str]) -> Optional[Path]:
    for item in candidates:
        path = _valid(Path(item))
        if path:
            return path
    return None


def _from_running() -> Optional[Path]:
    out = _run_powershell(
        "(Get-Process TBeHome -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 -ExpandProperty Path)"
    )
    return _valid(Path(out)) if out else None


def _install_roots() -> List[Path]:
    roots: List[Path] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(env)
        if value:
            roots.append(Path(value))
    for letter in "CDEFGH":
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots.append(drive / "Program Files")
            roots.append(drive / "Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs")
    return roots


def _from_common_dirs() -> Optional[Path]:
    patterns = (
        "TBeHome/main/current/TBeHome.exe",
        "TBeHome/TBeHome.exe",
        "*/TBeHome/main/current/TBeHome.exe",
        "*/TBeHome/TBeHome.exe",
    )
    for root in _install_roots():
        if not root.is_dir():
            continue
        for pattern in patterns:
            try:
                for path in root.glob(pattern):
                    found = _valid(path)
                    if found:
                        return found
            except OSError:
                continue
    return None


def _from_registry() -> Optional[Path]:
    script = (
        "$keys = @('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');"
        "Get-ItemProperty $keys -ErrorAction SilentlyContinue |"
        " Where-Object { $_.DisplayName -match 'TBeHome|钉钉版' } |"
        " ForEach-Object { $_.InstallLocation; $_.DisplayIcon }"
    )
    for line in _run_powershell(script).splitlines():
        text = line.strip().strip('"')
        if not text:
            continue
        path = Path(text.split(",")[0])
        candidates = [path]
        if path.is_file():
            candidates.append(path.parent / "main" / "current" / HOME_IMAGE)
        for base in list(candidates):
            if base.is_dir():
                candidates += [
                    base / "TBeHome.exe",
                    base / "main" / "current" / "TBeHome.exe",
                ]
        for item in candidates:
            found = _valid(item)
            if found:
                return found
    return None


def find_home_exe(candidates: Iterable[str] = (), log: LogFn = _noop) -> Optional[Path]:
    """按可靠性查 TBeHome.exe；绝不回退到旧版客户端。"""
    path = _from_configured(candidates)
    if path:
        log(f"用配置里的钉钉版路径：{path}")
        return path
    for label, func in (
        ("正在运行的 TBeHome 进程", _from_running),
        ("常见安装目录", _from_common_dirs),
        ("卸载注册表项", _from_registry),
    ):
        log(f"  查找钉钉版 e+：{label} …")
        try:
            path = func()
        except Exception as exc:  # noqa: BLE001
            log(f"    （{label} 出错：{exc}）")
            continue
        if path:
            log(f"  找到：{path}")
            return path
    log("  没有找到钉钉版 e+（TBeHome.exe）。")
    log("  请先安装新版客户端，或在「账号与连接」里手动指定 TBeHome.exe。")
    return None


# ---------------------------------------------------------------------------
# 启动 / 前台
# ---------------------------------------------------------------------------
def launch_home(exe: Path, log: LogFn = _noop) -> bool:
    if is_home_running():
        log("钉钉版 e+ 已经在运行。")
        return True
    log(f"启动钉钉版 e+：{exe}")
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True)
    except OSError as exc:
        log(f"  启动失败：{exc}")
        return False
    deadline = time.time() + 45
    while time.time() < deadline:
        if is_home_running():
            log("  客户端已启动。")
            time.sleep(2)
            return True
        time.sleep(1)
    log("  等了 45 秒还没看到 TBeHome 进程。")
    return False


def bring_home_to_front() -> bool:
    if os.name != "nt" or not is_home_running():
        return False
    pids = set(home_pids())
    found = []

    try:
        user32 = ctypes.windll.user32
        from ctypes import wintypes

        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if not length:
                return True
            found.append(hwnd)
            return True

        user32.EnumWindows(EnumProc(cb), 0)
        if not found:
            return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)          # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# token
# ---------------------------------------------------------------------------
def token_from_home() -> str:
    """只从 TBeHome.exe 进程内存读 token。"""
    found: dict = {}
    for pid in home_pids()[:6]:
        newsapi._scan_process(pid, found, budget=400)
        if found:
            break
    if not found:
        return ""
    return max(found.items(), key=lambda kv: kv[1])[0]


def token_valid(token: str, timeout: float = 8.0) -> bool:
    if not token:
        return False
    try:
        return NewsApi(token, timeout=timeout).check()
    except Exception:  # noqa: BLE001
        return False


def watch_token(
    state_path: Path,
    timeout: float = 60.0,
    log: LogFn = _noop,
    status: StatusFn = _noop,
    stop_event=None,
) -> str:
    """轮询 TBeHome 内存，读到有效 token 就缓存并返回。"""
    deadline = time.time() + timeout
    last_note = 0.0
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return ""
        token = token_from_home()
        if token:
            status("发现 token，正在校验…")
            if token_valid(token):
                newsapi.remember_token(state_path, token, "钉钉版e+内存")
                log("  已接管 token（来自钉钉版 e+），之后可纯 HTTP 运行。")
                return token
            log("  读到的 token 已失效，继续等待登录态刷新…")
        now = time.time()
        if now - last_note > 8:
            last_note = now
            status("等待「新闻资讯」加载…")
        time.sleep(2)
    return ""


def auto_prepare(
    state_path: Path,
    candidates: Iterable[str] = (),
    log: LogFn = _noop,
    status: StatusFn = _noop,
    stop_event=None,
) -> str:
    """一键准备：优先复用缓存 token，否则启动新版客户端并接管。

    返回有效 token；失败返回空串。不会启动、也不会读取旧版 TBe.exe。
    """
    cached = newsapi.remembered_token(state_path)
    if cached:
        status("检查已缓存的 token…")
        if token_valid(cached):
            log("  沿用已缓存的 token，不需要启动客户端。")
            return cached
        log("  缓存 token 已失效，准备从钉钉版 e+ 重新读取。")

    exe = find_home_exe(candidates, log)
    if exe is None:
        status("没有找到钉钉版 e+")
        return ""
    if not launch_home(exe, log):
        status("钉钉版 e+ 启动失败")
        return ""

    status("正在读取 token…")
    token = watch_token(state_path, timeout=12, log=log, status=status,
                        stop_event=stop_event)
    if token:
        return token

    # 首次使用时新闻模块可能还没打开：把窗口带到前台，提示一次，然后继续等
    log("")
    log("没有读到 token。请在钉钉版 e+ 里点开「e+」→「新闻资讯」打开一次新闻列表。")
    log("程序会在后台继续等待，读到后自动接管，不用再点其它按钮。")
    if not bring_home_to_front():
        log("  没找到客户端可见窗口；如果它在托盘里，请右键打开，再点「新闻资讯」。")
    status("等待你打开「e+ → 新闻资讯」…")
    # 不在这里长时间阻塞：交给 UI 起一个后台 watcher，
    # 用户点开新闻列表后自动接管，期间界面仍可操作。
    return ""
