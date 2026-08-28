"""Stage-1 E2E: two SimpleX identities, encrypted message round-trip.

Process A (alice): setup -> create_invitation -> write link to file -> wait for
                   contactConnected -> send message.
Process B (bob):   setup -> read link -> accept_invitation -> wait for message in inbox.

Run by simplex_e2e_orchestrate.py (separate processes => separate libsimplex instances).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_tools as st  # noqa: E402

SHARE = Path.home() / ".local" / "share" / "aureon" / "simplex" / "_e2e"
LINK_FILE = SHARE / "invitation.txt"
READY_A = SHARE / "alice_ready"
READY_B = SHARE / "bob_ready"


def run_alice() -> int:
    print("[alice] setup...")
    r = st.call_tool("simplex_setup", {"display_name": "alice", "db_prefix": str(SHARE / "alice_db")})
    if not r["ok"]:
        print("[alice] setup FAILED", r); return 1
    print("[alice] create invitation...")
    r = st.call_tool("simplex_create_invitation", {})
    if not r["ok"]:
        print("[alice] create_invitation FAILED", r); return 1
    link = r["link"]
    LINK_FILE.write_text(link, encoding="utf-8")
    READY_A.write_text("1", encoding="utf-8")
    print(f"[alice] invitation written: {link[:60]}...")

    # wait for bob to connect (contact appears active), then send
    print("[alice] waiting for bob to connect (up to 120s)...")
    deadline = time.time() + 120
    contact = None
    while time.time() < deadline:
        lc = st.call_tool("simplex_list_contacts", {})
        act = [c for c in lc.get("output", []) if c.get("active")]
        if act:
            contact = act[0]
            break
        time.sleep(2)
    if not contact:
        print("[alice] TIMEOUT: bob never connected"); return 1
    print(f"[alice] bob connected: {contact}")

    msg = "你好 Bob,这是来自 alice 的 E2E 加密消息 (stage-1 proof)."
    print(f"[alice] sending: {msg}")
    r = st.call_tool("simplex_send_message", {"contact": str(contact["contact_id"]), "text": msg})
    print(f"[alice] send -> ok={r['ok']} {json.dumps(r, ensure_ascii=False)[:200]}")
    if not r["ok"]:
        return 1
    # keep runtime alive so bob can read
    time.sleep(15)
    return 0


def run_bob() -> int:
    print("[bob] setup...")
    r = st.call_tool("simplex_setup", {"display_name": "bob", "db_prefix": str(SHARE / "bob_db")})
    if not r["ok"]:
        print("[bob] setup FAILED", r); return 1
    print("[bob] waiting for invitation file...")
    deadline = time.time() + 90
    while not LINK_FILE.exists() and time.time() < deadline:
        time.sleep(1)
    if not LINK_FILE.exists():
        print("[bob] TIMEOUT waiting for invitation"); return 1
    link = LINK_FILE.read_text(encoding="utf-8").strip()
    print(f"[bob] accepting invitation: {link[:60]}...")
    r = st.call_tool("simplex_accept_invitation", {"link": link, "timeout": 100})
    if not r["ok"]:
        print("[bob] accept FAILED", r); return 1
    contact = r["output"]
    print(f"[bob] connected to alice: {contact}")
    READY_B.write_text("1", encoding="utf-8")

    # wait for alice's message
    print("[bob] waiting for message (up to 90s)...")
    deadline = time.time() + 90
    while time.time() < deadline:
        rr = st.call_tool("simplex_read_messages", {"pop": True})
        items = rr.get("output", [])
        texts = [i.get("text", "") for i in items]
        if any("E2E 加密消息" in t for t in texts):
            print(f"[bob] GOT MESSAGE: {texts}")
            print("[bob] E2E SUCCESS")
            return 0
        time.sleep(2)
    print("[bob] TIMEOUT: no message received"); return 1


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    SHARE.mkdir(parents=True, exist_ok=True)
    if role == "alice":
        sys.exit(run_alice())
    elif role == "bob":
        sys.exit(run_bob())
    else:
        print("usage: simplex_e2e_two_identities.py [alice|bob]"); sys.exit(2)
