"""chatroom_client.py — 3.B 群聊客户端功能块(签名身份 + 收发,面向 agent / CLI)

L2 功能块,供 VPS 端壳 / 本地 / 手机任一端独立跑,也可被 daemon 调用。
配套中继 chatroom_relay.py(房间+服务器定序+成员+历史)。

3.B 身份规则(本块落实):
  - **密钥对即身份**:临时 Ed25519 密钥对,user_id = 公钥指纹(base64 前 16 字符)。
    显示名是自由文本、允许重名;撞名靠 user_id 派生颜色/字母消歧(前端职责)。
  - **消息签名**:body.canonical(排序 JSON)用 Ed25519 私钥签名,接收方用 user_id 对应
    公钥验真防冒充(两个"alice"指纹不同)。服务器只定序转发,不验签、不知明文。
  - **断线重连**:本块自动重连 + 重 join;可选 --state 持久化密钥(否则每次新身份)。

v1 明文房间;可选 E2E 模式(--e2e):接 securedm_groupchat_e2e.GroupE2E,
join 公告分发公钥,post 发密文,收到 key-dist 自动登记,owner 收 member-left 自动轮换。

CLI:
  python chatroom_client.py --url ws://127.0.0.1:18811 --room test --name alice \\
      [--key-file alice.key] [--say "你好"] [--listen] [--bot] [--history] \\
      [--e2e] [--owner]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import websocket  # websocket-client
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

_TOKEN_FILE = Path.home() / ".local" / "share" / "aureon" / "l4_token"


def _token() -> str:
    try:
        if _TOKEN_FILE.exists():
            return _TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("L4_TOKEN", "")


def _canonical(obj) -> bytes:
    """确定性序列化(排序键、紧凑、UTF-8),签名/验签双方必须一致。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class Identity:
    """Ed25519 密钥对即身份。user_id = 公钥指纹(base64url 前 16 字符,去 =)。"""

    def __init__(self, priv: Ed25519PrivateKey):
        self.priv = priv
        self.pub: Ed25519PublicKey = priv.public_key()
        self.pub_bytes = self.pub.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.user_id = base64.urlsafe_b64encode(
            hashlib.sha256(self.pub_bytes).digest()).decode().rstrip("=")[:16]

    @classmethod
    def generate(cls) -> "Identity":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def load(cls, path: Path) -> "Identity":
        raw = base64.b64decode(path.read_text().strip())
        return cls(Ed25519PrivateKey.from_private_bytes(raw))

    def save(self, path: Path) -> None:
        raw = self.priv.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
            serialization.NoEncryption())
        path.write_text(base64.b64encode(raw).decode())

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(self.priv.sign(payload)).decode()


def verify(user_pub_b64: str, payload: bytes, sig_b64: str) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(user_pub_b64))
        pub.verify(base64.b64decode(sig_b64), payload)
        return True
    except Exception:  # noqa: BLE001
        return False


def encode_e2e_pub(e2e) -> str:
    """(兼容保留)suite=1 conduit pub 的 b64。suite=2 的 X25519 pub 走独立
    join 字段 e2e_x25519(conduit pub 1250B,双钥 JSON 打包会超 relay 2048 截断)。"""
    return base64.b64encode(e2e.public).decode()


def decode_e2e_pub(e2e_pub: str) -> dict:
    """解析 e2e_pub → {"rust": bytes|None, "x25519": bytes|None}。
    主流格式是裸 b64(conduit pub)→ 只填 rust;兼容旧 JSON 打包格式
    (b64(json({"r"/"rust","x"/"x25519"})))→ 两键都解。"""
    out = {"rust": None, "x25519": None}
    raw = base64.b64decode(e2e_pub)
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        obj = None
    if isinstance(obj, dict):
        for keys, dst in ((("r", "rust"), "rust"), (("x", "x25519"), "x25519")):
            for k in keys:
                if obj.get(k):
                    try:
                        out[dst] = base64.b64decode(obj[k])
                    except Exception:  # noqa: BLE001
                        pass
                    break
    else:
        out["rust"] = raw  # 主流:裸 conduit PeerPublic
    return out


