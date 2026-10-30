"""纯 ctypes 的 Windows 查询：进程 / 端口占用 / 快捷方式 / 注册表。

用途：替代 tasklist 和 PowerShell。原来的实现每查一次进程就起一个控制台
子进程（tasklist / powershell），程序运行时（尤其钉钉版每 2 秒轮询一次）
会不停闪黑框。这里全部走 Win32 API，零子进程、零弹窗。
"""

from __future__ import annotations

import ctypes
import struct
import winreg
from ctypes import wintypes
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)

MAX_PATH = 260
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE = ctypes.c_void_p(-1).value
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

TCP_TABLE_OWNER_PID_LISTENER = 3
AF_INET = 2
ERROR_INSUFFICIENT_BUFFER = 122


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                     ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE,
                                    ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD


# ---------------------------------------------------------------------------
# 进程
# ---------------------------------------------------------------------------
def _snapshot() -> List[tuple]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    items: List[tuple] = []
    try:
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            items.append((int(entry.th32ProcessID), entry.szExeFile))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return items


def pids(image_name: str) -> List[int]:
    """按可执行文件名（如 TBe.exe / TBeHome.exe）找进程 PID。"""
    target = image_name.lower()
    return [pid for pid, exe in _snapshot() if exe.lower() == target]


def exe_path(pid: int) -> Optional[Path]:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(MAX_PATH)
        buf = ctypes.create_unicode_buffer(MAX_PATH)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                               ctypes.byref(size)):
            return Path(buf.value)
    finally:
        kernel32.CloseHandle(handle)
    return None


def kill_pids(pids_: Iterable[int]) -> int:
    killed = 0
    for pid in pids_:
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            continue
        try:
            if kernel32.TerminateProcess(handle, 1):
                killed += 1
        finally:
            kernel32.CloseHandle(handle)
    return killed


# ---------------------------------------------------------------------------
# 端口占用
# ---------------------------------------------------------------------------
class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


def listen_owner(port: int) -> Optional[int]:
    """返回监听该 TCP 端口的进程 PID（找不到返回 None）。"""
    size = wintypes.DWORD(0)
    ret = iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False,
                                       AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0)
    if ret not in (0, ERROR_INSUFFICIENT_BUFFER) or size.value == 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    ret = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False,
                                       AF_INET, TCP_TABLE_OWNER_PID_LISTENER, 0)
    if ret != 0:
        return None
    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD)).contents.value
    rows = (MIB_TCPROW_OWNER_PID * count).from_buffer(
        buf, ctypes.sizeof(wintypes.DWORD))
    for row in rows:
        raw = row.dwLocalPort
        local_port = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
        if local_port == port:
            return int(row.dwOwningPid)
    return None


def listen_owner_name(port: int) -> str:
    """'TBe.exe (PID 1234)' 这样的可读串，找不到返回空串。"""
    pid = listen_owner(port)
    if not pid:
        return ""
    path = exe_path(pid)
    if path:
        return f"{path.name} (PID {pid})"
    return f"PID {pid}"


# ---------------------------------------------------------------------------
# 快捷方式（.lnk）
# ---------------------------------------------------------------------------
def _cstr(data: bytes, start: int, end: int) -> bytes:
    if start >= len(data):
        return b""
    end = min(end, len(data))
    stop = data.find(b"\x00", start, end)
    return data[start:stop if stop >= 0 else end]


def lnk_target(lnk: Path) -> Optional[Path]:
    """解析 .lnk 的目标路径（读 LinkInfo 的 LocalBasePath + CommonPathSuffix）。

    只认文件类型的 .lnk；解析失败返回 None（由调用方跳过）。
    """
    try:
        data = lnk.read_bytes()
    except OSError:
        return None
    if len(data) < 76 or struct.unpack_from("<I", data, 0)[0] != 0x4C:
        return None
    flags = struct.unpack_from("<I", data, 20)[0]
    pos = 76
    if flags & 0x01:                                   # HasLinkTargetIDList
        if pos + 2 > len(data):
            return None
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    if not flags & 0x02:                               # 没有 LinkInfo
        return None
    if pos + 4 > len(data):
        return None
    li_end = pos + struct.unpack_from("<I", data, pos)[0]
    try:
        lbp = struct.unpack_from("<I", data, pos + 12)[0]
        cps = struct.unpack_from("<I", data, pos + 20)[0]
    except struct.error:
        return None
    raw = (_cstr(data, pos + lbp, li_end) if lbp else b"") + \
          (_cstr(data, pos + cps, li_end) if cps else b"")
    if not raw:
        return None
    try:
        text = raw.decode("mbcs")
    except (UnicodeDecodeError, LookupError):
        text = raw.decode("latin-1")
    path = Path(text)
    return path if path.is_file() else None


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
_UNINSTALL_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

_APP_PATH_ROOTS = [
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    (winreg.HKEY_CURRENT_USER,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
]

_FIELDS = ("DisplayName", "Publisher", "InstallLocation", "DisplayIcon",
           "UninstallString")


def iter_uninstall() -> Iterator[dict]:
    for hive, base in _UNINSTALL_ROOTS:
        try:
            root = winreg.OpenKey(hive, base)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    key = winreg.OpenKey(root, sub)
                except OSError:
                    continue
                info: dict = {}
                try:
                    for name in _FIELDS:
                        try:
                            info[name] = winreg.QueryValueEx(key, name)[0]
                        except OSError:
                            pass
                finally:
                    key.Close()
                if info:
                    yield info
        finally:
            root.Close()


def app_path_default(exe_name: str) -> str:
    for hive, base in _APP_PATH_ROOTS:
        try:
            key = winreg.OpenKey(hive, base + "\\" + exe_name)
        except OSError:
            continue
        try:
            try:
                value = winreg.QueryValueEx(key, "")[0]
            except OSError:
                continue
            if value:
                return str(value)
        finally:
            key.Close()
    return ""
