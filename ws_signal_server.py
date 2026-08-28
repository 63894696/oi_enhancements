"""ws_signal_server.py — Agent-First OS 2 人通话 WebSocket 信令服务器(方案 A)

替换"聊天记录 + 浏览器轮询"的不可靠信令。两端浏览器各自连同一个 WS 服务,
信令(SDP offer/answer + ICE candidates + end/busy)**实时推送**,不轮询、无残留状态。

架构(Jitsi/LiveKit 同思路):
  - 房间 = 一个通话的最小单元。两名参与者按 `room` 加入。
  - 服务器只做**转发**(relay):一端的信令广播给同房间的另一端。不解码、不存储媒体。
  - 每个连接注册 {room, peer_id, role};服务器把消息推给房间里的其他连接。
  - E2E:信令内容(SDP/ICE)本身由两端 WebRTC 生成;服务器只见信令文本,
    真正的媒体走 P2P WebRTC(不过服务器)。

安全:token 认证(与 SecureDM 共用 l4_token 信任根)。监听 127.0.0.1:18810。
远程/VPS 接入后续经 SSH 隧道或反代(同 l4 远程接入)。

协议(JSON 文本帧):
  客户端 → 服务器:{"type":"join","room":"<room>","peer":"<peerId>","token":"<t>"}
  服务器 → 客户端:{"type":"joined","room":..,"peer":..,"peers":[..]} / {"type":"peer-joined"/"peer-left","peer":..}
  客户端 → 服务器:{"type":"signal","room":..,"data":{...}}   # offer/answer/ice/end/busy
  服务器 → 房间其他端:{"type":"signal","from":..,"data":{...}}
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import websockets
from websockets.asyncio.server import serve, ServerConnection

WS_HOST = os.environ.get("WS_SIGNAL_HOST", "0.0.0.0")  # 0.0.0.0:手机经 LAN IP 可达;token 仍守门
WS_PORT = int(os.environ.get("WS_SIGNAL_PORT", "18810"))
_TOKEN_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_token"


def _token() -> str:
    try:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("L4_TOKEN", "")


_ACCESS_TOKEN = _token()

# rooms: room_id -> {peer_id: connection}
_ROOMS: dict[str, dict[str, ServerConnection]] = {}
# connection -> (room, peer)
_CONN_META: dict[ServerConnection, tuple[str, str]] = {}


async def _broadcast(room: str, msg: dict, exclude: ServerConnection | None = None) -> None:
    peers = _ROOMS.get(room, {})
    text = json.dumps(msg, ensure_ascii=False)
    for pid, conn in list(peers.items()):
        if exclude is not None and conn is exclude:
            continue
        try:
            await conn.send(text)
        except Exception:  # noqa: BLE001
            pass


async def _handle(ws: ServerConnection) -> None:
    room = ""
    peer = ""
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            mtype = msg.get("type")
            if mtype == "join":
                # token 校验(与 SecureDM 共用信任根)
                if _ACCESS_TOKEN and msg.get("token") != _ACCESS_TOKEN:
                    await ws.send(json.dumps({"type": "error", "error": "unauthorized"}))
                    await ws.close(code=4001)
                    return
                room = str(msg.get("room", ""))[:128]
                peer = str(msg.get("peer", ""))[:64]
                if not room or not peer:
                    await ws.send(json.dumps({"type": "error", "error": "room+peer required"}))
                    continue
                # 同 room+peer 的旧连接踢掉(防重连叠加)
                _ROOMS.setdefault(room, {})
                old = _ROOMS[room].get(peer)
                if old is not None and old is not ws:
                    try:
                        await old.close(code=4000)
                    except Exception:  # noqa: BLE001
                        pass
                _ROOMS[room][peer] = ws
                _CONN_META[ws] = (room, peer)
                others = [p for p in _ROOMS[room] if p != peer]
                await ws.send(json.dumps({
                    "type": "joined", "room": room, "peer": peer, "peers": others,
                }, ensure_ascii=False))
                # 通知房间其他人:新 peer 加入
                await _broadcast(room, {"type": "peer-joined", "peer": peer}, exclude=ws)
            elif mtype == "signal":
                # 路由到 payload 指定的 room(信令可以跨房间发:主叫发到对方收件房间,
                # 不必自己也在那个房间)。用 msg.room,不是连接当前 room。
                target_room = str(msg.get("room", ""))[:128] or room
                if not target_room:
                    continue
                data = msg.get("data", {})
                # 转发给目标房间里的其他人
                await _broadcast(target_room, {"type": "signal", "from": peer, "data": data}, exclude=ws)
            elif mtype == "ping":
                await ws.send(json.dumps({"type": "pong"}))
    except websockets.exceptions.ConnectionClosed:  # noqa: BLE001
        pass
    finally:
        # 连接断开:从房间移除并通知其他人
        meta = _CONN_META.pop(ws, None)
        if meta:
            room, peer = meta
            peers = _ROOMS.get(room, {})
            if peers.get(peer) is ws:
                del peers[peer]
            if not peers:
                _ROOMS.pop(room, None)
            else:
                await _broadcast(room, {"type": "peer-left", "peer": peer})


async def main() -> None:
    async with serve(_handle, WS_HOST, WS_PORT):
        print(f"WS 信令服务器就绪: ws://{WS_HOST}:{WS_PORT}  (token 认证,房间转发)")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