class ChatClient:
    """一个群聊成员:join/post/read/listen,自动签名 + 验签 + 重连。可选 E2E。

    E2E(传入 e2e=GroupE2E 时启用):
      - join 公告 e2e_pub(b64 conduit pub,suite=1)+ e2e_x25519(b64 X25519 raw32,
        suite=2)+ suites=[1,2],并从 joined/member-joined 收集其他成员的公钥进
        member_pubs({uid:{"rust":..,"x25519":..}},逐人分发的前提)。
      - post_seal 发 ctext;收到 key-dist(定向给自己的)按 suite 自动 unwrap 登记,
        收到 ctext 自动 open_msg 解出明文交 on_msg(带 decrypted 字段)。
      - is_owner=True 时,收到 member-left 自动 rotate_room(给剩余成员逐人定向
        分发新钥:有 x25519 公钥→suite=2 全端通用,只有 rust→suite=1)。
    """

    def __init__(self, url: str, room: str, name: str, identity: Identity,
                 is_bot: bool = False, on_msg=None, on_event=None,
                 e2e=None, is_owner: bool = False):
        self.url = url
        self.room = room
        self.name = name
        self.id = identity
        self.is_bot = is_bot
        self.on_msg = on_msg      # (msg_dict, verified: bool)
        self.on_event = on_event  # (event_dict)
        self.e2e = e2e            # GroupE2E | None
        self.is_owner = is_owner  # 声明房主:收到 member-left 自动轮换
        self.owner_uid = ""       # joined 回的精确 owner(权威判定)
        # user_id -> {"rust": PeerPublic bytes|None, "x25519": raw32 bytes|None}(E2E 成员)
        self.member_pubs: dict[str, dict] = {}
        self.ws: websocket.WebSocket | None = None
        self.last_seq = 0
        self.members: dict[str, dict] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._recv_thread: threading.Thread | None = None

    # ── 连接 ──────────────────────────────────────────────────────────── #
    def connect(self) -> bool:
        try:
            self.ws = websocket.create_connection(self.url, timeout=15)
        except Exception as e:  # noqa: BLE001
            self._emit({"type": "client-error", "error": f"connect: {e}"})
            return False
        join = {
            "type": "join", "room": self.room, "user_id": self.id.user_id,
            "display_name": self.name, "token": _token(), "is_bot": self.is_bot,
            # 公钥随 join 公告,供其他成员验签(身份=公钥,无需服务端登记)
            "pubkey": base64.b64encode(self.id.pub_bytes).decode(),
        }
        if self.e2e is not None:
            # E2E 公告:suite=1 conduit pub 走 e2e_pub(b64,relay 透传≤2048);
            # suite=2 跨端 X25519 pub 走独立字段 e2e_x25519(b64 raw32,44 字符)。
            # 拆两个字段是因 conduit pub 本身 1250B,JSON 打包双钥会超 relay 截断。
            join["e2e_pub"] = base64.b64encode(self.e2e.public).decode()
            join["e2e_x25519"] = base64.b64encode(self.e2e.x25519_public_bytes).decode()
            join["suites"] = [1, 2]
        self._send(join)
        return True

    def _send(self, obj: dict) -> None:
        with self._lock:
            if self.ws:
                try:
                    self.ws.send(json.dumps(obj, ensure_ascii=False))
                except Exception:  # noqa: BLE001
                    pass

    # ── 收发 ──────────────────────────────────────────────────────────── #
    def _post_body(self, body: dict, mentions: list[str] | None = None,
                   to_user_id: str = "") -> None:
        """签名并发一个任意 body 的 post(text/ctext/key-dist 共用)。"""
        sig = self.id.sign(_canonical({"room": self.room, "body": body, "user_id": self.id.user_id}))
        obj = {
            "type": "post", "room": self.room, "user_id": self.id.user_id,
            "display_name": self.name, "body": body, "mentions": mentions or [],
            "pubkey": base64.b64encode(self.id.pub_bytes).decode(), "sig": sig,
        }
        if to_user_id:
            obj["to_user_id"] = to_user_id
        self._send(obj)

    def post(self, text: str, mentions: list[str] | None = None) -> None:
        self._post_body({"kind": "text", "text": text}, mentions)

    def post_seal(self, text: str, suite: int = 2) -> None:
        """E2E 发送:seal_msg 产 ctext body,签名逻辑同 post。需已登记房间钥。"""
        if self.e2e is None:
            raise RuntimeError("post_seal requires e2e")
        self._post_body(self.e2e.seal_msg(self.room, text, suite=suite))

    def rotate_room(self) -> int:
        """owner 在成员离开后轮换:epoch+1 新钥,对剩余成员(除自己)逐人定向分发。
        按成员公告的公钥选套件:有 x25519 → suite=2(全端通用);只有 rust → suite=1。
        返回发出的 key-dist 数量。"""
        if self.e2e is None:
            return 0
        others = {uid: pub for uid, pub in self.member_pubs.items()
                  if uid != self.id.user_id}
        bodies = self.e2e.rotate(self.room, others)
        for body in bodies:
            self._post_body(body, to_user_id=str(body.get("to_user_id") or ""))
        return len(bodies)

    def _collect_e2e_pub(self, uid: str, e2e_pub: str = "",
                         e2e_x25519: str = "") -> None:
        """从 joined/member-joined 透传字段收集成员分发公钥(套件协商/逐人分发用)。
        e2e_pub = suite=1 conduit pub(b64);e2e_x25519 = suite=2 X25519 raw32(b64)。
        兼容旧 JSON 打包格式(b64(json({"r"/"rust","x"/"x25519"})))与裸 conduit pub。"""
        if not uid or (not e2e_pub and not e2e_x25519):
            return
        ent = {"rust": None, "x25519": None}
        if e2e_pub:
            try:
                parsed = decode_e2e_pub(e2e_pub)
                ent["rust"] = parsed.get("rust")
                ent["x25519"] = parsed.get("x25519")
            except Exception:  # noqa: BLE001
                pass
        if e2e_x25519:
            try:
                ent["x25519"] = base64.b64decode(e2e_x25519)
            except Exception:  # noqa: BLE001
                pass
        if ent["rust"] is None and ent["x25519"] is None:
            return
        # 与已有记录合并(后到的字段不覆盖已收的非空值,除非新值非空)
        old = self.member_pubs.get(uid) or {"rust": None, "x25519": None}
        self.member_pubs[uid] = {
            "rust": ent["rust"] or old.get("rust"),
            "x25519": ent["x25519"] or old.get("x25519"),
        }

    def read_history(self, since_seq: int = 0) -> None:
        self._send({"type": "read", "room": self.room, "since_seq": since_seq})

    def request_members(self) -> None:
        self._send({"type": "members", "room": self.room})

    # ── 接收循环 ──────────────────────────────────────────────────────── #
    def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "joined":
            self.last_seq = msg.get("last_seq", 0)
            self.owner_uid = msg.get("owner", "")  # relay 回的精确房主(权威判定)
            for m in msg.get("members", []):
                self.members[m["user_id"]] = m
                self._collect_e2e_pub(m["user_id"], m.get("e2e_pub", ""),
                                      m.get("e2e_x25519", ""))
            self._emit(msg)
        elif t == "msg":
            self.last_seq = max(self.last_seq, msg.get("seq", 0))
            verified = False
            pk, sig = msg.get("pubkey"), msg.get("sig")
            claimed_uid = msg.get("user_id", "")
            if pk and sig:
                # 防冒充:先从消息自带 pubkey 重算 user_id,必须等于消息声称的 user_id,
                # 否则任何人都能挂别人 pubkey 冒名。指纹一致才验签。
                real_uid = base64.urlsafe_b64encode(hashlib.sha256(
                    base64.b64decode(pk)).digest()).decode().rstrip("=")[:16]
                if real_uid == claimed_uid:
                    verified = verify(pk, _canonical({
                        "room": self.room, "body": msg.get("body"), "user_id": claimed_uid,
                    }), sig)
            body = msg.get("body") or {}
            kind = body.get("kind")
            if self.e2e is not None and kind == "key-dist":
                # 逐人分发:只认领定向给自己的(to_user_id 缺省/不匹配则忽略)
                to_uid = body.get("to_user_id")
                if to_uid in (None, "", self.id.user_id):
                    try:
                        self.e2e.unwrap_key_dist(body)
                        msg["key_dist_ok"] = True
                    except Exception:  # noqa: BLE001
                        msg["key_dist_ok"] = False
                if self.on_msg:
                    self.on_msg(msg, verified)
            elif self.e2e is not None and kind == "ctext":
                # 密文消息:按 body.epoch 取钥解密,明文放 msg["decrypted"] 交 on_msg;
                # 解不开(无该 epoch 钥/验签失败)→ decrypted=None + decrypt_ok=False 标记。
                try:
                    msg["decrypted"] = self.e2e.open_msg(body)
                    msg["decrypt_ok"] = True
                except Exception:  # noqa: BLE001
                    msg["decrypted"] = None
                    msg["decrypt_ok"] = False
                if self.on_msg:
                    self.on_msg(msg, verified)
            else:
                if self.on_msg:
                    self.on_msg(msg, verified)
        elif t in ("member-joined", "member-online", "member-offline", "member-left"):
            uid = msg.get("user_id")
            if uid:
                if t in ("member-joined", "member-online"):
                    self.members[uid] = {"user_id": uid, "display_name": msg.get("display_name", uid),
                                         "is_bot": msg.get("is_bot", False), "online": True}
                    self._collect_e2e_pub(uid, msg.get("e2e_pub", ""),
                                          msg.get("e2e_x25519", ""))
                elif uid in self.members:
                    self.members[uid]["online"] = False
            if t == "member-left" and uid:
                self.member_pubs.pop(uid, None)  # 离开者不再参与分发
            self._emit(msg)
            # 房主在成员显式离开后自动轮换房间钥(前向保密)
            if t == "member-left" and self.is_owner and self.e2e is not None \
                    and uid != self.id.user_id:
                try:
                    self.rotate_room()
                except Exception as e:  # noqa: BLE001
                    self._emit({"type": "client-error", "error": f"rotate: {e}"})
        elif t in ("history", "members", "error"):
            if t == "members":
                for m in msg.get("members", []):
                    self.members[m["user_id"]] = m
                    self._collect_e2e_pub(m["user_id"], m.get("e2e_pub", ""),
                                          m.get("e2e_x25519", ""))
            self._emit(msg)

    def _emit(self, ev: dict) -> None:
        if self.on_event:
            self.on_event(ev)

    def listen(self, reconnect: bool = True) -> None:
        """阻塞接收循环;断线自动重连 + 重 join + 补拉历史。"""
        while not self._stop.is_set():
            if self.ws is None:
                if not self.connect():
                    time.sleep(2)
                    continue
            try:
                raw = self.ws.recv()
                if raw is None:
                    raise ConnectionError("closed")
                self._handle(json.loads(raw))
            except Exception as e:  # noqa: BLE001
                self._emit({"type": "client-disconnect", "error": str(e)})
                try:
                    if self.ws:
                        self.ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self.ws = None
                if not reconnect:
                    break
                time.sleep(2)  # 重连退避

    def start_background(self) -> None:
        self._recv_thread = threading.Thread(target=self.listen, daemon=True)
        self._recv_thread.start()
        # 等 joined
        for _ in range(50):
            if self.id.user_id in self.members or self.last_seq >= 0 and self.members:
                break
            time.sleep(0.1)

    def start_recv_only(self) -> None:
        """单次模式的后台 recv(不自动重连)。say/history 等一次性操作也要 recv,
        否则收不到 joined/history 响应(它们只在 recv 循环里分发)。"""
        def _loop():
            while not self._stop.is_set() and self.ws is not None:
                try:
                    raw = self.ws.recv()
                    if raw is None:
                        break
                    self._handle(json.loads(raw))
                except Exception:  # noqa: BLE001
                    break
        self._recv_thread = threading.Thread(target=_loop, daemon=True)
        self._recv_thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self.ws:
                self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description="3.B 群聊客户端功能块")
    ap.add_argument("--url", default=os.environ.get("CHATROOM_URL", "ws://127.0.0.1:18811"))
    ap.add_argument("--room", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--key-file", default="", help="Ed25519 私钥文件(无则生成新身份)")
    ap.add_argument("--bot", action="store_true", help="以机器人身份加入(带 BOT 徽)")
    ap.add_argument("--say", default="", help="发一条消息后退出")
    ap.add_argument("--listen", action="store_true", help="持续监听")
    ap.add_argument("--history", action="store_true", help="拉一次历史后退出")
    ap.add_argument("--e2e", action="store_true",
                    help="启用群聊 E2E(密钥材料持久化在 --key-file 旁的 <key>.e2e,"
                         "seed128+X25519 打包)")
    ap.add_argument("--owner", action="store_true",
                    help="声明房主:收到 member-left 自动轮换房间钥并逐人分发")
    args = ap.parse_args()

    key_path = Path(args.key_file) if args.key_file else None
    if key_path and key_path.exists():
        ident = Identity.load(key_path)
    else:
        ident = Identity.generate()
        if key_path:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            ident.save(key_path)

    # E2E 密钥材料:seed128 + X25519 打包(160B),持久化在 --key-file 旁的
    # <name>.e2e(不打印真值);旧 128B 文件自动升级(重新生成 X25519 并回写)。
    e2e = None
    if args.e2e:
        from securedm_groupchat_e2e import GroupE2E
        seed_path = (key_path.parent / (key_path.name + ".e2e")) if key_path \
            else Path("chatroom_e2e.seed")
        if seed_path.exists():
            blob = base64.b64decode(seed_path.read_text().strip())
            e2e = GroupE2E.from_secret(blob)
            if len(blob) != 160:  # 旧格式 → 升级回写(带上新的 X25519)
                seed_path.write_text(base64.b64encode(e2e.export_secret()).decode())
        else:
            e2e = GroupE2E()
            seed_path.write_text(base64.b64encode(e2e.export_secret()).decode())

    def on_msg(m, verified):
        vb = "✓" if verified else "✗未验签"
        body = m.get("body") or {}
        kind = body.get("kind")
        bot = "[BOT]" if kind == "bot" else ""
        if kind == "ctext":
            if m.get("decrypt_ok"):
                text = f"🔒{m['decrypted']}"
            else:
                text = "🔒[加密消息,无钥]"
        elif kind == "key-dist":
            text = f"🔑 房间钥分发(epoch={body.get('epoch')}, {'已登记' if m.get('key_dist_ok') else '登记失败'})"
        else:
            text = body.get("text", body)
        print(f"[#{m.get('seq')}] {m.get('display_name')}{bot}({m.get('user_id')[:8]}…){vb}: "
              f"{text}", flush=True)

    def on_event(ev):
        t = ev.get("type")
        if t == "joined":
            names = [f"{m['display_name']}({'BOT' if m.get('is_bot') else '人'}{'●' if m.get('online') else '○'})"
                     for m in ev.get("members", [])]
            who = "房主" if ev.get("owner") == ident.user_id else "成员"
            e2e_tag = " [E2E]" if e2e else ""
            print(f"⟡ 已加入 {ev.get('room')}(我={args.name}/{ident.user_id[:8]}…/{who}{e2e_tag}) 成员: {', '.join(names)}", flush=True)
        elif t == "history":
            print(f"⟡ 历史 {len(ev.get('msgs', []))} 条 (last_seq={ev.get('last_seq')})", flush=True)
            for m in ev.get("msgs", []):
                on_msg(m, bool(m.get("sig")))
        elif t in ("member-joined", "member-online"):
            print(f"⟡ {ev.get('display_name')} 上线", flush=True)
        elif t in ("member-offline", "member-left"):
            print(f"⟡ {ev.get('display_name')} 离线", flush=True)
            if t == "member-left" and args.owner and e2e:
                ep = e2e.book.current_epoch(args.room)
                print(f"⟡ 房主已自动轮换房间钥(epoch={ep})", flush=True)
        elif t == "error":
            print(f"✗ 服务器错误: {ev.get('error')}", flush=True)

    c = ChatClient(args.url, args.room, args.name, ident, is_bot=args.bot,
                   on_msg=on_msg, on_event=on_event, e2e=e2e, is_owner=args.owner)

    if args.say:
        c.connect()
        c.start_recv_only()  # 单次模式也要 recv,否则收不到 joined/广播
        time.sleep(1.0)
        if e2e is not None:
            if e2e.book.current_epoch(args.room) < 0:
                print("✗ E2E 启用但本房尚无房间钥(需先经房主分发/邀请带入),未发送", flush=True)
            else:
                c.post_seal(args.say)
        else:
            c.post(args.say)
        time.sleep(1.0)
        c.stop()
        return

    if args.history:
        c.connect()
        c.start_recv_only()
        time.sleep(1.0)
        c.read_history(0)
        time.sleep(1.5)
        c.stop()
        return

    c.listen(reconnect=True)  # --listen 或默认阻塞监听


if __name__ == "__main__":
    main()
