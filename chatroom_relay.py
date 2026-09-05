"""chatroom_relay.py — Agent-First OS 3.B 群聊中继(房间 + 服务器定序 + 成员 + 历史)

在 ws_signal_server(纯转发、无状态、人走房消)基础上,按 3.B 群聊设计惯例扩展为
**群聊房间服务器**。设计依据:Documents/prisiragent-os-integration/stage3-selfhosted-im-design.md §6
与记忆 stage3b-chatroom-design-conventions-2026-07-22。

与 ws_signal_server 的本质差异:
  - signal_server 是**通话信令 relay**:房间={peer:conn} 即时映射,消息不存、不定序,人走房消。
  - chatroom_relay 是**群聊消息中继**:房间**持久**(本进程内存 + 可选 JSON 落盘),
    每条消息分配**服务器单调序号 seq**(Matrix stream ordering / Discord snowflake 同思路,
    单中继即全序权威,不上向量钟/CRDT),支持 `read since_seq` 增量拉历史。

3.B 核心规则(本服务器落实的部分):
  - **服务器定序**:seq 由服务器在收信时分配,客户端乐观回显 + reconcile。
  - **服务器只存密文/不验证签名**:签名验真在客户端做(身份=user_id=Ed25519指纹)。
    服务器只负责定序、广播、存历史,**不知明文、不碰密钥**(为后续 E2E 房间钥留位)。
  - **成员管理**:join/leave/members;在线状态 heartbeat 30s/离线 90s + 宽限期。

协议(JSON 文本帧,token 认证同 l4_token):
  C→S {"type":"join","room":..,"user_id":..,"display_name":..,"token":..,"is_bot":bool,"invite":..,
       "e2e_pub":..,"e2e_x25519":..,"suites":[1,2]}
  S→C {"type":"joined","room":..,"user_id":..,"owner":..,
       "members":[{user_id,display_name,is_bot,online,e2e_pub,e2e_x25519,suites}],"last_seq":N}
  S→广播 {"type":"member-joined"/"member-left","user_id":..,"display_name":..,
          "e2e_pub":..,"e2e_x25519":..,"suites":[..]}
  C→S {"type":"post","room":..,"user_id":..,"display_name":..,"body":{...},"mentions":[uid],"to_user_id":..}
  S→广播 {"type":"msg","room":..,"seq":N,"user_id":..,"display_name":..,"ts":..,"body":{...},"mentions":[..]}
       (若带 to_user_id → 只定向投递给该成员的 conn,仍分配 seq 存 history;目标离线则只存 history)
  C→S {"type":"set-invite","room":..,"user_id":..,"invite":..}   # 仅 owner;回 {"type":"invite-set"}
  C→S {"type":"revoke-invite","room":..,"user_id":..}           # 仅 owner;回 {"type":"invite-revoked"}
  C→S {"type":"leave","room":..,"user_id":..} → 移除成员,广播 {"type":"member-left",...}
  C→S {"type":"read","room":..,"since_seq":N}   # 拉历史(增量)
  S→C {"type":"history","room":..,"msgs":[...],"last_seq":M}
  C→S {"type":"members","room":..} → {"type":"members","members":[...]}
  C→S {"type":"ping"} → {"type":"pong"}

监听 0.0.0.0:18811(token 守门)。部署 VPS,经 nginx 反代 WSS(后续)。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import websockets
from websockets.asyncio.server import serve, ServerConnection

WS_HOST = os.environ.get("CHATROOM_HOST", "0.0.0.0")
WS_PORT = int(os.environ.get("CHATROOM_PORT", "18811"))
_TOKEN_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_token"
_STATE_FILE = Path(os.environ.get("CHATROOM_STATE", "")) if os.environ.get("CHATROOM_STATE") else None
_MAX_HISTORY = int(os.environ.get("CHATROOM_MAX_HISTORY", "500"))


def _token() -> str:
    try:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("L4_TOKEN", "")


_ACCESS_TOKEN = _token()


class Room:
    """一个群聊房间:成员 + 单调序号 + 消息历史。"""

    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
        # user_id -> {display_name, is_bot, conn, last_seen, online}
        self.members: dict[str, dict] = {}
        self.seq = 0  # 服务器单调序号(最后分配的)
        self.history: list[dict] = []  # [{seq,user_id,display_name,ts,body,mentions}]
        self.invite: str = ""  # 入门密钥(空=开放房);仅 owner 可设/换/吊销
        self.owner: str = ""   # 首个加入者的 user_id(房主,可管 invite)

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def add_msg(self, user_id: str, display_name: str, body: dict, mentions: list[str],
                pubkey: str = "", sig: str = "") -> dict:
        m = {
            "seq": self.next_seq(),
            "user_id": user_id,
            "display_name": display_name,
            "ts": int(time.time() * 1000),
            "body": body,
            "mentions": mentions or [],
            # 透传签名材料:服务器只定序转发、不验签(验签在客户端,3.B 身份=公钥)
            "pubkey": pubkey,
            "sig": sig,
        }
        self.history.append(m)
        if len(self.history) > _MAX_HISTORY:
            self.history = self.history[-_MAX_HISTORY:]
        return m

    def member_list(self) -> list[dict]:
        out = []
        for uid, m in self.members.items():
            ent = {
                "user_id": uid,
                "display_name": m["display_name"],
                "is_bot": bool(m.get("is_bot")),
                "online": bool(m.get("online")),
            }
            # E2E 透传字段:仅当成员 join 时带了才带出(旧客户端不受影响)。
            # relay 不解析其内容,纯管道。
            if m.get("e2e_pub"):
                ent["e2e_pub"] = m["e2e_pub"]
            if m.get("e2e_x25519"):
                ent["e2e_x25519"] = m["e2e_x25519"]
            if m.get("suites"):
                ent["suites"] = m["suites"]
            out.append(ent)
        return out

    def to_json(self) -> dict:
        return {"room_id": self.room_id, "seq": self.seq, "history": self.history,
                "invite": self.invite, "owner": self.owner,
                "members": [{k: v for k, v in m.items() if k not in ("conn",)} | {"user_id": uid}
                            for uid, m in self.members.items()]}


# room_id -> Room
_ROOMS: dict[str, Room] = {}
# conn -> (room_id, user_id)
_CONN_META: dict[ServerConnection, tuple[str, str]] = {}


def _get_room(room_id: str) -> Room:
    return _ROOMS.setdefault(room_id, Room(room_id))


async def _send(conn: ServerConnection, msg: dict) -> bool:
    try:
        await conn.send(json.dumps(msg, ensure_ascii=False))
        return True
    except Exception:  # noqa: BLE001
        return False


async def _broadcast(room: Room, msg: dict, exclude: ServerConnection | None = None) -> None:
    text = json.dumps(msg, ensure_ascii=False)
    for uid, m in list(room.members.items()):
        conn = m.get("conn")
        if conn is None or (exclude is not None and conn is exclude):
            continue
        try:
            await conn.send(text)
        except Exception:  # noqa: BLE001
            pass


def _save_state() -> None:
    if not _STATE_FILE:
        return
    try:
        data = {rid: r.to_json() for rid, r in _ROOMS.items()}
        _STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _load_state() -> None:
    if not _STATE_FILE or not _STATE_FILE.exists():
        return
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        for rid, rd in data.items():
            r = Room(rid)
            r.seq = rd.get("seq", 0)
            r.history = rd.get("history", [])
            r.invite = rd.get("invite", "")
            r.owner = rd.get("owner", "")
            # 成员不恢复连接,只恢复名册(online=False)
            for m in rd.get("members", []):
                ent = {
                    "display_name": m.get("display_name", m["user_id"]),
                    "is_bot": m.get("is_bot", False),
                    "conn": None, "last_seen": 0, "online": False,
                }
                if m.get("e2e_pub"):
                    ent["e2e_pub"] = m["e2e_pub"]
                if m.get("e2e_x25519"):
                    ent["e2e_x25519"] = m["e2e_x25519"]
                if m.get("suites"):
                    ent["suites"] = m["suites"]
                r.members[m["user_id"]] = ent
            _ROOMS[rid] = r
    except Exception:  # noqa: BLE001
        pass


async def _handle(ws: ServerConnection) -> None:
    room_id = ""
    user_id = ""
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            mtype = msg.get("type")

            if mtype == "join":
                if _ACCESS_TOKEN and msg.get("token") != _ACCESS_TOKEN:
                    await _send(ws, {"type": "error", "error": "unauthorized"})
                    await ws.close(code=4001)
                    return
                room_id = str(msg.get("room", ""))[:128]
                user_id = str(msg.get("user_id", ""))[:64]
                display_name = str(msg.get("display_name", user_id))[:64]
                is_bot = bool(msg.get("is_bot", False))
                # E2E 透传字段(纯管道,不解析):e2e_pub = suite=1 conduit 分发公钥(b64);
                # e2e_x25519 = suite=2 跨端 X25519 公钥(b64 raw32,独立字段避免
                # conduit pub(1250B)挤爆 e2e_pub 的 2048 截断);suites = 支持的套件。
                e2e_pub = str(msg.get("e2e_pub", "") or "")[:2048]
                e2e_x25519 = str(msg.get("e2e_x25519", "") or "")[:256]
                suites_raw = msg.get("suites")
                suites = ([int(s) for s in suites_raw if isinstance(s, (int, float))][:8]
                          if isinstance(suites_raw, list) else [])
                if not room_id or not user_id:
                    await _send(ws, {"type": "error", "error": "room+user_id required"})
                    continue
                room = _get_room(room_id)
                # invite 门禁:房间设了入门密钥且未带对 → 拒绝并断连(invite 不入日志/不回显)
                if room.invite and msg.get("invite") != room.invite:
                    await _send(ws, {"type": "error", "error": "invite-required"})
                    await ws.close(code=4003)
                    return
                # 同 user_id 旧连接踢掉(防重连叠加)
                old = room.members.get(user_id)
                if old is not None and old.get("conn") is not None and old["conn"] is not ws:
                    try:
                        await old["conn"].close(code=4000)
                    except Exception:  # noqa: BLE001
                        pass
                is_new = old is None
                room.members[user_id] = {
                    "display_name": display_name, "is_bot": is_bot,
                    "conn": ws, "last_seen": time.time(), "online": True,
                }
                if e2e_pub:
                    room.members[user_id]["e2e_pub"] = e2e_pub
                if e2e_x25519:
                    room.members[user_id]["e2e_x25519"] = e2e_x25519
                if suites:
                    room.members[user_id]["suites"] = suites
                # 新房第一个加入者即 owner
                if not room.owner:
                    room.owner = user_id
                _CONN_META[ws] = (room_id, user_id)
                await _send(ws, {
                    "type": "joined", "room": room_id, "user_id": user_id,
                    "owner": room.owner,  # 精确房主判定(客户端无需启发式)
                    "members": room.member_list(), "last_seq": room.seq,
                })
                joined_ev = {
                    "type": "member-joined" if is_new else "member-online",
                    "user_id": user_id, "display_name": display_name, "is_bot": is_bot,
                }
                if e2e_pub:
                    joined_ev["e2e_pub"] = e2e_pub
                if e2e_x25519:
                    joined_ev["e2e_x25519"] = e2e_x25519
                if suites:
                    joined_ev["suites"] = suites
                await _broadcast(room, joined_ev, exclude=ws)
                _save_state()

            elif mtype == "post":
                if not room_id or not user_id:
                    continue
                room = _ROOMS.get(room_id)
                if room is None or user_id not in room.members:
                    continue
                member = room.members[user_id]
                body = msg.get("body", {})
                mentions = [str(u)[:64] for u in (msg.get("mentions") or [])]
                rec = room.add_msg(user_id, member["display_name"], body, mentions,
                                   pubkey=str(msg.get("pubkey", "")), sig=str(msg.get("sig", "")))
                member["last_seen"] = time.time()
                # 定向投递:带非空 to_user_id 时只投给该成员的 conn(密钥分发用),
                # 不广播;仍分配 seq、仍存 history(密文)。目标离线则只存 history 不报错。
                to_uid = str(msg.get("to_user_id", "") or "")[:64]
                if to_uid:
                    target = room.members.get(to_uid)
                    tconn = target.get("conn") if target else None
                    if tconn is not None:
                        await _send(tconn, {"type": "msg", "room": room_id, **rec})
                else:
                    await _broadcast(room, {"type": "msg", "room": room_id, **rec})
                _save_state()

            elif mtype == "set-invite":
                # 仅 owner 可设/换 invite(值不入日志、不回显)
                if not room_id or not user_id:
                    continue
                room = _ROOMS.get(room_id)
                if room is None or user_id not in room.members or user_id != room.owner:
                    continue
                room.invite = str(msg.get("invite", "") or "")[:128]
                await _send(ws, {"type": "invite-set", "room": room_id})
                _save_state()

            elif mtype == "revoke-invite":
                # 仅 owner 可吊销 invite(变开放房);不影响已加入成员
                if not room_id or not user_id:
                    continue
                room = _ROOMS.get(room_id)
                if room is None or user_id not in room.members or user_id != room.owner:
                    continue
                room.invite = ""
                await _send(ws, {"type": "invite-revoked", "room": room_id})
                _save_state()

            elif mtype == "leave":
                # 显式离开:从 room.members 移除(非标离线),广播 member-left。
                # 这是触发其他成员轮换房间钥的信号。
                if not room_id or not user_id:
                    continue
                room = _ROOMS.get(room_id)
                if room is None or user_id not in room.members:
                    continue
                m = room.members.pop(user_id)
                _CONN_META.pop(ws, None)
                await _broadcast(room, {
                    "type": "member-left", "user_id": user_id,
                    "display_name": m["display_name"],
                })
                _save_state()

            elif mtype == "read":
                rid = str(msg.get("room", room_id))[:128]
                room = _ROOMS.get(rid)
                if room is None:
                    await _send(ws, {"type": "history", "room": rid, "msgs": [], "last_seq": 0})
                    continue
                try:
                    since = int(msg.get("since_seq", 0))
                except Exception:  # noqa: BLE001
                    since = 0
                msgs = [m for m in room.history if m["seq"] > since]
                await _send(ws, {"type": "history", "room": rid, "msgs": msgs, "last_seq": room.seq})

            elif mtype == "members":
                rid = str(msg.get("room", room_id))[:128]
                room = _ROOMS.get(rid)
                await _send(ws, {
                    "type": "members", "room": rid,
                    "members": room.member_list() if room else [],
                })

            elif mtype == "ping":
                if room_id and user_id and room_id in _ROOMS and user_id in _ROOMS[room_id].members:
                    _ROOMS[room_id].members[user_id]["last_seen"] = time.time()
                await _send(ws, {"type": "pong"})

    except websockets.exceptions.ConnectionClosed:  # noqa: BLE001
        pass
    finally:
        meta = _CONN_META.pop(ws, None)
        if meta:
            room_id, user_id = meta
            room = _ROOMS.get(room_id)
            if room and user_id in room.members:
                m = room.members[user_id]
                if m.get("conn") is ws:
                    # 宽限期:标记离线而非立即移除(吸收网络抖动)
                    m["online"] = False
                    m["conn"] = None
                    m["last_seen"] = time.time()
                    await _broadcast(room, {
                        "type": "member-offline", "user_id": user_id,
                        "display_name": m["display_name"],
                    })
                    _save_state()


async def main() -> None:
    _load_state()
    async with serve(_handle, WS_HOST, WS_PORT):
        print(f"3.B 群聊中继就绪: ws://{WS_HOST}:{WS_PORT}  (房间+定序+成员+历史, token 认证)")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _save_state()
