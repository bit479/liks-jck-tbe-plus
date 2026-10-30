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
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from . import winproc
from .cdp import is_debug_port_open, list_targets

LogFn = Callable[[str], None]

# 从配置的端口开始，向后试多少个端口
PORT_SCAN_COUNT = 20


def _noop(_: str) -> None:
    pass


# ---------------------------------------------------------------------------
# 基础命令
# ---------------------------------------------------------------------------


def is_attached(port: int) -> bool:
    """调试端口是否已经开着。"""
    return is_debug_port_open(port)


def is_client_running() -> bool:
    # 纯 Win32 进程快照，不再起 PowerShell / tasklist（避免运行时闪黑框）
    return bool(winproc.pids("TBe.exe"))


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
    for pid in winproc.pids("TBe.exe"):
        path = winproc.exe_path(pid)
        if path is not None:
            return _normalize(str(path))
    return None


def _from_shortcuts() -> Optional[Path]:
    """从桌面 / 开始菜单的 .lnk 读目标。纯二进制解析，不起子进程。"""
    dirs = [
        Path.home() / "Desktop",
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    found: List[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            continue
        try:
            lnks = list(directory.rglob("*.lnk"))
        except OSError:
            continue
        for lnk in lnks:
            if "TBe" not in lnk.name and "特变" not in lnk.name:
                continue
            target = winproc.lnk_target(lnk)
            if target is None:
                continue
            found.append(target)
            # 快捷方式常常指向小启动器，顺手把同目录带版本号的子目录也收进来
            parent = target.parent
            if parent.is_dir():
                for sub in parent.iterdir():
                    if sub.is_dir() and (sub / "TBe.exe").is_file():
                        found.append(sub / "TBe.exe")
    return _pick_best(found)


def _from_uninstall_registry() -> Optional[Path]:
    found: List[Path] = []
    for info in winproc.iter_uninstall():
        blob = " ".join(str(v) for v in info.values())
        if "tbe" not in blob.lower() and "特变" not in blob:
            continue
        for field in ("InstallLocation", "DisplayIcon", "UninstallString"):
            text = str(info.get(field) or "").strip().strip('"')
            if not text:
                continue
            path = _normalize(text)
            if path is not None:
                if path.name.lower() == "tbe.exe":
                    found.append(path)
                continue
            # 拿到的可能只是目录，或者 unins000.exe
            directory = Path(text.split(",")[0])
            if directory.is_file():
                directory = directory.parent
            if directory.is_dir():
                for sub in [directory] + [d for d in directory.iterdir() if d.is_dir()]:
                    exe = sub / "TBe.exe"
                    if exe.is_file():
                        found.append(exe)
    return _pick_best(found)


def _from_app_paths() -> Optional[Path]:
    path = _normalize(winproc.app_path_default("TBe.exe"))
    return _pick_best([path] if path is not None else [])


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
def port_available(port: int) -> bool:
    """这个端口现在能不能绑上。

    用来同时避开两种情况：
      1. 端口被别的程序占着；
      2. 端口落在 Windows 的保留端口段里（装了 Hyper-V / WSL / Docker 之后
         系统会预留一些段，落在里面的端口连管理员也绑不上，
         客户端就会静默地起不来调试端口）。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def find_available_port(preferred: int, count: int = PORT_SCAN_COUNT):
    """从 preferred 开始找一个真能用的端口，找不到返回 None。"""
    for offset in range(count):
        port = preferred + offset
        if port_available(port):
            return port
    return None


def port_owner(port: int) -> str:
    """查一下端口被谁占着（只用于日志提示）。纯 Win32，无子进程。"""
    return winproc.listen_owner_name(port)


def is_tbe_debug_port(port: int) -> bool:
    """这个端口上的调试接口是不是我们的 TBe+ 客户端。

    只判断"有 CDP 接口"是不够的——别的程序（比如手动调试的 Chrome）
    也可能占着同一个端口。
    """
    # 扫描时用短超时，避免个别端口没响应时卡住
    if not is_debug_port_open(port, timeout=1.5):
        return False
    try:
        for target in list_targets(port, timeout=2):
            url = target.get("url") or ""
            if "app.asar" in url or url.startswith("miniapp://"):
                return True
    except Exception:
        return False
    return False


def resolve_debug_port(preferred: int, log: LogFn = _noop):
    """找出当前真正可以用的调试端口。

    先看配置里的，再往后扫一段——因为上一次可能因为端口冲突自动换过端口，
    客户端还在那个端口上跑着。
    """
    for offset in range(PORT_SCAN_COUNT):
        port = preferred + offset
        if is_tbe_debug_port(port):
            if offset:
                log(f"在端口 {port} 上发现了已接管的客户端")
            return port
    return None


def save_debug_port(port: int) -> bool:
    """把实际使用的端口记进 config.json。"""
    return _save_config({"debug_port": port})


def _save_config(patch: dict) -> bool:
    from .config import CONFIG_FILE

    data: dict = {}
    if CONFIG_FILE.is_file():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data.update(patch)
    try:
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return True
    except OSError:
        return False


def stop_client(log: LogFn = _noop) -> bool:
    """把 TBe+ 关干净。

    这一步很关键：TBe+ 是单实例的，旧进程还在的话，新进程只会把窗口
    唤到前面然后自己退出，**调试端口根本不会开**。表现就是一直连不上。
    """
    log("正在关闭 TBe+ …")
    for attempt in range(3):
        winproc.kill_pids(winproc.pids("TBe.exe"))
        time.sleep(2)
        if not is_client_running():
            log("  客户端已关闭")
            return True
        log(f"  还有 TBe 进程没退出，再试一次（{attempt + 1}/3）")

    log("  提示：TBe 进程没能全部关掉。若它是单实例的，新进程可能开不出调试端口。")
    log("  可以手动退出 TBe+（托盘图标右键退出），然后再点一次「① 准备」。")
    return False


def start_client(exe: Path, port: int, log: LogFn = _noop) -> None:
    log(f"带调试端口启动：{exe}")
    log(f"  --remote-debugging-port={port}")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    subprocess.Popen(
        [str(exe), f"--remote-debugging-port={port}"],
        cwd=str(exe.parent),
        close_fds=True,
        creationflags=flags,
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


def restart_client(
    exe: Path, preferred_port: int, timeout: float, log: LogFn = _noop
):
    """关掉客户端再带调试端口启动。返回实际使用的端口；失败返回 None。"""
    existing = resolve_debug_port(preferred_port, log)
    if existing is not None:
        log(f"客户端已经处于可接管状态（端口 {existing}），不用重启")
        return existing

    stop_client(log)

    port = find_available_port(preferred_port)
    if port is None:
        log(
            f"从 {preferred_port} 往后找了 {PORT_SCAN_COUNT} 个端口都不可用，"
            "没法给客户端开调试端口。"
        )
        log("  请检查是不是有程序占着一大片端口，或者系统保留端口段太大。")
        return None
    if port != preferred_port:
        owner = port_owner(preferred_port)
        reason = f"被 {owner} 占用" if owner else "可能落在 Windows 保留端口段里"
        log(f"端口 {preferred_port} 当前不可用（{reason}）")
        log(f"  自动改用端口 {port}")

    start_client(exe, port, log)
    if wait_for_port(port, timeout, log):
        return port

    owner = port_owner(port)
    log(f"等不到调试端口 {port}。")
    if owner:
        log(f"  该端口正被 {owner} 占用")
    if not is_client_running():
        log("  客户端进程也不在了，说明它启动后立刻退出了。")
        log("  常见原因：单实例锁（旧进程没关干净），或者被安全软件拦了。")
        log("  请手动退出 TBe+（托盘图标右键退出）后再点一次「① 准备」。")
    else:
        log("  TBe+ 在运行，但没开调试端口，多半是端口问题。")
        log("  可以运行下面这条命令查看系统保留端口段：")
        log("    netsh int ipv4 show excludedportrange protocol=tcp")
        log("  也可以在 config.json 里把 debug_port 改成别的值（比如 19222）后重试。")
    return None


def save_client_exe(exe: Path, existing: Iterable[str] = ()) -> bool:
    """把找到的路径记进 config.json，下次直接命中。"""
    from .config import CONFIG_FILE

    known: list = []
    if CONFIG_FILE.is_file():
        try:
            loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                known = list(loaded.get("client_exe_candidates") or [])
        except (OSError, json.JSONDecodeError):
            known = []

    candidates = [str(exe)]
    for item in list(existing) + known:
        if item and item not in candidates:
            candidates.append(item)
    return _save_config({"client_exe_candidates": candidates})
