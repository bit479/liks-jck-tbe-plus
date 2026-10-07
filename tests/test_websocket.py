#!/usr/bin/env python
"""自检：本地起一个迷你 WebSocket 服务器，验证 tbeplus/ws.py 和 tbeplus/cdp.py。

覆盖的点：
  * 握手（含 Sec-WebSocket-Accept 校验）
  * 客户端帧必须掩码（服务器侧反掩码解出来）
  * 大消息（走 16 位/64 位长度字段）
  * 分片消息（服务端把一条消息拆成两个帧发）
  * 服务端主动发 ping，客户端要回 pong 且不影响后续收发
  * 事件消息（没有 id 的报文）要能被缓存下来

跑法：  python tests/test_websocket.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tbeplus.cdp import CDPSession, CDPError  # noqa: E402

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
BIG_TEXT = "大" * 200_000  # 200k 字符，验证扩展长度字段


# ---------------------------------------------------------------------------
# 服务端侧的最小实现
# ---------------------------------------------------------------------------
def recv_exact(conn: socket.socket, size: int) -> bytes:
    buf = b""
    while len(buf) < size:
        chunk = conn.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("对端关闭")
        buf += chunk
    return buf


def read_client_frame(conn: socket.socket):
    head = recv_exact(conn, 2)
    fin = bool(head[0] & 0x80)
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", recv_exact(conn, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(conn, 8))[0]
    mask = recv_exact(conn, 4) if masked else None
    payload = recv_exact(conn, length) if length else b""
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload, masked


def send_frame(conn: socket.socket, opcode: int, data: bytes, fin: bool = True) -> None:
    header = bytearray()
    header.append((0x80 if fin else 0) | opcode)
    length = len(data)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    conn.sendall(bytes(header) + data)


def handshake(conn: socket.socket) -> None:
    data = b""
    while b"\r\n\r\n" not in data:
        data += conn.recv(4096)
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    key = ""
    for line in head.split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    conn.sendall(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        ).encode("ascii")
    )


def server_worker(conn: socket.socket, state: dict) -> None:
    try:
        handshake(conn)
        state["handshake"] = True

        # 先发一个 ping，看看客户端会不会乖乖回 pong
        send_frame(conn, 0x9, b"hello")

        # 再发一条没有 id 的事件消息
        send_frame(conn, 0x1, json.dumps({"method": "Test.event", "params": {"n": 1}}).encode())

        while True:
            try:
                fin, opcode, payload, masked = read_client_frame(conn)
            except (ConnectionError, OSError):
                # 客户端正常关连接，属于预期结束
                state["closed_by_client"] = True
                break
            if not masked:
                state["unmasked_from_client"] = True
            if opcode == 0xA:  # 客户端回过 pong 了
                state["got_pong"] = True
                continue
            if opcode == 0x8:
                break
            if opcode != 0x1:
                continue
            message = json.loads(payload.decode("utf-8"))
            state["methods"].append(message.get("method"))
            if message.get("method") == "Runtime.evaluate":
                # 分两帧发一条大消息，顺便验证分片
                blob = json.dumps(
                    {"id": message["id"], "result": {"result": {"value": BIG_TEXT}}}
                ).encode("utf-8")
                half = len(blob) // 2
                send_frame(conn, 0x1, blob[:half], fin=False)
                send_frame(conn, 0x0, blob[half:], fin=True)
            else:
                send_frame(
                    conn,
                    0x1,
                    json.dumps(
                        {"id": message["id"], "result": {"echo": message.get("method")}}
                    ).encode(),
                )
    except Exception as exc:  # noqa: BLE001
        state["error"] = repr(exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def start_server() -> tuple[socket.socket, int, dict, threading.Thread]:
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    state: dict = {"methods": []}

    def accept_once():
        conn, _ = listener.accept()
        server_worker(conn, state)

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()
    return listener, port, state, thread


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def main() -> int:
    listener, port, state, thread = start_server()
    failures: list[str] = []
    session = None

    async def scenario():
        nonlocal session
        session = CDPSession(f"ws://127.0.0.1:{port}/devtools/page/TEST", timeout=20)
        await session.connect()
        # 大消息 + 分片
        value = await session.evaluate("1+1")
        if value != BIG_TEXT:
            failures.append(f"大消息/分片读取不正确，长度 {len(value) if value else 0}")
        # 普通请求
        result = await session.send("Page.enable")
        if result.get("echo") != "Page.enable":
            failures.append(f"普通请求应答不对：{result}")
        # 事件消息应该被缓存下来
        if not any(e.get("method") == "Test.event" for e in session.events):
            failures.append("没有缓存到事件消息")
        await session.close()

    try:
        import asyncio

        asyncio.run(scenario())
    except CDPError as exc:
        failures.append(f"CDP 调用失败：{exc}")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"异常：{exc!r}")
    finally:
        listener.close()
        thread.join(timeout=3)

    if not state.get("handshake"):
        failures.append("握手没完成")
    if state.get("unmasked_from_client"):
        failures.append("客户端发的帧没有掩码（协议违规）")
    if not state.get("got_pong"):
        failures.append("收到服务端 ping 后没有回 pong")
    if state.get("error"):
        failures.append(f"服务端异常：{state['error']}")

    print("=" * 60)
    print(f"服务端收到的请求：{state.get('methods')}")
    print(f"握手成功={state.get('handshake')}  收到 pong={state.get('got_pong')}  "
          f"客户端帧已掩码={not state.get('unmasked_from_client')}")
    print("=" * 60)
    if failures:
        for item in failures:
            print(f"[FAIL] {item}")
        return 1
    print("[PASS] WebSocket 实现全部通过（握手 / 掩码 / 大消息 / 分片 / ping-pong / 事件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
