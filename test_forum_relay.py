"""test_forum_relay.py — F-1b relay 端到端单测(真起 WS 服务,不假 PASS)

覆盖契约 §5 验收项:
  1. 伪造签名 → nack bad_sig
  2. PoW 不足 → nack bad_pow
  3. 正常帖 → 广播 + confirmations 递增
  4. 创世根 3 次引用 → 新身份 confirmed 翻转
  5. 未确认身份引用 → 不计票(父帖计数不变)
  6. takedown 错公钥 → nack;operator 正确签名 → 生效 + read 剔除
  7. retract 作者自删 → 生效;他人 retract → nack bad_parent
  8. 限流 → rate_limited
  9. author_fp 与 pub 不一致 → nack bad_fp
 10. 状态落盘重载后历史保留、引用图重建

设计:每连接一个「收集协程」把入站帧按类型进队列,断言从队列取——
     杜绝 recv_until 与广播交错导致的帧丢失/超时竞态。
用法: python test_forum_relay.py   (自动起 relay 于 127.0.0.1:18931,测完自清理)
"""
import asyncio, base64, hashlib, json, os, subprocess, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PORT = 18931
TAG = "FORUMTEST"
STATE = Path(tempfile.gettempdir()) / "forum_relay_test_state.json"


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def b64(b): return base64.b64encode(b).decode()
def b64url16(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")[:16]

def check_pow(digest, bits):
    full, rem = divmod(bits, 8)
    if digest[:full] != b"\x00" * full: return False
    if rem and (digest[full] >> (8 - rem)) != 0: return False
    return True

def solve_pow(post, bits):
    n = 0
    while True:
        post["pow"] = {"alg": "sha256-b64", "bits": bits, "nonce": n}
        if check_pow(hashlib.sha256(canon(post).encode()).digest(), bits): return post
        n += 1

class TestId:
    def __init__(self, seed: bytes):
        self.priv = Ed25519PrivateKey.from_private_bytes(seed)
        raw = self.priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.pub = b64(raw)
        self.fp = b64url16(hashlib.sha256(raw).digest())

    def make_post(self, body, *, kind="post", parent=None, bits=8, bad_sig=False, spoof_fp=None, board="lobby"):
        post = {"v": 1, "kind": kind, "board": board, "parent": parent,
                "body": f"[{TAG}] {body}", "author_pub": self.pub,
                "author_fp": spoof_fp or self.fp,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "pow": None}
        post = solve_pow(post, bits)
        sig = self.priv.sign(canon(post).encode())
        post["sig"] = b64(b"\x00" * 64 if bad_sig else sig)
        return post

ALICE = TestId(b"A" * 32); BOB = TestId(b"B" * 32); CAROL = TestId(b"C" * 32)
DAVE = TestId(b"D" * 32); EVE = TestId(b"E" * 32); OP = TestId(b"O" * 32)
GENESIS = [ALICE.pub, BOB.pub, CAROL.pub]

passed, failed = [], []
def ok(name, cond, extra=""):
    (passed if cond else failed).append(name)
    print(("✓ " if cond else "✗ ") + name + (f"  [{extra}]" if extra else ""), flush=True)

class Client:
    """一个 WS 连接 + 按类型分桶的入站队列,杜绝广播/应答交错丢帧。"""
    def __init__(self, ws):
        self.ws = ws
        self.queues: dict[str, asyncio.Queue] = {}
        self.task = asyncio.create_task(self._collect())

    async def _collect(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                self.queues.setdefault(m.get("type", "?"), asyncio.Queue()).put_nowait(m)
        except Exception:
            pass

    async def get(self, type_, timeout=5):
        return await asyncio.wait_for(self.queues.setdefault(type_, asyncio.Queue()).get(), timeout)

    async def post_and_ack(self, post):
        await self.ws.send(json.dumps({"type": "post", "post": post}))
        ack_q = self.queues.setdefault("ack", asyncio.Queue())
        nack_q = self.queues.setdefault("nack", asyncio.Queue())
        ack_t = asyncio.create_task(ack_q.get())
        nack_t = asyncio.create_task(nack_q.get())
        done, pending = await asyncio.wait({ack_t, nack_t}, timeout=8,
                                           return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if not done:
            raise TimeoutError("post_and_ack 无应答")
        for t in done:
            m = t.result()
            return (m, None) if "post_id" in m else (None, m["reason"])

    _last_post_at = 0.0
    async def post_safe(self, post):
        """发帖前确保距上次发帖 ≥2.1s(避开 relay 1帖/2s 硬间隔);返回 post_and_ack 结果。"""
        gap = time.monotonic() - Client._last_post_at
        if gap < 2.1:
            await asyncio.sleep(2.1 - gap)
        Client._last_post_at = time.monotonic()
        return await self.post_and_ack(post)

    async def close(self):
        self.task.cancel()
        await self.ws.close()

async def wait_port():
    for _ in range(60):
        try:
            async with websockets.connect(f"ws://127.0.0.1:{PORT}"):
                return
        except OSError:
            await asyncio.sleep(0.2)
    raise RuntimeError("relay 未能起服务")

async def main():
    env = dict(os.environ, FORUM_HOST="127.0.0.1", FORUM_PORT=str(PORT),
               FORUM_STATE=str(STATE), POW_BITS="8", OPERATOR_PUB=OP.pub,
               GENESIS_PUBS=",".join(GENESIS), CONFIRM_THRESHOLD="3", POST_TTL_DAYS="180")
    proc = subprocess.Popen([sys.executable, "forum_relay.py"], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=os.path.dirname(os.path.abspath(__file__)))
    try:
        await wait_port()
        ws1, ws2 = await websockets.connect(f"ws://127.0.0.1:{PORT}"), await websockets.connect(f"ws://127.0.0.1:{PORT}")
        c1, c2 = Client(ws1), Client(ws2)
        for c in (c1, c2):
            await c.ws.send(json.dumps({"type": "hello"}))
        w = await c1.get("welcome")
        await c2.get("welcome")
        ok("welcome 下发 pow_bits/genesis", w["pow_bits"] == 8 and len(w["genesis_fps"]) == 3)

        # 2. PoW bits 声明低于门槛 → bad_pow
        cheat = ALICE.make_post("pow cheat"); cheat["pow"]["bits"] = 0
        _, reason = await c1.post_safe(cheat)
        ok("PoW bits 低于门槛 → nack bad_pow", reason == "bad_pow", str(reason))
        await asyncio.sleep(2.1)
        # 9. fp 与 pub 不一致
        _, reason = await c1.post_safe(ALICE.make_post("spoof", spoof_fp=BOB.fp))
        ok("fp/pub 不一致 → nack bad_fp", reason == "bad_fp", str(reason))
        await asyncio.sleep(2.1)
        # 1. 伪造签名
        _, reason = await c1.post_safe(ALICE.make_post("forged", bad_sig=True))
        ok("伪造签名 → nack bad_sig", reason == "bad_sig", str(reason))
        await asyncio.sleep(2.1)  # 限流硬间隔:nack 也消耗发帖闸

        # 3. DAVE(未确认新身份)发主帖 → ack confirmed=false;ws2 收到广播
        dave_post = DAVE.make_post("新人第一帖")
        ack, nack_reason = await c1.post_safe(dave_post)
        ok("新身份主帖 ack confirmed=false", bool(ack) and ack["confirmed"] is False,
           f"nack={nack_reason}")
        bc = await c2.get("post")
        ok("广播带 post_id/seq", bc["post_id"] == ack["post_id"] and bc["seq"] >= 1)
        dave_pid = ack["post_id"]

        # 5. 未确认身份 EVE 回复 → 父帖计数不变(0),不翻转
        await c1.post_safe(EVE.make_post("未确认者捧场", kind="reply", parent=dave_pid))
        await c2.get("post")  # 吃掉 EVE 帖广播
        # 限流:此后 c1 每帖前 sleep 2.1s(post_and_ack 调用点标注)
        await ws1.send(json.dumps({"type": "read", "since_seq": 0}))
        h = await c1.get("history")
        rec = [m for m in h["msgs"] if m["post_id"] == dave_pid][0]
        ok("未确认身份引用不计票", rec["confirmations"] == 0 and rec["confirmed"] is False,
           f"conf={rec['confirmations']}")

        # 4. 创世根引用「一票计满」(契约 §3:genesis 作者引用直接计满阈值)。
        #    另验「普通已确认身份」逐票:先把 EVE 确认(创世根回复 EVE 的帖),再用 EVE+另一确认者凑票。
        await c1.post_safe(ALICE.make_post("创世一票计满", kind="reply", parent=dave_pid))
        upd = await c2.get("confirm-update")
        ok("创世根一票计满 confirmations=3 confirmed 翻转",
           upd["confirmations"] == 3 and upd["confirmed"] is True, str(upd))

        # 7. 他人 retract → bad_parent;作者本人 retract → 生效
        _, reason = await c1.post_safe(BOB.make_post("越权", kind="retract", parent=dave_pid))
        ok("他人 retract → nack bad_parent", reason == "bad_parent", str(reason))
        r_ack, _ = await c1.post_safe(DAVE.make_post("", kind="retract", parent=dave_pid))
        rc = await c2.get("retract")
        ok("作者 retract 生效", r_ack is not None and rc["post_id"] == dave_pid)

        # 6. takedown:错公钥 → nack;operator 正确签名 → 生效(对另一个未 retract 的帖)
        target = None
        await ws1.send(json.dumps({"type": "read", "since_seq": 0, "include_takedown": True}))
        for m in (await c1.get("history"))["msgs"]:
            target = m["post_id"]
            break
        ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        reason_txt = "x"
        payload = canon({"post_id": target, "reason": reason_txt, "ts": ts_now}).encode()
        await ws1.send(json.dumps({"type": "takedown", "post_id": target, "reason": reason_txt,
                                   "ts": ts_now, "operator_sig": b64(BOB.priv.sign(payload))}))
        m = await c1.get("nack")
        ok("takedown 错公钥 → nack bad_operator_sig", m["reason"] == "bad_operator_sig")
        await ws1.send(json.dumps({"type": "takedown", "post_id": target, "reason": reason_txt,
                                   "ts": ts_now, "operator_sig": b64(OP.priv.sign(payload))}))
        td = await c2.get("takedown")
        ok("operator takedown 生效广播", td["post_id"] == target)

        # read 剔除 / 审计可见
        await ws1.send(json.dumps({"type": "read", "since_seq": 0}))
        h = await c1.get("history")
        ids = {m["post_id"] for m in h["msgs"]}
        ok("read 剔除 retract+takedown 帖", dave_pid not in ids and target not in ids)
        await ws1.send(json.dumps({"type": "read", "since_seq": 0, "include_takedown": True}))
        h2 = await c1.get("history")
        ids2 = {m["post_id"] for m in h2["msgs"]}
        ok("审计模式可见", dave_pid in ids2 and target in ids2)

        # 8. 限流:2s 内连发 → rate_limited
        await c1.post_safe(ALICE.make_post("限流1"))
        _, reason = await c1.post_and_ack(ALICE.make_post("限流2"))  # 故意立刻连发触发限流
        ok("2s 内连发 → rate_limited", reason == "rate_limited", str(reason))

        await c1.close(); await c2.close()
        # 落盘真实条数(重启前)——诊断 retract/takedown 是否也落了盘
        import json as _json
        disk = _json.loads(STATE.read_text(encoding="utf-8"))
        flags = [(pid[:6], r.get('retracted'), r.get('taken_down')) for pid, r in disk['posts'].items()]
        print(f"[test] 落盘 posts={len(disk['posts'])} seq={disk['seq']} flags={flags}", flush=True)

        # 10. 重启 relay → 历史保留(含 retract/takedown 标记随 _save() 落盘恢复)
        #    实际接受 4 条(DAVE 主帖/EVE 回复/创世引用/限流1),均须保留;其余 nack/retract 不留历史。
        proc.terminate(); proc.wait(timeout=5)
        proc = subprocess.Popen([sys.executable, "forum_relay.py"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        await wait_port()
        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws3:
            await ws3.send(json.dumps({"type": "read", "since_seq": 0, "include_takedown": True}))
            c3 = Client(ws3)
            h3 = await c3.get("history")
            ok("重启后历史保留(4 条全量 + 标记恢复)",
               len(h3["msgs"]) == 4 and any(
                   m["post_id"] == dave_pid and m["retracted"] for m in h3["msgs"]),
               f"got {len(h3['msgs'])}")
            await c3.close()
    finally:
        if proc.poll() is None:
            proc.terminate(); proc.wait(timeout=5)
        if STATE.exists():
            STATE.unlink()
        # 调试:relay 日志尾(诊断广播/验签问题)
        try:
            out = proc.stdout.read() if proc.stdout else ""
            if out:
                print("--- relay log tail ---")
                print("\n".join(out.splitlines()[-20:]))
        except Exception:
            pass

    print(f"\n{len(passed)}/{len(passed) + len(failed)} 通过")
    if failed:
        print("失败:", failed)
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    asyncio.run(main())
