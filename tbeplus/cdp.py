"""极简 Chrome DevTools Protocol 客户端。

为什么不用 Playwright：
TBe+ 桌面端是 2023 年版本的老 Electron 内核，不支持 browser context 管理，
Playwright 的 connect_over_cdp 一连上就报
    Protocol error (Browser.setDownloadBehavior): Browser context management is not supported
所以这里直接对着 DevTools 协议说话——只用到 Runtime / DOM / Input / Page
这几个最基础的域，老内核全都支持。
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any, Dict, List, Optional

import websockets


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
    """连到一个调试目标的 WebSocket，发命令收结果。"""

    def __init__(self, ws_url: str, timeout: float = 30.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self._ws: Any = None
        self._next_id = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader: Optional[asyncio.Task] = None
        self.events: List[Dict[str, Any]] = []

    async def __aenter__(self) -> "CDPSession":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    async def connect(self) -> None:
        self._ws = await websockets.connect(
            self.ws_url, max_size=128 * 1024 * 1024, open_timeout=self.timeout
        )
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                message = json.loads(raw)
                mid = message.get("id")
                if mid is not None and mid in self._pending:
                    future = self._pending.pop(mid)
                    if "error" in message:
                        future.set_exception(CDPError(str(message["error"])))
                    else:
                        future.set_result(message.get("result") or {})
                else:
                    self.events.append(message)
                    if len(self.events) > 500:
                        del self.events[:250]
        except Exception:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CDPError("CDP 连接已断开"))
            self._pending.clear()

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._ws is None:
            raise CDPError("尚未连接")
        self._next_id += 1
        mid = self._next_id
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[mid] = future
        try:
            await self._ws.send(
                json.dumps({"id": mid, "method": method, "params": params or {}})
            )
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(mid, None)
            raise CDPError(f"{method} 超时") from exc
        except CDPError:
            self._pending.pop(mid, None)
            raise
        except Exception as exc:
            # 页面重载 / 窗口关闭会把连接掐掉，统一成 CDPError 好让上层重连
            self._pending.pop(mid, None)
            raise CDPError(f"{method} 连接断开：{exc}") from exc

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

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
