"""_test_groupchat_crossstack.py — SecureDM 群聊 E2E 跨端(suite=2)整合验收。

验证 Python 端新增的 suite=2 X25519 房间钥包裹与 Web 端 chatroom.html 参数
逐字节对齐(X25519 ECDH → HKDF-SHA256 salt=32×0x00 / info="securedm-roomkey-v1"
→ AES-256-GCM),实现全端互通。

覆盖:
  1. suite=2 单端 roundtrip(Python wrap_for_member_web ↔ unwrap_key_dist)
  2. 参数对齐断言:独立「模拟 Web」实现(同 cryptography 但按 Web 参数手写,
     不调用 GroupE2E)双向交叉解 → 证明两端参数逐字节对齐(不依赖浏览器)
  3. 跨端分发:3 客户端(alice owner/bob/carol)经 relay 真跑,逐人 suite=2 分发
     → 解出同钥 → post_seal(suite=2)→ 双向解出原文
  4. 跨端轮换:carol leave → alice 自动 rotate(suite=2)→ bob 升 epoch+1 →
     新密文 carol 旧钥解不开(前向保密)
  5. 混合房套件协商:alice/bob 公告 [1,2]、carol 公告 [2] → 交集=2
  6. 服务器侧断言:relay 落盘 state 的 history 无明文消息串、无 32B 房间钥明文,
     ctext/key-dist body 只含 b64 密文字段
  7. 明文回归:不带 E2E 的普通 ChatClient 同房发明文 → 正常收发

token/invite/房间钥真值不打印、不入日志(只打印「相等/不等」布尔)。

跑法:  python _test_groupchat_crossstack.py
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import chatroom_client  # noqa: E402
from chatroom_client import ChatClient, Identity  # noqa: E402
from securedm_groupchat_e2e import (  # noqa: E402
    GroupE2E, SUITE_RUST, SUITE_WEB_AESGCM)

# 模拟 Web 端的独立实现(与 GroupE2E 无共享代码,按 chatroom.html 参数手写)
from cryptography.hazmat.primitives.asymmetric.x25519 import (  # noqa: E402
    X25519PrivateKey, X25519PublicKey)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402

PORT = 18814
URL = f"ws://127.0.0.1:{PORT}"
TOKEN = "test-token-not-real"  # 仅测试用,非生产真值
ROOM = "cross-room"
PLAINTEXT_1 = "跨端互通·跨端你好"
PLAINTEXT_2 = "轮换后跨端秘密"

_results: list[tuple[str, bool, str]] = []


def report(case: str, ok: bool, note: str = "") -> None:
    _results.append((case, ok, note))
    print(f"[{'PASS' if ok else 'FAIL'}] {case}" + (f"  — {note}" if note else ""), flush=True)


def wait_for(pred, timeout: float = 8.0, interval: float = 0.05) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(interval)
    return False


def _port_open() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except Exception:
        return False


# ── 模拟 Web 端(chatroom.html GroupE2E)的独立实现 ──────────────────────────
# 与 securedm_groupchat_e2e.GroupE2E 无共享代码,参数按 Web 端手写:
#   ECDH deriveBits(256) → HKDF-SHA256(salt=32×0x00, info="securedm-roomkey-v1")
#   → AES-256-GCM,12B nonce。

def web_hkdf_wrap_key(shared: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32,
                salt=b"\x00" * 32,
                info="securedm-roomkey-v1".encode("utf-8")).derive(shared)


def web_wrap(their_pub32: bytes, key32: bytes) -> dict:
    """模拟 Web wrapRoomKey(theirPubB64,keyBytes)→ {eph,nonce,ct}。"""
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(X25519PublicKey.from_public_bytes(their_pub32))
    nonce = os.urandom(12)
    ct = AESGCM(web_hkdf_wrap_key(shared)).encrypt(nonce, key32, None)
    eph_raw = eph.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"eph": base64.b64encode(eph_raw).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ct).decode()}


def web_unwrap(my_priv: X25519PrivateKey, eph_b64: str, nonce_b64: str,
               ct_b64: str) -> bytes:
    """模拟 Web unwrapRoomKey(myKp,eph,nonce,ct)→ 32B 房间钥。"""
    eph_pub = X25519PublicKey.from_public_bytes(base64.b64decode(eph_b64))
    shared = my_priv.exchange(eph_pub)
    return AESGCM(web_hkdf_wrap_key(shared)).decrypt(
        base64.b64decode(nonce_b64), base64.b64decode(ct_b64), None)


class Member:
    """包一层 ChatClient:收 msg/event 进缓冲,便于断言。"""

    def __init__(self, name: str, owner: bool = False, suites: list[int] | None = None,
                 e2e: bool = True):
        self.name = name
        self.id = Identity.generate()
        self.e2e = GroupE2E() if e2e else None
        self.suites = suites  # None → 客户端默认 [1,2]
        self.msgs: list[dict] = []
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.cli = ChatClient(
            URL, ROOM, name, self.id,
            on_msg=self._on_msg, on_event=self._on_ev,
            e2e=self.e2e, is_owner=owner,
        )

    @property
    def uid(self) -> str:
        return self.id.user_id

    def _on_msg(self, m, verified):
        m = dict(m)
        m["_verified"] = verified
        with self.lock:
            self.msgs.append(m)

    def _on_ev(self, ev):
        with self.lock:
            self.events.append(ev)

    def connect_bg(self) -> None:
        if self.suites is not None:
            # 覆盖 join 里的套件声明(混合房协商用):patch connect 后改 join
            orig_send = self.cli._send
            def _send_first(obj):
                if obj.get("type") == "join":
                    obj["suites"] = self.suites
                    self.cli._send = orig_send  # 只改第一次(join)
                orig_send(obj)
            self.cli._send = _send_first
        self.cli.connect()
        self.cli.start_recv_only()

    def joined_event(self):
        with self.lock:
            for ev in self.events:
                if ev.get("type") == "joined":
                    return ev
        return None

    def ctexts(self):
        with self.lock:
            return [m for m in self.msgs if (m.get("body") or {}).get("kind") == "ctext"]

    def key_dists(self):
        with self.lock:
            return [m for m in self.msgs if (m.get("body") or {}).get("kind") == "key-dist"]

    def member_left_events(self):
        with self.lock:
            return [ev for ev in self.events if ev.get("type") == "member-left"]

    def decrypted_texts(self):
        return [m.get("decrypted") for m in self.ctexts() if m.get("decrypt_ok")]

    def has_decrypted(self, text: str) -> bool:
        return text in self.decrypted_texts()

    def has_plain(self, text: str) -> bool:
        with self.lock:
            return any((m.get("body") or {}).get("kind") == "text"
                       and (m.get("body") or {}).get("text") == text
                       for m in self.msgs)

    def stop(self):
        self.cli.stop()


def main() -> int:
    # ═══ 纯密码学层(不起 relay)═════════════════════════════════════════════

    # ── 1. suite=2 单端 roundtrip ─────────────────────────────────────────
    a = GroupE2E()
    b = GroupE2E()
    key = a.new_room_key()
    body = a.wrap_for_member_web(ROOM, 0, key, b.x25519_public_bytes)
    ok = (body.get("kind") == "key-dist" and body.get("suite") == SUITE_WEB_AESGCM
          and body.get("epoch") == 0 and body.get("room") == ROOM
          and set(body) >= {"eph", "nonce", "ct"})
    try:
        room2, epoch2, key2 = b.unwrap_key_dist(body)
        ok = ok and (room2 == ROOM and epoch2 == 0 and key2 == key)
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"    roundtrip err: {e}", flush=True)
    report("1 suite=2 单端 roundtrip(wrap_for_member_web→unwrap_key_dist 解回同钥)", ok)

    # ── 2. 参数对齐断言(模拟 Web ↔ Python 双向交叉)────────────────────────
    # 2a. 「Web wrap」→ Python unwrap:用独立 web_wrap 构造 body,Python 解
    c = GroupE2E()
    web_body = web_wrap(c.x25519_public_bytes, key)
    web_body.update({"kind": "key-dist", "suite": 2, "epoch": 3, "room": ROOM})
    ok2a = False
    try:
        r3, e3, k3 = c.unwrap_key_dist(web_body)
        ok2a = (r3 == ROOM and e3 == 3 and k3 == key)
    except Exception as e:  # noqa: BLE001
        print(f"    2a err: {e}", flush=True)
    # 2b. Python wrap → 「Web unwrap」:GroupE2E 产物用独立 web_unwrap 解
    d_priv = X25519PrivateKey.generate()
    d_pub = d_priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    py_body = a.wrap_for_member_web(ROOM, 5, key, d_pub)
    ok2b = False
    try:
        k4 = web_unwrap(d_priv, py_body["eph"], py_body["nonce"], py_body["ct"])
        ok2b = (k4 == key)
    except Exception as e:  # noqa: BLE001
        print(f"    2b err: {e}", flush=True)
    report("2 参数对齐:模拟Web wrap→Python 解 ✓ 且 Python wrap→模拟Web 解 ✓",
           ok2a and ok2b, f"web→py={ok2a} py→web={ok2b}")

    # ═══ relay 整合层 ══════════════════════════════════════════════════════
    state_fd, state_path = tempfile.mkstemp(prefix="chatroom_state_", suffix=".json")
    os.close(state_fd)
    os.unlink(state_path)
    fake_home = tempfile.mkdtemp(prefix="chatroom_home_")
    tok_dir = Path(fake_home) / ".local" / "share" / "aureon"
    tok_dir.mkdir(parents=True, exist_ok=True)
    tok_file = tok_dir / "l4_token"
    tok_file.write_text(TOKEN, encoding="utf-8")
    chatroom_client._TOKEN_FILE = tok_file

    env = dict(os.environ)
    env["L4_TOKEN"] = TOKEN
    env["CHATROOM_PORT"] = str(PORT)
    env["CHATROOM_STATE"] = state_path
    env["CHATROOM_HOST"] = "127.0.0.1"
    env["HOME"] = fake_home
    env["USERPROFILE"] = fake_home

    proc = subprocess.Popen(
        [sys.executable, str(HERE / "chatroom_relay.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    alice = bob = carol = dave = None
    try:
        if not wait_for(lambda: _port_open(), timeout=8):
            report("relay 启动", False, "端口未就绪")
            return 1
        report("0 relay 启动", True, f"ws://127.0.0.1:{PORT}")

        # ── 3. 跨端分发:3 客户端 suite=2 逐人分发 → 同钥 → 密文互解 ────────
        alice = Member("alice", owner=True)
        bob = Member("bob")
        carol = Member("carol")
        alice.connect_bg()
        if not wait_for(lambda: alice.joined_event() is not None):
            report("3 join", False, "alice 未收到 joined")
            return 1
        bob.connect_bg()
        carol.connect_bg()
        ok = wait_for(lambda: bob.joined_event() is not None
                      and carol.joined_event() is not None)
        ok = ok and wait_for(
            lambda: bob.uid in alice.cli.member_pubs and carol.uid in alice.cli.member_pubs)
        # 每个成员的公告应解出 x25519 + rust 双公钥(Python 端)
        pubs_ok = all(
            alice.cli.member_pubs.get(uid, {}).get("x25519")
            and alice.cli.member_pubs.get(uid, {}).get("rust")
            for uid in (bob.uid, carol.uid))
        if not pubs_ok:  # 诊断:不解真值,只报每个成员公告的形态
            diag = {uid[:6]: (sorted((alice.cli.member_pubs.get(uid) or {}).keys()),
                              {k: (v is not None) for k, v in (alice.cli.member_pubs.get(uid) or {}).items()})
                    for uid in (bob.uid, carol.uid)}
            print(f"    3a diag: {diag}", flush=True)
        report("3a 三方 joined + 公告 e2e_pub 含 x25519+rust 双公钥", ok and pubs_ok,
               f"pubs_ok={pubs_ok}")

        # alice new_room_key + 逐人 wrap_for_member_web(suite=2)分发
        key0 = alice.e2e.new_room_key()
        alice.e2e.book.set_key(ROOM, 0, key0)
        for uid in (bob.uid, carol.uid):
            bdy = alice.e2e.wrap_for_member_web(
                ROOM, 0, key0, alice.cli.member_pubs[uid]["x25519"])
            bdy["to_user_id"] = uid
            alice.cli._post_body(bdy, to_user_id=uid)
        ok = wait_for(lambda: bob.e2e.book.current_epoch(ROOM) == 0
                      and carol.e2e.book.current_epoch(ROOM) == 0)
        same_key = ok and (bob.e2e.book.get_key(ROOM, 0) == key0
                           and carol.e2e.book.get_key(ROOM, 0) == key0)
        # 收到的 key-dist 应全是 suite=2
        kd_suites = {int((m.get("body") or {}).get("suite", -1))
                     for m in (bob.key_dists() + carol.key_dists())}
        report("3b suite=2 逐人分发:bob/carol 解出同钥(只报相等布尔)", ok and same_key,
               f"kd_suites={sorted(kd_suites)} same_key={same_key}")

        alice.cli.post_seal(PLAINTEXT_1, suite=SUITE_WEB_AESGCM)
        ok = wait_for(lambda: bob.has_decrypted(PLAINTEXT_1)
                      and carol.has_decrypted(PLAINTEXT_1))
        # bob 也回发一条,验证双向
        bob.cli.post_seal("bob回alice·跨端", suite=SUITE_WEB_AESGCM)
        ok = ok and wait_for(lambda: alice.has_decrypted("bob回alice·跨端")
                             and carol.has_decrypted("bob回alice·跨端"))
        report("3c suite=2 密文互发互解(alice→b/c,bob→a/c)", ok)

        # ── 4. 跨端轮换:carol leave → alice 自动 rotate(suite=2)───────────
        carol.cli._send({"type": "leave", "room": ROOM, "user_id": carol.uid})
        ok = wait_for(lambda: bob.e2e.book.current_epoch(ROOM) == 1, timeout=10)
        alice_rot = wait_for(lambda: alice.e2e.book.current_epoch(ROOM) == 1)
        left_seen = wait_for(lambda: any(ev.get("user_id") == carol.uid
                                         for ev in alice.member_left_events()))
        # 新 key-dist 应仍是 suite=2(bob 有 x25519 公告)
        bob_kd2 = [m for m in bob.key_dists()
                   if int((m.get("body") or {}).get("epoch", -1)) == 1]
        kd2_suite = {int((m.get("body") or {}).get("suite", -1)) for m in bob_kd2}
        report("4a carol leave → owner 自动轮换 epoch1(suite=2),bob 升级",
               ok and alice_rot and left_seen and kd2_suite == {SUITE_WEB_AESGCM},
               f"bob_ep={bob.e2e.book.current_epoch(ROOM)} kd2_suite={sorted(kd2_suite)}")

        new_ct = alice.e2e.seal_msg(ROOM, PLAINTEXT_2, suite=SUITE_WEB_AESGCM)
        fs_ok = False
        try:
            carol.e2e.open_msg(new_ct)  # carol 只有 epoch0 → 必须失败
        except Exception:
            fs_ok = True
        bob_ok = False
        try:
            bob_ok = bob.e2e.open_msg(new_ct) == PLAINTEXT_2
        except Exception:
            pass
        report("4b 前向保密:carol 旧钥解不开新密文,bob 新钥能解", fs_ok and bob_ok,
               f"carol_失败={fs_ok} bob_解出={bob_ok}")

        # ── 5. 混合房套件协商:alice/bob=[1,2],carol2=[2] → 交集=2 ──────────
        # carol 已 leave;用新成员 carol2 只声明 [2] 加入同房验证交集计算
        carol2 = Member("carol2", suites=[2])
        carol2.connect_bg()
        ok = wait_for(lambda: carol2.joined_event() is not None)
        suite_sets = []
        for m in (alice, bob, carol2):
            for uid, mem in m.cli.members.items():
                if mem.get("suites"):
                    suite_sets.append(set(mem["suites"]))
        common = set.intersection(*suite_sets) if suite_sets else set()
        # 应见到至少一个 [2] 声明(carol2),且交集=2
        has_2only = any(s == {2} for s in suite_sets)
        report("5 混合房套件协商:存在仅[2]成员,全员交集={2}",
               ok and has_2only and common == {2},
               f"交集={sorted(common)} 仅2成员={has_2only}")
        carol2.stop()

        # ── 6. 服务器侧断言:state JSON 无明文/无房间钥明文 ─────────────────
        # 触发一次落盘
        alice.cli.post_seal("落盘触发·密文", suite=SUITE_WEB_AESGCM)
        wait_for(lambda: os.path.exists(state_path), timeout=5)
        time.sleep(0.3)  # 等 _save_state 写盘
        state_ok = False
        leak_note = ""
        try:
            raw = Path(state_path).read_text(encoding="utf-8")
            data = json.loads(raw)
            # 6a. 明文消息串绝不出现在服务器落盘里
            no_plain = (PLAINTEXT_1 not in raw and PLAINTEXT_2 not in raw
                        and "bob回alice·跨端" not in raw)
            # 6b. 房间钥 b64 绝不出现在落盘里(不打印真值,只断言找不到)
            key_b64 = base64.b64encode(key0).decode()
            no_key = key_b64 not in raw
            # 6c. history 里 ctext/key-dist body 只有密文字段
            fields_ok = True
            found_ct = found_kd = False
            for rid, rd in data.items():
                for rec in rd.get("history", []):
                    body = rec.get("body") or {}
                    k = body.get("kind")
                    if k == "ctext":
                        found_ct = True
                        if body.get("text") is not None or not body.get("ct"):
                            fields_ok = False
                    elif k == "key-dist":
                        found_kd = True
                        if body.get("text") is not None or not body.get("ct"):
                            fields_ok = False
                        # suite=2 key-dist 不应含 hs(suite=1 字段)
                        if int(body.get("suite", 0)) == 2 and "hs" in body:
                            fields_ok = False
            state_ok = no_plain and no_key and fields_ok and found_ct and found_kd
            leak_note = (f"无明文={no_plain} 无钥={no_key} 字段净={fields_ok} "
                         f"有ctext={found_ct} 有keydist={found_kd}")
        except Exception as e:  # noqa: BLE001
            leak_note = f"state 读取异常: {e}"
        report("6 服务器侧断言:落盘无明文消息串/无房间钥明文/密文字段纯净",
               state_ok, leak_note)

        # ── 7. 明文回归:不带 E2E 的旧客户端同房发明文 ──────────────────────
        dave = Member("dave", e2e=False)  # 普通 ChatClient,无 GroupE2E
        dave.connect_bg()
        ok = wait_for(lambda: dave.joined_event() is not None)
        dave.cli.post("dave明文大家好")
        ok = ok and wait_for(lambda: dave.has_plain("dave明文大家好"))
        # E2E 成员也能收到 dave 的明文(kind=text 直通 on_msg)
        ok = ok and wait_for(lambda: alice.has_plain("dave明文大家好"))
        # dave 收到 E2E 密文消息 → 不崩,body 原样(无 decrypted 字段)。
        # dave 无 e2e,ChatClient._handle 对 ctext 直通 on_msg 不解密(不崩即通过)。
        alice.cli.post_seal("给dave看的密文", suite=SUITE_WEB_AESGCM)
        dave_got_ctext = wait_for(lambda: any(
            (m.get("body") or {}).get("kind") == "ctext" for m in dave.msgs), timeout=5)
        dave_ok = True
        with dave.lock:
            for m in dave.msgs:
                if (m.get("body") or {}).get("kind") == "ctext":
                    if m.get("decrypted"):  # dave 无 e2e,不应有 decrypted
                        dave_ok = False
        if not (dave_got_ctext and dave_ok):
            kinds = {}
            with dave.lock:
                for m in dave.msgs:
                    k = (m.get("body") or {}).get("kind")
                    kinds[k] = kinds.get(k, 0) + 1
            print(f"    7 diag: dave 收到 body kinds={kinds} got_ctext={dave_got_ctext}",
                  flush=True)
        report("7 明文回归:无E2E客户端明文正常收发,E2E 密文对其不崩不误解",
               ok and dave_got_ctext and dave_ok,
               f"dave收发明文={ok} dave见密文不崩={dave_got_ctext and dave_ok}")

        failed = [c for c, ok, _ in _results if not ok]
        print(f"\n{'=' * 56}\n合计 {len(_results)} 项,PASS {len(_results) - len(failed)},FAIL {len(failed)}")
        if failed:
            print("FAIL 项:", "; ".join(failed))
        return 0 if not failed else 1
    finally:
        for m in (alice, bob, carol, dave):
            if m is not None:
                try:
                    m.stop()
                except Exception:
                    pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        for p in (state_path,):
            try:
                os.unlink(p)
            except Exception:
                pass
        try:
            tok_file.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
