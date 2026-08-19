"""_test_groupchat_rotation.py — 群聊 E2E:joined owner 字段/e2e_pub 公告/逐人分发/
密文收发/成员离开自动轮换/前向保密/旧钥解历史/套件协商 端到端验证。

起 chatroom_relay 子进程(端口 18813,独立临时 state,隔离 HOME 让 L4_TOKEN 生效),
用 chatroom_client.ChatClient + GroupE2E 起 alice(owner)/bob/carol 三客户端真跑。

token/invite/房间钥真值不打印、不入日志。

跑法:  python _test_groupchat_rotation.py
"""
from __future__ import annotations

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
from securedm_groupchat_e2e import GroupE2E, SUITE_WEB_AESGCM  # noqa: E402

PORT = 18813
URL = f"ws://127.0.0.1:{PORT}"
TOKEN = "test-token-not-real"  # 仅测试用,非生产真值
ROOM = "rot-room"

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


class Member:
    """包一层 ChatClient:收 msg/event 进缓冲,便于断言。"""

    def __init__(self, name: str, owner: bool = False):
        self.name = name
        self.id = Identity.generate()
        self.e2e = GroupE2E()
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

    def stop(self):
        self.cli.stop()


def main() -> int:
    state_fd, state_path = tempfile.mkstemp(prefix="chatroom_state_", suffix=".json")
    os.close(state_fd)
    os.unlink(state_path)
    # 隔离 HOME:relay 与 client 的 _token() 都优先读真实 ~/.local/share/aureon/l4_token,
    # 必须把 HOME 指到放了测试 token 的假家目录,两端才能对上。
    fake_home = tempfile.mkdtemp(prefix="chatroom_home_")
    tok_dir = Path(fake_home) / ".local" / "share" / "aureon"
    tok_dir.mkdir(parents=True, exist_ok=True)
    tok_file = tok_dir / "l4_token"
    tok_file.write_text(TOKEN, encoding="utf-8")
    # chatroom_client._TOKEN_FILE 是 import 期按真实 HOME 算好的模块常量,
    # 之后改 HOME 环境变量不影响它 → 直接把模块常量指到测试 token 文件。
    chatroom_client._TOKEN_FILE = tok_file

    env = dict(os.environ)
    env["L4_TOKEN"] = TOKEN
    env["CHATROOM_PORT"] = str(PORT)
    env["CHATROOM_STATE"] = state_path
    env["CHATROOM_HOST"] = "127.0.0.1"
    env["HOME"] = fake_home        # relay 子进程 import 时按此 HOME 定位 token
    env["USERPROFILE"] = fake_home

    proc = subprocess.Popen(
        [sys.executable, str(HERE / "chatroom_relay.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    alice = bob = carol = None
    try:
        up = wait_for(lambda: _port_open(), timeout=8)
        if not up:
            report("relay 启动", False, "端口未就绪")
            return 1
        report("relay 启动", True, f"ws://127.0.0.1:{PORT}")

        # ── 1. 三客户端启用 E2E 加入同房 ─────────────────────────────────
        alice = Member("alice", owner=True)
        bob = Member("bob")
        carol = Member("carol")
        alice.connect_bg()
        if not wait_for(lambda: alice.joined_event() is not None):
            with alice.lock:
                evs = [(e.get("type"), e.get("error", "")) for e in alice.events]
            report("1 join", False, f"alice 未收到 joined; events={evs}")
            return 1
        bob.connect_bg()
        carol.connect_bg()
        ok = wait_for(lambda: bob.joined_event() is not None
                      and carol.joined_event() is not None)
        ok = ok and wait_for(
            lambda: bob.uid in alice.cli.member_pubs and carol.uid in alice.cli.member_pubs)
        report("1a 三方 joined", ok)

        ja = alice.joined_event()
        owner_ok = ja is not None and ja.get("owner") == alice.uid
        pubs_ok = (
            bob.uid in alice.cli.member_pubs and carol.uid in alice.cli.member_pubs
            and alice.uid in bob.cli.member_pubs and carol.uid in bob.cli.member_pubs
            and alice.uid in carol.cli.member_pubs and bob.uid in carol.cli.member_pubs
        )
        report("1b joined.owner==alice + 三方互收 e2e_pub", owner_ok and pubs_ok,
               f"owner_ok={owner_ok} pubs_ok={pubs_ok}")

        # ── 2. alice 分发 epoch0 房间钥(逐人定向)────────────────────────
        key0 = alice.e2e.new_room_key()
        alice.e2e.book.set_key(ROOM, 0, key0)
        for uid in (bob.uid, carol.uid):
            body = alice.e2e.wrap_for_member(ROOM, 0, key0, alice.cli.member_pubs[uid])
            body["to_user_id"] = uid
            alice.cli._post_body(body, to_user_id=uid)
        ok = wait_for(lambda: bob.e2e.book.current_epoch(ROOM) == 0
                      and carol.e2e.book.current_epoch(ROOM) == 0)
        ep_ok = (alice.e2e.book.current_epoch(ROOM) == 0
                 and bob.e2e.book.current_epoch(ROOM) == 0
                 and carol.e2e.book.current_epoch(ROOM) == 0)
        same_key = ok and (
            bob.e2e.book.get_key(ROOM, 0) == key0 and carol.e2e.book.get_key(ROOM, 0) == key0)
        report("2 逐人分发 epoch0,三方 epoch 一致且钥相同", ok and ep_ok and same_key,
               f"epochs a/b/c = {alice.e2e.book.current_epoch(ROOM)}/"
               f"{bob.e2e.book.current_epoch(ROOM)}/{carol.e2e.book.current_epoch(ROOM)}")

        # ── 3. 密文收发(轮换前)─────────────────────────────────────────
        alice.cli.post_seal("轮换前你好", suite=SUITE_WEB_AESGCM)
        ok = wait_for(lambda: bob.has_decrypted("轮换前你好")
                      and carol.has_decrypted("轮换前你好"))
        report("3 alice post_seal → bob/carol 解出原文", ok,
               f"bob={bob.has_decrypted('轮换前你好')} carol={carol.has_decrypted('轮换前你好')}")

        # ── 4. carol leave → alice 自动轮换 → bob 升 epoch1;前向保密 ─────
        carol.cli._send({"type": "leave", "room": ROOM, "user_id": carol.uid})
        # bob 应收到 epoch1 的 key-dist 并登记
        ok = wait_for(lambda: bob.e2e.book.current_epoch(ROOM) == 1, timeout=10)
        alice_rotated = wait_for(lambda: alice.e2e.book.current_epoch(ROOM) == 1)
        left_seen = wait_for(lambda: any(ev.get("user_id") == carol.uid
                                         for ev in alice.member_left_events()))
        report("4a carol leave → owner 收 member-left 并自动轮换到 epoch1",
               ok and alice_rotated and left_seen,
               f"bob_ep={bob.e2e.book.current_epoch(ROOM)} "
               f"alice_ep={alice.e2e.book.current_epoch(ROOM)} left_seen={left_seen}")

        # bob 新 epoch 下 seal/open 通
        roundtrip = False
        if ok:
            try:
                ct = bob.e2e.seal_msg(ROOM, "轮换后bob说", suite=SUITE_WEB_AESGCM)
                roundtrip = (alice.e2e.open_msg(ct) == "轮换后bob说"
                             and ct.get("epoch") == 1)
            except Exception:
                roundtrip = False
        # carol 用旧 epoch0 钥解轮换后的新密文 → 必须失败(前向保密)
        fs_ok = False
        try:
            new_ct = alice.e2e.seal_msg(ROOM, "轮换后秘密", suite=SUITE_WEB_AESGCM)
            carol.e2e.open_msg(new_ct)  # carol 只有 epoch0,body.epoch=1 → KeyError
            fs_ok = False
        except Exception:
            fs_ok = True
        # bob 用旧 epoch0 钥仍能解轮换前的历史消息
        hist_ok = False
        pre_ct = None
        for m in bob.ctexts():
            if (m.get("body") or {}).get("epoch") == 0:
                pre_ct = m["body"]
                break
        if pre_ct is not None:
            try:
                hist_ok = bob.e2e.open_msg(pre_ct) == "轮换前你好"
            except Exception:
                hist_ok = False
        report("4b 前向保密 + 旧钥解历史", roundtrip and fs_ok and hist_ok,
               f"新epoch_roundtrip={roundtrip} carol_旧钥解新密文失败={fs_ok} "
               f"bob_旧钥解历史={hist_ok}")

        # ── 5. 套件协商:都声明 [1,2] → 交集=2 ──────────────────────────
        all_suites = []
        for m in (alice, bob):
            for uid, mem in m.cli.members.items():
                if mem.get("suites"):
                    all_suites.append(set(mem["suites"]))
        # 自己 join 时声明的 + 收到别人的,都应是 {1,2};交集含 2
        common = set.intersection(*all_suites) if all_suites else set()
        suite_ok = 2 in common and all(s == {1, 2} for s in all_suites)
        report("5 套件协商:全员 [1,2] → 当前套件=2", suite_ok and len(all_suites) >= 2,
               f"交集={sorted(common)} 声明数={len(all_suites)}")

        failed = [c for c, ok, _ in _results if not ok]
        print(f"\n{'='*52}\n合计 {len(_results)} 项,PASS {len(_results)-len(failed)},FAIL {len(failed)}")
        if failed:
            print("FAIL 项:", "; ".join(failed))
        return 0 if not failed else 1
    finally:
        for m in (alice, bob, carol):
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
        try:
            os.unlink(state_path)
        except Exception:
            pass
        try:
            tok_file.unlink()
        except Exception:
            pass


def _port_open() -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
