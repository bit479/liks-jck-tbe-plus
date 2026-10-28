"""新闻站的纯 HTTP 客户端：只要拿到 tbea_art_token，就不需要任何客户端在线。

实测（2026-10）：
    Cookie: tbea_art_token=<32 位十六进制>
    GET  /api/article/lists?page_no=&page_size=[&section_code=]
    GET  /api/article/detail?id=
    POST /api/article/addDigg      （表单 id=）
这三个接口都正常返回 code=1，不需要 Electron/CDP/钉钉界面。

token 从哪来：
    1. config.json 里的 news_token（也可以 cli.py --token 写入）；
    2. 上次成功用的 state.json；
    3. 从已经登录的客户端进程内存里读（新版 TBeHome.exe / 旧版 TBe.exe）。
读不到就提示用户手填一次，之后会缓存复用。
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_BASE = "https://tbeanews.tbea.com/api"
TOKEN_RE = re.compile(rb"tbea_art_token=([0-9a-fA-F]{32})")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


class NewsApiError(RuntimeError):
    pass


class NewsApi:
    """极简新闻站客户端（纯标准库）。"""

    def __init__(self, token: str, base: str = DEFAULT_BASE, timeout: float = 25.0):
        self.token = token.strip()
        self.base = base.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _call(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base}{path}"
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Cookie": f"tbea_art_token={self.token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json, text/plain, */*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NewsApiError(f"请求失败：{exc}") from exc
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise NewsApiError(f"返回不是 JSON：{text[:120]!r}") from exc
        if not isinstance(payload, dict):
            raise NewsApiError(f"返回结构异常：{text[:120]!r}")
        return payload

    # ------------------------------------------------------------------
    def check(self) -> bool:
        """token 还有效吗。"""
        try:
            payload = self._call("/article/lists", {"page_no": 1, "page_size": 1})
        except NewsApiError:
            return False
        return payload.get("code") == 1

    def list_page(
        self, section_code: str = "", page_no: int = 1, page_size: int = 200
    ) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        params: Dict[str, Any] = {"page_no": page_no, "page_size": page_size}
        if section_code:
            params["section_code"] = section_code
        payload = self._call("/article/lists", params)
        if payload.get("code") != 1:
            raise NewsApiError(
                f"列表失败：code={payload.get('code')} msg={payload.get('msg')!r}"
            )
        data = payload.get("data") or {}
        items = data.get("lists") or []
        return items, data.get("count")

    def portal_sections(self) -> Dict[str, str]:
        """栏目名 → section_code（新闻资讯=news、开讲了=lecture…）。"""
        payload = self._call("/pc/portal")
        if payload.get("code") not in (0, 1):
            raise NewsApiError(f"门户接口异常：code={payload.get('code')}")
        data = payload.get("data") or {}
        codes: Dict[str, str] = {}
        for key, value in data.items():
            if isinstance(value, dict) and value.get("name"):
                codes[str(value["name"]).strip()] = key
        return codes

    def detail(self, article_id: str) -> Dict[str, Any]:
        payload = self._call("/article/detail", {"id": article_id})
        if payload.get("code") != 1:
            raise NewsApiError(
                f"详情失败：code={payload.get('code')} msg={payload.get('msg')!r}"
            )
        data = payload.get("data")
        if not isinstance(data, dict) or not data:
            raise NewsApiError("详情为空")
        return data

    def add_digg(self, article_id: str) -> bool:
        payload = self._call("/article/addDigg", data={"id": article_id})
        return payload.get("code") == 1


# ----------------------------------------------------------------------
# 令牌来源：配置文件 / state.json / 客户端内存
# ----------------------------------------------------------------------
def load_state(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def remembered_token(state_path: Path) -> str:
    data = load_state(state_path)
    token = data.get("news_token")
    return token if isinstance(token, str) else ""


def remember_token(state_path: Path, token: str, source: str) -> None:
    data = load_state(state_path)
    data["news_token"] = token
    data["news_token_source"] = source
    data["news_token_saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(state_path, data)


# ---------------- Windows 进程内存扫描（只读） ----------------
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01


class _MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def _pids(image_name: str) -> List[int]:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="gbk",
            errors="replace",
            timeout=20,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        int(m.group(1))
        for m in re.finditer(rf'"{re.escape(image_name)}","(\d+)"', out)
    ]


def _scan_process(pid: int, found: Dict[str, int], budget: int = 400) -> None:
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return
    addr = 0
    mbi = _MBI()
    size = ctypes.sizeof(_MBI)
    try:
        while kernel32.VirtualQueryEx(
            handle, ctypes.c_void_p(addr), ctypes.byref(mbi), size
        ):
            base = mbi.BaseAddress or 0
            if (
                mbi.State == MEM_COMMIT
                and not (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS))
                and 0 < mbi.RegionSize <= 64 * 1024 * 1024
            ):
                buf = ctypes.create_string_buffer(mbi.RegionSize)
                read = ctypes.c_size_t(0)
                if kernel32.ReadProcessMemory(
                    handle,
                    ctypes.c_void_p(base),
                    buf,
                    mbi.RegionSize,
                    ctypes.byref(read),
                ):
                    data = buf.raw[: read.value]
                    for match in TOKEN_RE.finditer(data):
                        token = match.group(1).decode("ascii").lower()
                        found[token] = found.get(token, 0) + 1
                        budget -= 1
                        if budget <= 0:
                            return
            nxt = base + mbi.RegionSize
            if nxt <= addr:
                break
            addr = nxt
    finally:
        kernel32.CloseHandle(handle)


def scan_token_in_memory(images=("TBeHome.exe", "TBe.exe")) -> str:
    """从已登录客户端进程里读 tbea_art_token（新版优先）。找不到返回空串。

    钉钉版会传入 ``images=("TBeHome.exe",)``，确保不会误读旧版客户端。
    """
    if os.name != "nt":
        return ""
    found: Dict[str, int] = {}
    for image in images:
        for pid in _pids(image)[:4]:
            _scan_process(pid, found)
        if found:
            break
    if not found:
        return ""
    return max(found.items(), key=lambda kv: kv[1])[0]
