"""极简 WebSocket 客户端（纯标准库，零第三方依赖）。

为什么要自己写：
CDP 需要一条 WebSocket 连接。用第三方库 `websockets` 的话，用户如果没有
Python 环境还得多装一次包，网络不通就直接跑不起来。这里只实现 RFC 6455
里必需的那部分——握手、文本帧、分片、ping/pong、关闭帧，够用且稳定。

只支持 ws://（CDP 调试端口永远在本机，不涉及 TLS）。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import time
from urllib.parse import urlparse

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocketError(RuntimeError):
    pass


class WebSocketConnection:
    """阻塞式 WebSocket 客户端。调用方负责用线程包起来，别卡住事件循环。"""

    def __init__(self, url: str, timeout: float = 30.0, max_frame_size: int = 64 * 1024 * 1024):
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "http"):
            raise WebSocketError(f"只支持 ws:// ，收到 {parsed.scheme}://")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.timeout = timeout
        self.max_frame_size = max_frame_size
        self.closed = False
        self._sock: socket.socket | None = None
        self._buf = bytearray()

    # ------------------------------------------------------------------
    # 握手
    # ------------------------------------------------------------------
    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("ascii"))

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WebSocketError("握手过程中连接被关闭")
            header += chunk
            if len(header) > 64 * 1024:
                raise WebSocketError("握手响应头异常地大")

        head, _, rest = header.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 101" not in status:
            raise WebSocketError(f"握手失败：{status}")

        expected = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
        )
        if expected.lower() not in head.lower():
            raise WebSocketError("握手校验失败：Sec-WebSocket-Accept 不匹配")

        self._buf = bytearray(rest)

    # ------------------------------------------------------------------
    # 收发
    # ------------------------------------------------------------------
    def _recv_exact(self, size: int, deadline: float) -> bytes:
        while len(self._buf) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("读取超时")
            self._sock.settimeout(remaining)
            chunk = self._sock.recv(max(4096, size - len(self._buf)))
            if not chunk:
                raise WebSocketError("连接已关闭")
            self._buf += chunk
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def _read_frame(self, deadline: float):
        head = self._recv_exact(2, deadline)
        fin = bool(head[0] & 0x80)
        opcode = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2, deadline))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8, deadline))[0]
        if length > self.max_frame_size:
            raise WebSocketError(f"帧过大：{length} 字节")
        mask = self._recv_exact(4, deadline) if masked else None
        payload = self._recv_exact(length, deadline) if length else b""
        if mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def _send_frame(self, opcode: int, data: bytes) -> None:
        if self._sock is None or self.closed:
            raise WebSocketError("连接已关闭")
        header = bytearray()
        header.append(0x80 | opcode)
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        # 客户端发出的帧必须掩码
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._sock.sendall(bytes(header) + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def recv_message(self, deadline: float):
        """返回 (opcode, payload)。收到关闭帧返回 (8, b"")。"""
        fragments: list[bytes] = []
        opcode = None
        while True:
            fin, op, payload = self._read_frame(deadline)
            if op == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if op == OP_PONG:
                continue
            if op == OP_CLOSE:
                try:
                    self._send_frame(OP_CLOSE, b"")
                except Exception:
                    pass
                self.closed = True
                return OP_CLOSE, b""
            if op != OP_CONT:
                opcode = op
                fragments = [payload]
            else:
                fragments.append(payload)
            if fin:
                return opcode if opcode is not None else OP_TEXT, b"".join(fragments)

    def recv_text(self, deadline: float) -> str:
        opcode, payload = self.recv_message(deadline)
        if opcode == OP_CLOSE:
            raise WebSocketError("连接已被对端关闭")
        return payload.decode("utf-8", "replace")

    def deadline(self, timeout: float | None = None) -> float:
        return time.monotonic() + (timeout if timeout is not None else self.timeout)

    def close(self) -> None:
        self.closed = True
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
