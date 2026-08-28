"""simplex_e2e_rotation.py — 功能块③(会话轮换)E2E 验证

Process A (agent): setup -> create_invitation -> 等 peer -> rename 轮换 -> 再发消息
Process B (peer):  setup -> accept -> 读消息 -> 观察发送方显示名变化 + 消息仍通

验证(rename 路径):
  1) agent 换假名后,peer 收到的后续消息发送方 = 新名
  2) 消息流不中断(轮换前后都能收发)

用法:先开 agent,再开 peer。
  python simplex_e2e_rotation.py agent
  python simplex_e2e_rotation.py peer
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_rotation as rot  # noqa: E402
import simplex_tools as st  # noqa: E402

ROOT = Path(__file__).resolve().parent
SHARE = ROOT / "e2e_share_rot"
SHARE.mkdir(exist_ok=True)
INVITE = SHARE / "invite.txt"


def run_agent() -> int:
    print("[agent] setup...", flush=True)
    r = st.call_tool("simplex_setup", {"display_name": "rot-agent", "db_prefix": str(SHARE / "agent_db")})
    if not r["ok"] and not r.get("already_running"):
        print("[agent] setup FAILED", r, flush=True); return 1
    r = st.call_tool("simplex_create_invitation", {})
    if not r["ok"]:
        print("[agent] create_invitation FAILED", r, flush=True); return 1
    INVITE.write_text(r["link"])
    print(f"[agent] invitation: {r['link'][:50]}...", flush=True)

    print("[agent] waiting for peer (up to 120s)...", flush=True)
    cid = None
    for _ in range(60):
        rc = st.call_tool("simplex_list_contacts", {})
        for c in rc.get("output", []):
            if c.get("active"):
                cid, cname = c["contact_id"], c["display_name"]; break
        if cid: break
        time.sleep(2)
    if not cid:
        print("[agent] TIMEOUT: peer never connected", flush=True); return 1
    print(f"[agent] peer connected: {cname}", flush=True)

    # 轮换前发一条
    st.call_tool("simplex_send_message", {"contact": cname, "text": "轮换前的消息"})
    time.sleep(2)

    # rename 轮换
    id0 = rot.call_tool("simplex_current_identity", {})
    old = (id0.get("output") or {}).get("active_user")
    print(f"[agent] current identity: {old}", flush=True)
    r = rot.call_tool("simplex_rotate_identity", {"strategy": "rename"})
    if not r["ok"]:
        print("[agent] rotate FAILED", r, flush=True); return 1
    new = r["output"]["new_alias"]
    print(f"[agent] rotated: {r['output']['old_alias']} -> {new}", flush=True)

    # 轮换后发一条
    rs = st.call_tool("simplex_send_message", {"contact": cname, "text": "轮换后的消息"})
    print(f"[agent] post-rotate send ok={rs['ok']}", flush=True)
    (SHARE / "newname.txt").write_text(new)
    time.sleep(8)
    ok = r["ok"] and rs["ok"] and new != old
    print(f"[agent] {'E2E SUCCESS' if ok else 'E2E FAIL'}", flush=True)
    return 0 if ok else 1


def run_peer() -> int:
    print("[peer] setup...", flush=True)
    r = st.call_tool("simplex_setup", {"display_name": "rot-peer", "db_prefix": str(SHARE / "peer_db")})
    if not r["ok"] and not r.get("already_running"):
        print("[peer] setup FAILED", r, flush=True); return 1
    print("[peer] waiting for invitation...", flush=True)
    for _ in range(60):
        if INVITE.exists(): break
        time.sleep(2)
    if not INVITE.exists():
        print("[peer] TIMEOUT", flush=True); return 1
    r = st.call_tool("simplex_accept_invitation", {"link": INVITE.read_text().strip(), "timeout": 100})
    if not r["ok"]:
        print("[peer] accept FAILED", r, flush=True); return 1
    print(f"[peer] connected: {r['output']}", flush=True)

    # 收两条消息(轮换前后)
    print("[peer] collecting messages (up to 90s)...", flush=True)
    msgs = []
    for _ in range(45):
        rm = st.call_tool("simplex_read_messages", {"pop": True})
        msgs.extend([m.get("text", "") for m in rm.get("output", [])])
        if len(msgs) >= 2: break
        time.sleep(2)
    print(f"[peer] got {len(msgs)} messages: {msgs}", flush=True)

    # 读发送方当前显示名(轮换后应是新名)
    rc = st.call_tool("simplex_list_contacts", {})
    names = [c.get("display_name") for c in rc.get("output", [])]
    newname = (SHARE / "newname.txt").read_text().strip() if (SHARE / "newname.txt").exists() else None
    print(f"[peer] contacts now: {names}; expected new name: {newname}", flush=True)
    ok = len(msgs) >= 2 and newname and newname in names
    print(f"[peer] {'E2E SUCCESS' if ok else 'E2E FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    if role == "agent":
        sys.exit(run_agent())
    elif role == "peer":
        sys.exit(run_peer())
    else:
        print("usage: python simplex_e2e_rotation.py [agent|peer]")
        sys.exit(2)
