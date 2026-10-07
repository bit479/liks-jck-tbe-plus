"""极简 Chrome DevTools Protocol 客户端（零第三方依赖）。

为什么不用 Playwright：
TBe+ 桌面端是 2023 年版本的老 Electron 内核，不支持 browser context 管理，
Playwright 的 connect_over_cdp 一连上就报
    Protocol error (Browser.setDownloadBehavior): Browser context management is not supported

为什么自己写 WebSocket：
这样整个程序一个第三方包都不需要——用户只要有 Python 就能跑，
不用 pip、不用联网装依赖。

只用到 Runtime / Input / Page 这几个最基础的域，老内核全都支持。
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.request
from typing import Any, Dict, List, Optional

from .ws import WebSocketConnection, WebSocketError


class CDPError(RuntimeError):
    """CDP 调用失败（含超时、连接被目标重建掐断）。"""


def list_targets(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> List[Dict[str, Any]]:
    """GET /json/list，拿到当前所有页面 / webview。"""
    url = f"http://{host}:{port}/json/list"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def browser_endpoint(port: int, host: str = "127.0.0.1") -> Optional[str]:
    """GET /json/version，拿浏览器级 WebSocket 地址。"""
    try:
        url = f"http://{host}:{port}/json/version"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8")).get("webSocketDebuggerUrl")
    except Exception:
        return None


def is_debug_port_open(port: int, host: str = "127.0.0.1") -> bool:
    return browser_endpoint(port, host) is not None


class CDPSession:
    """连到一个调试目标，发命令收结果。

    底层是阻塞 socket，所以真正的收发放在线程里跑（asyncio.to_thread），
    再用一把线程锁保证同一时刻只有一个请求在跑。
    """

    def __init__(self, ws_url: str, timeout: float = 30.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self.events: List[Dict[str, Any]] = []
        self._conn: Optional[WebSocketConnection] = None
        self._io_lock = threading.Lock()
        self._next_id = 0

    async def __aenter__(self) -> "CDPSession":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    # ------------------------------------------------------------------
    async def connect(self) -> None:
        self._conn = WebSocketConnection(self.ws_url, timeout=self.timeout)
        try:
            await asyncio.to_thread(self._conn.connect)
        except (WebSocketError, OSError, TimeoutError) as exc:
            raise CDPError(f"连接调试目标失败：{exc}") from exc

    # ------------------------------------------------------------------
    def _call(self, method: str, params: Optional[Dict[str, Any]], timeout: float) -> Dict[str, Any]:
        """阻塞式收发一轮（在线程里执行）。"""
        if self._conn is None:
            raise CDPError("尚未连接")
        with self._io_lock:
            self._next_id += 1
            mid = self._next_id
            payload = json.dumps({"id": mid, "method": method, "params": params or {}})
            deadline = self._conn.deadline(timeout)
            self._conn.send_text(payload)
            while True:
                message = json.loads(self._conn.recv_text(deadline))
                if message.get("id") == mid:
                    if "error" in message:
                        raise CDPError(str(message["error"]))
                    return message.get("result") or {}
                # 不是本次的应答，当事件先存着
                self.events.append(message)
                if len(self.events) > 500:
                    del self.events[:250]

    async def send(
        self, method: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(
                self._call, method, params, timeout or self.timeout
            )
        except CDPError:
            raise
        except asyncio.TimeoutError as exc:
            raise CDPError(f"{method} 超时") from exc
        except (WebSocketError, OSError, TimeoutError, ValueError) as exc:
            # 页面重载 / 窗口关闭会把连接掐掉，统一成 CDPError 好让上层重连
            raise CDPError(f"{method} 连接断开：{exc}") from exc

    # ------------------------------------------------------------------
    async def evaluate(self, expression: str, await_promise: bool = True) -> Any:
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise CDPError(
                f"JS 异常：{detail.get('text')} "
                f"{detail.get('exception', {}).get('description', '')}"
            )
        return (result.get("result") or {}).get("value")

    async def click_point(self, x: float, y: float) -> None:
        """派发真实鼠标事件（比 JS 的 el.click() 更接近手工操作）。"""
        base = {"x": x, "y": y, "button": "left", "clickCount": 1}
        await self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await self.send(
            "Input.dispatchMouseEvent", {**base, "type": "mousePressed", "buttons": 1}
        )
        await self.send(
            "Input.dispatchMouseEvent", {**base, "type": "mouseReleased", "buttons": 0}
        )

    async def navigate(self, url: str) -> None:
        """导航到 url。注意：目标重建会掐断连接，属正常现象。"""
        try:
            await self.send("Page.enable")
        except CDPError:
            pass
        try:
            await self.send("Page.navigate", {"url": url})
        except CDPError:
            pass

    # ------------------------------------------------------------------
    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                await asyncio.to_thread(conn.close)
            except Exception:
                pass
