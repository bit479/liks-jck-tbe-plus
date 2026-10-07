"""接管 TBe+ 桌面客户端。

TBe+ 没有网页版登录入口（门户页只是个"下载客户端"页），新闻站还有 WAF
只放行客户端内部的请求。所以只能接管桌面端：
TBe+ 是 Electron 壳，本质就是 Chromium，带上 --remote-debugging-port 重启后
就能用 CDP 直接驱动它，不需要账号密码、也不用过验证码。

TBe.exe 的位置每台机器可能都不一样，所以这里除了读配置，还会自动查找：
    1. 正在运行的 TBe 进程     —— 最准，直接拿到它现在的路径
    2. 桌面 / 开始菜单快捷方式  —— 安装包一般都会建
    3. 卸载注册表项            —— 能拿到安装目录
    4. App Paths 注册表项
    5. 常见安装目录通配搜索
都找不到时，界面会弹出文件选择框让用户自己指定，并记到 config.json 里。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .cdp import is_debug_port_open, list_targets

LogFn = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# ---------------------------------------------------------------------------
# 基础命令
# ---------------------------------------------------------------------------
def _run_powershell(script: str, timeout: float = 25.0) -> str:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return (result.stdout or "").strip()
    except Exception:
        return ""


def is_attached(port: int) -> bool:
    """调试端口是否已经开着。"""
    return is_debug_port_open(port)


def is_client_running() -> bool:
    return bool(_run_powershell("(Get-Process TBe -ErrorAction SilentlyContinue).Count"))


def summarize_targets(port: int) -> List[tuple]:
    """返回 [(type, title)]，界面用来显示当前状态。"""
    try:
        return [(t.get("type", ""), t.get("title", "") or "") for t in list_targets(port)]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 找 TBe.exe
# ---------------------------------------------------------------------------
def _normalize(path_text: str) -> Optional[Path]:
    """把注册表/快捷方式里拿到的杂乱字符串整理成一个路径。"""
    if not path_text:
        return None
    text = path_text.strip().strip('"').strip()
    if not text:
        return None
    # 去掉图标索引后缀，例如 C:\x\TBe.exe,0
    if text.endswith(",0"):
        text = text[:-2]
    candidate = Path(text)
    return candidate if candidate.is_file() else None


def _pick_best(paths: Iterable[Path]) -> Optional[Path]:
    """多个候选里挑最像"真正的客户端主程序"的那个。

    同一个 tbea 目录下通常还有一个几百 KB 的小启动器（也叫 TBe.exe），
    真正的主程序在带版本号的子目录里、体积大得多，所以按体积排。
    """
    existing = [p for p in paths if p.is_file()]
    if not existing:
        return None
    existing.sort(
        key=lambda p: (
            # 带版本号子目录的优先
            0 if p.parent.name.isdigit() else 1,
            -p.stat().st_size,
        )
    )
    return existing[0]


def _from_running_process() -> Optional[Path]:
    out = _run_powershell(
        "Get-CimInstance Win32_Process -Filter \"Name='TBe.exe'\" "
        "-ErrorAction SilentlyContinue | Select-Object -First 1 "
        "-ExpandProperty ExecutablePath"
    )
    return _normalize(out)


def _from_shortcuts() -> Optional[Path]:
    script = (
        "$sh = New-Object -ComObject WScript.Shell;"
        "$dirs = @(\"$env:USERPROFILE\\Desktop\",\"$env:PUBLIC\\Desktop\","
        "\"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\","
        "\"$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\");"
        "Get-ChildItem -Path $dirs -Filter *.lnk -Recurse -ErrorAction SilentlyContinue |"
        " Where-Object { $_.Name -match 'TBe|特变' } |"
        " ForEach-Object { $sh.CreateShortcut($_.FullName).TargetPath }"
    )
    found: List[Path] = []
    for line in _run_powershell(script).splitlines():
        path = _normalize(line)
        if path is None:
            continue
        found.append(path)
        # 快捷方式常常指向小启动器，顺手把同目录带版本号的子目录也收进来
        directory = path.parent
        if directory.is_dir():
            for sub in directory.iterdir():
                if sub.is_dir() and (sub / "TBe.exe").is_file():
                    found.append(sub / "TBe.exe")
    return _pick_best(found)


def _from_uninstall_registry() -> Optional[Path]:
    script = (
        "$keys = @('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');"
        "Get-ItemProperty $keys -ErrorAction SilentlyContinue |"
        " Where-Object { $_.DisplayName -match 'TBe|特变' -or $_.Publisher -match 'tbea' } |"
        " ForEach-Object { $_.InstallLocation; $_.DisplayIcon; $_.UninstallString }"
    )
    found: List[Path] = []
    for line in _run_powershell(script).splitlines():
        path = _normalize(line)
        if path is not None:
            if path.name.lower() == "tbe.exe":
                found.append(path)
            continue
        # 拿到的可能只是目录，或者 unins000.exe
        text = line.strip().strip('"')
        if not text:
            continue
        directory = Path(text)
        if directory.is_file():
            directory = directory.parent
        if directory.is_dir():
            for sub in [directory] + [d for d in directory.iterdir() if d.is_dir()]:
                exe = sub / "TBe.exe"
                if exe.is_file():
                    found.append(exe)
    return _pick_best(found)


def _from_app_paths() -> Optional[Path]:
    script = (
        "@('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\TBe.exe',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\App Paths\\TBe.exe',"
        "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\TBe.exe') |"
        " ForEach-Object { (Get-ItemProperty $_ -ErrorAction SilentlyContinue).'(default)' }"
    )
    found = [_normalize(line) for line in _run_powershell(script).splitlines()]
    return _pick_best([p for p in found if p is not None])


def _from_common_dirs(log: LogFn = _noop) -> Optional[Path]:
    """通配搜索常见的安装目录。"""
    found: List[Path] = []
    roots: List[Path] = []
    for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
        roots.append(Path(base))
    for letter in "DEFGH":
        drive = Path(f"{letter}:\\")
        if drive.exists():
            roots.append(drive)
            roots.append(drive / "Program Files")
            roots.append(drive / "Program Files (x86)" / "tbea")

    for root in roots:
        if not root.is_dir():
            continue
        # 直接在 tbea 目录里找
        for pattern in ("tbea/*/TBe.exe", "tbea/TBe.exe", "*/tbea/*/TBe.exe", "*/tbea/TBe.exe"):
            try:
                found.extend(root.glob(pattern))
            except OSError:
                continue
    return _pick_best(found)


def detect_client_exe(log: LogFn = _noop) -> Optional[Path]:
    """自动查找 TBe.exe。按可靠性从高到低依次尝试。"""
    steps = [
        ("正在运行的 TBe 进程", _from_running_process),
        ("桌面 / 开始菜单快捷方式", _from_shortcuts),
        ("卸载注册表项", _from_uninstall_registry),
        ("App Paths 注册表项", _from_app_paths),
        ("常见安装目录", lambda: _from_common_dirs(log)),
    ]
    for label, func in steps:
        log(f"  查找 TBe.exe：{label} …")
        try:
            path = func()
        except Exception as exc:  # noqa: BLE001
            log(f"    （{label} 出错：{exc}）")
            continue
        if path is not None:
            return path
    return None


def find_client_exe(candidates: Iterable[str]) -> Optional[Path]:
    """只看配置里给的路径。"""
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def resolve_client_exe(candidates: Iterable[str], log: LogFn = _noop) -> Optional[Path]:
    """先看配置，再自动查找。"""
    exe = find_client_exe(candidates)
    if exe is not None:
        log(f"用配置里的路径：{exe}")
        return exe
    log("配置里的路径都不存在，开始自动查找 TBe.exe …")
    exe = detect_client_exe(log)
    if exe is not None:
        log(f"自动找到：{exe}")
    return exe


# ---------------------------------------------------------------------------
# 启停客户端
# ---------------------------------------------------------------------------
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
    shown = -1
    while time.time() < deadline:
        if is_debug_port_open(port):
            log("调试端口已就绪")
            return True
        waited = int(timeout - (deadline - time.time()))
        if waited // 5 != shown:
            shown = waited // 5
            log(f"  等待客户端启动…{waited}s")
        time.sleep(2)
    return False


def restart_client(exe: Path, port: int, timeout: float, log: LogFn = _noop) -> bool:
    """关掉再带调试端口启动，并等端口就绪。"""
    stop_client(log)
    start_client(exe, port, log)
    return wait_for_port(port, timeout, log)


def save_client_exe(exe: Path, existing: Iterable[str] = ()) -> bool:
    """把找到的路径记进 config.json，下次直接命中。"""
    from .config import CONFIG_FILE

    data: dict = {}
    if CONFIG_FILE.is_file():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}

    candidates = [str(exe)]
    for item in list(existing) + list(data.get("client_exe_candidates") or []):
        if item and item not in candidates:
            candidates.append(item)
    data["client_exe_candidates"] = candidates

    try:
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError:
        return False
