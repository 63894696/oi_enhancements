"""forum_relay.py — Prisir 免注册论坛 VPS 中继(F-1b)

契约: docs/prisir-forum-protocol-2026-08-21.md(已定稿,canon 双向对拍 5/5 绿)
与 chatroom_relay.py 的本质差异:
  - chatroom_relay 是**群聊中继**:不验签、成员制、房间语义无内容寻址。
  - forum_relay 是**免注册论坛中继**:**验签通过才入历史/广播**,帖子内容寻址(post_id),
    「社会性到账」确认计数防冒名,PoW 抗垃圾,运营者签名撤下,作者签名自删,TTL 过期归档。
relay 红线:只存签名对象、永不接触私钥、验签失败一律 nack 不进历史。

部署: VPS 127.0.0.1:18812,nginx 反代 wss://bbs.babelspan.com/forum(主站域,CF 橙云+LE full)。
      本机盯梢镜像: 同脚本另实例 0.0.0.0:18813(仅 wg0,WG 内网 ws://10.66.66.1:18813),独立 state 不混生产。
环境变量:
  FORUM_HOST/FORUM_PORT     监听(默认 0.0.0.0:18812;生产应 127.0.0.1 由 nginx 终结 TLS)
  FORUM_STATE               落盘文件(默认 forum_state.json,原子写)
  POW_BITS                  PoW 难度(默认 18;测试用 8)
  OPERATOR_PUB              运营者 Ed25519 公钥 b64(takedown 验签;空=撤下功能关闭)
  GENESIS_PUBS              创世信任根公钥 b64,逗号分隔(其引用直接计满 3 票)
  POST_TTL_DAYS             帖子存活天数(默认 180,过期 read 不返回、归档不删)
  CONFIRM_THRESHOLD         确认阈值(默认 3)

多板块(F-2,2026-08-21):
  板块是 relay 白名单(BOARDS),不开放自由开版——防滥用、防主题稀释。
  两级树:域(domain)→ 板块(board)。域固定 3 个,板块随版本增减。
  welcome 帧下发 boards 目录(供客户端渲染导航);post 校验 board ∈ BOARDS;
  read 可选按 board 过滤(增量同步仍按全局 seq,不过滤板块,保证游标单调)。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import websockets
from websockets.asyncio.server import serve, ServerConnection

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

WS_HOST = os.environ.get("FORUM_HOST", "0.0.0.0")
WS_PORT = int(os.environ.get("FORUM_PORT", "18812"))
_STATE_FILE = Path(os.environ.get("FORUM_STATE", "forum_state.json"))
POW_BITS = int(os.environ.get("POW_BITS", "18"))
OPERATOR_PUB = os.environ.get("OPERATOR_PUB", "").strip()
GENESIS_PUBS = {s.strip() for s in os.environ.get("GENESIS_PUBS", "").split(",") if s.strip()}
POST_TTL_DAYS = int(os.environ.get("POST_TTL_DAYS", "180"))
CONFIRM_THRESHOLD = int(os.environ.get("CONFIRM_THRESHOLD", "3"))
# 镜像同步模式(只用于盯梢镜像实例):接受 relay 转发的已验签历史帖——
# 跳过 ts 时间窗与 PoW 重校(历史帖 ts 早已超窗、PoW 已由生产验过),仍强校验签名+board+fp。
# 生产实例绝不可开(=0),否则任何人都能发无 PoW 旧 ts 帖。
RELAY_SYNC_MODE = os.environ.get("FORUM_RELAY_SYNC", "0") == "1"

# 板块注册表(白名单)。域 → [(board_slug, 显示名, 一句话简介)]。
# 「域」固定三个,顶导航恒定;「板块」随产品/内容版本增减,长期死版归档降格。
BOARDS: dict[str, list[tuple[str, str, str]]] = {
    "browser": [  # 浏览器本体与各内建功能(每项开发一个反馈航道)
        ("translate",   "悬停翻译",   "custom-hover-translate 划词/悬停翻译"),
        ("search",      "搜索",       "搜索管道 / 多模型 / 引用"),
        ("network",     "网络链路",   "内嵌 sing-box 海外访问链路"),
        ("agent",       "智能体",     "M3 agent 操作层 / 连续代行"),
        ("findex",      "文件搜索",   "prisir_findex 本机文件搜索引擎"),
        ("ime",         "灵犀输入法", "拼音 / 五笔 / 语音"),
        ("shell",       "PrisirAI 对话", "PrisirAI 对话壳(本地名 prisiragent-shell)Electron 壳 / v2.0 装包器反馈落地"),
        ("forum",       "论坛本体",   "chrome://forum 页面与协议本身"),
    ],
    "babelspan": [  # 内容站,按书籍类型分版(随尺规分类扩展)
        ("literature",  "文学",       "文学类精评与书目讨论"),
        ("nonfiction",  "非虚构",     "社科/科普/传记等非虚构"),
        ("genre",       "类型小说",   "科幻/推理/奇幻等类型文学"),
        ("podcast",     "播客",       "音频栏目与衍生讨论"),
    ],
    "meta": [  # 站务与游乐场(低 stakes,学会新样式)
        ("lobby",       "门厅",       "新手第一帖 / 自我介绍 / 测试签名"),
        ("tea",         "茶馆",       "闲聊,验证「社会性到账」怎么摘帽"),
        ("meta",        "站务",       "论坛用法 / 规则 / 建议新板块"),
    ],
}
DEFAULT_BOARD = "lobby"  # 新人第一眼落地页
_ALL_BOARDS = {slug for boards in BOARDS.values() for slug, _, _ in boards}

MAX_BODY = 4000          # 纯文字帖上限(字符)
MAX_BODY_IMG = 96 * 1024 # 含内联图片帖上限(base64 data:image;客户端压 ≤64KB WebP 后约 87KB base64)
IMG_RE = __import__("re").compile(r"!\[[^\]]*\]\(data:image/(?:webp|jpeg|png);base64,([A-Za-z0-9+/=]+)\)")
TS_SKEW_SEC = 600
ALLOWED_KINDS = {"post", "reply", "retract"}
GENESIS_FPS: set[str] = set()  # 启动时从 GENESIS_PUBS 推导


# ── canon / 编码(与 JS group.html:158-167 + test_forum_canon.py 逐字节一致) ──
def canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def b64d(s: str) -> bytes:
    return base64.b64decode(s)


def b64url16(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")[:16]


def fp_of_pub(pub_b64: str) -> str:
    return b64url16(hashlib.sha256(b64d(pub_b64)).digest())


def check_pow(digest: bytes, bits: int) -> bool:
    n_full, rem = divmod(bits, 8)
    if digest[:n_full] != b"\x00" * n_full:
        return False
    if rem and (digest[n_full] >> (8 - rem)) != 0:
        return False
    return True


def _has_forbidden_chars(s: str) -> bool:
    return " " in s or " " in s  # U+2028/U+2029:JS/PY JSON 转义分歧源,协议禁止


# ── 帖子校验(契约 §4 顺序:形状→board/ts→fp→post_id→PoW→签名) ──
def validate_post(post: dict, posts_by_id: dict[str, dict]) -> tuple[str | None, dict]:
    """返回 (nack_reason | None, 派生字段 {post_id})。校验顺序见契约 §4。"""
    if not isinstance(post, dict):
        return "bad_field", {}
    top_keys = {"v", "kind", "board", "parent", "body", "author_pub", "author_fp", "ts", "pow", "sig"}
    if set(post.keys()) != top_keys:
        return "bad_field", {}
    if post["v"] != 1 or post["kind"] not in ALLOWED_KINDS or post["board"] not in _ALL_BOARDS:
        return "bad_board" if post.get("board") not in _ALL_BOARDS else "bad_field", {}
    body = post.get("body")
    if not isinstance(body, str) or _has_forbidden_chars(body):
        return "oversize", {}
    # 图片帖放宽上限:含 >=1 个合法 data:image 标记按 MAX_BODY_IMG,否则按纯文字 MAX_BODY
    limit = MAX_BODY_IMG if IMG_RE.search(body) else MAX_BODY
    if len(body) > limit:
        return "oversize", {}
    pow_ = post.get("pow")
    if not isinstance(pow_, dict) or pow_.get("alg") != "sha256-b64" \
            or not isinstance(pow_.get("bits"), int) or not isinstance(pow_.get("nonce"), int) \
            or pow_["nonce"] < 0 or pow_["nonce"] >= 2**53:
        return "bad_pow", {}
    # 同步模式跳过 PoW 难度与 ts 时间窗(历史帖已由生产验过);否则按新帖标准
    if not RELAY_SYNC_MODE:
        if pow_["bits"] < POW_BITS:
            return "bad_pow", {}
        try:
            ts = datetime.fromisoformat(str(post["ts"]).replace("Z", "+00:00"))
            if abs(time.time() - ts.timestamp()) > TS_SKEW_SEC:
                return "bad_ts", {}
        except (ValueError, TypeError):
            return "bad_ts", {}
    if post["kind"] in ("reply", "retract"):
        parent = post.get("parent")
        if not isinstance(parent, str) or parent not in posts_by_id:
            return "bad_parent", {}
        if post["kind"] == "retract":  # 自删只能删自己的帖
            if posts_by_id[parent]["post"]["author_fp"] != post["author_fp"]:
                return "bad_parent", {}
    elif post.get("parent") is not None:
        return "bad_field", {}
    try:
        pub_raw = b64d(post["author_pub"])
        if len(pub_raw) != 32:
            return "bad_field", {}
        if fp_of_pub(post["author_pub"]) != post["author_fp"]:
            return "bad_fp", {}
        sig = b64d(post["sig"])
        if len(sig) != 64:
            return "bad_sig", {}
    except Exception:
        return "bad_field", {}
    signed_view = {k: v for k, v in post.items() if k != "sig"}
    # PoW:sha256(canon(无 sig)) 前 bits 位为 0(同步模式跳过——历史帖 PoW 已由生产验过)
    if not RELAY_SYNC_MODE and \
            not check_pow(hashlib.sha256(canon(signed_view).encode("utf-8")).digest(), pow_["bits"]):
        return "bad_pow", {}
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(sig, canon(signed_view).encode("utf-8"))
    except InvalidSignature:
        return "bad_sig", {}
    post_id = b64url16(hashlib.sha256(canon(post).encode("utf-8")).digest())
    return None, {"post_id": post_id}


def verify_operator_sig(post_id: str, reason: str, ts: str, sig_b64: str) -> bool:
    if not OPERATOR_PUB:
        return False
    try:
        payload = canon({"post_id": post_id, "reason": reason, "ts": ts}).encode("utf-8")
        Ed25519PublicKey.from_public_bytes(b64d(OPERATOR_PUB)).verify(b64d(sig_b64), payload)
        return True
    except Exception:
        return False


# ── 令牌桶限流(每连接) ──
class RateLimiter:
    def __init__(self) -> None:
        self.tokens = 20.0          # 容量 20(≈20帖/10min 的突发)
        self.refill_per_sec = 20 / 600
        self.last = time.monotonic()
        self.last_post = 0.0        # 1帖/2s 硬间隔

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(20.0, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now
        if now - self.last_post < 2.0 or self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        self.last_post = now
        return True


# ── 论坛状态(全量签名对象 + 确认计数) ──
class Forum:
    def __init__(self) -> None:
        self.posts: dict[str, dict] = {}   # post_id → {post, seq, confirmations, confirmed, taken_down, retracted}
        self.seq = 0
        self.references: dict[str, set[str]] = {}  # 被引用 post_id → 已确认引用者 fp 集
        self._load()

    def _expired(self, rec: dict) -> bool:
        try:
            ts = datetime.fromisoformat(rec["post"]["ts"].replace("Z", "+00:00")).timestamp()
            return time.time() - ts > POST_TTL_DAYS * 86400
        except Exception:
            return False

    def add(self, post: dict, post_id: str) -> dict:
        self.seq += 1
        rec = {"post": post, "seq": self.seq, "confirmations": 0,
               "confirmed": post["author_fp"] in GENESIS_FPS,
               "taken_down": False, "retracted": False}
        self.posts[post_id] = rec
        bumped = []
        parent = post.get("parent")
        if post["kind"] == "reply" and parent and parent in self.posts:
            prec = self.posts[parent]
            if not prec["taken_down"] and not prec["retracted"]:
                refs = self.references.setdefault(parent, set())
                # 创世根引用直接计满;已确认身份引用计 1 票(同 fp 去重)
                if rec["confirmed"]:
                    refs.add(post["author_fp"])
                    if post["author_fp"] in GENESIS_FPS:
                        new_conf = max(CONFIRM_THRESHOLD, len(refs))
                    else:
                        new_conf = len(refs)
                    if new_conf != prec["confirmations"]:
                        prec["confirmations"] = new_conf
                        if not prec["confirmed"] and new_conf >= CONFIRM_THRESHOLD:
                            prec["confirmed"] = True
                        bumped.append(parent)
        self._save()
        return {"rec": rec, "bumped": bumped}

    def takedown(self, post_id: str) -> bool:
        rec = self.posts.get(post_id)
        if not rec or rec["taken_down"]:
            return False
        rec["taken_down"] = True
        self._save()
        return True

    def retract(self, post_id: str) -> bool:
        rec = self.posts.get(post_id)
        if not rec or rec["retracted"] or rec["taken_down"]:
            return False
        rec["retracted"] = True
        self._save()
        return True

    def history(self, since_seq: int, include_takedown: bool = False,
                board: str | None = None) -> tuple[list[dict], int]:
        out = []
        for pid, rec in sorted(self.posts.items(), key=lambda kv: kv[1]["seq"]):
            if rec["seq"] <= since_seq or self._expired(rec):
                continue
            if (rec["taken_down"] or rec["retracted"]) and not include_takedown:
                continue
            if board is not None and rec["post"].get("board") != board:
                continue
            out.append({"post": rec["post"], "post_id": pid, "seq": rec["seq"],
                        "confirmations": rec["confirmations"], "confirmed": rec["confirmed"],
                        "taken_down": rec["taken_down"], "retracted": rec["retracted"]})
        return out, self.seq

    def _save(self) -> None:
        tmp = _STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"seq": self.seq, "posts": self.posts}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(_STATE_FILE)

    def _load(self) -> None:
        global GENESIS_FPS
        GENESIS_FPS = {fp_of_pub(p) for p in GENESIS_PUBS}
        if not _STATE_FILE.exists():
            return
        try:
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            self.seq = data.get("seq", 0)
            self.posts = data.get("posts", {})
            # 重建引用图(只收录「已确认身份」的回复票,与 add() 口径一致)
            for pid, rec in self.posts.items():
                p = rec["post"]
                if p.get("kind") == "reply" and p.get("parent") in self.posts and rec["confirmed"] \
                        and not rec["taken_down"] and not rec["retracted"]:
                    self.references.setdefault(p["parent"], set()).add(p["author_fp"])
        except Exception as e:
            print(f"[forum] state 载入失败,从空启动: {e}")


FORUM = Forum()
CONNS: set[ServerConnection] = set()
IP_CONNS: dict[str, int] = {}
MAX_CONN_PER_IP = 8


async def _send(conn: ServerConnection, msg: dict, skip_types: frozenset[str] = frozenset()) -> bool:
    try:
        await conn.send(json.dumps(msg, ensure_ascii=False))
        return True
    except Exception:
        return False


async def _broadcast(msg: dict) -> None:
    dead = []
    for c in list(CONNS):
        if not await _send(c, msg):
            dead.append(c)
    for c in dead:
        CONNS.discard(c)


async def _handle(ws: ServerConnection) -> None:
    ip = (ws.remote_address[0] if ws.remote_address else "?")
    if IP_CONNS.get(ip, 0) >= MAX_CONN_PER_IP:
        await ws.close(1013, "too many connections")
        return
    IP_CONNS[ip] = IP_CONNS.get(ip, 0) + 1
    CONNS.add(ws)
    rl = RateLimiter()
    rl.last_post = time.monotonic() - 2.0  # 新连接首帖不受 2s 硬间隔(此前无发帖行为)
    try:
        async for raw in ws:
            try:
                m = json.loads(raw)
            except Exception:
                continue
            try:
                await _dispatch(ws, m, m.get("type"), rl)
            except Exception:
                traceback.print_exc()
    finally:
        CONNS.discard(ws)
        IP_CONNS[ip] = max(0, IP_CONNS.get(ip, 1) - 1)


async def _dispatch(ws: ServerConnection, m: dict, t, rl: RateLimiter) -> None:
    if t == "hello":
        await _send(ws, {"type": "welcome", "pow_bits": POW_BITS, "board": DEFAULT_BOARD,
                         "boards": {d: [[s, n, desc] for s, n, desc in bl]
                                    for d, bl in BOARDS.items()},
                         "operator_fp": fp_of_pub(OPERATOR_PUB) if OPERATOR_PUB else None,
                         "genesis_fps": sorted(GENESIS_FPS), "last_seq": FORUM.seq})
        return
    if t == "ping":
        await _send(ws, {"type": "pong"})
        return
    if t == "read":
        msgs, last = FORUM.history(int(m.get("since_seq", 0)),
                                   bool(m.get("include_takedown", False)),
                                   m.get("board"))
        await _send(ws, {"type": "history", "msgs": msgs, "last_seq": last})
        return
    if t == "takedown":
        pid, reason, ts = str(m.get("post_id", "")), str(m.get("reason", "")), str(m.get("ts", ""))
        if verify_operator_sig(pid, reason, ts, str(m.get("operator_sig", ""))) and FORUM.takedown(pid):
            await _broadcast({"type": "takedown", "post_id": pid, "reason": reason,
                              "operator_sig": m.get("operator_sig"), "ts": ts})
        else:
            await _send(ws, {"type": "nack", "reason": "bad_operator_sig"})
        return
    if t != "post":
        return
    post = m.get("post")
    if not rl.allow():
        await _send(ws, {"type": "nack", "reason": "rate_limited"})
        return
    reason, derived = (validate_post(post, FORUM.posts)
                       if isinstance(post, dict) else ("bad_field", {}))
    if reason:
        await _send(ws, {"type": "nack", "reason": reason})
        return
    post_id = derived["post_id"]
    if post_id in FORUM.posts:
        await _send(ws, {"type": "nack", "reason": "duplicate"})
        return
    if post["kind"] == "retract":
        FORUM.retract(post["parent"])
        await _broadcast({"type": "retract", "post_id": post["parent"], "ts": post["ts"]})
        await _send(ws, {"type": "ack", "post_id": post_id, "seq": FORUM.seq,
                         "confirmations": 0, "confirmed": True})
        return
    res = FORUM.add(post, post_id)
    rec = res["rec"]
    frame = {"type": "post", "post": post, "post_id": post_id, "seq": rec["seq"],
             "confirmations": rec["confirmations"], "confirmed": rec["confirmed"]}
    await _broadcast(frame)
    await _send(ws, {"type": "ack", "post_id": post_id, "seq": rec["seq"],
                     "confirmations": rec["confirmations"], "confirmed": rec["confirmed"]})
    for pid in res["bumped"]:
        prec = FORUM.posts[pid]
        await _broadcast({"type": "confirm-update", "post_id": pid,
                          "confirmations": prec["confirmations"],
                          "confirmed": prec["confirmed"]})


async def main() -> None:
    print(f"[forum] relay @ {WS_HOST}:{WS_PORT} pow_bits={POW_BITS} genesis={len(GENESIS_FPS)} "
          f"operator={'set' if OPERATOR_PUB else 'OFF'} ttl={POST_TTL_DAYS}d "
          f"state={_STATE_FILE} posts={len(FORUM.posts)}")
    async with serve(_handle, WS_HOST, WS_PORT, max_size=64 * 1024):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
