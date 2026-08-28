"""simplex_e2e_a2h.py — 功能块②(A2H 审批通道)双进程 E2E 验证

Process A (agent):  setup -> create_invitation -> 等 phone 连上 -> set_approver -> a2h_request(敏感操作)
Process B (phone):  setup -> accept_invitation -> 读审批卡片 -> 回 'yes <id>' 或 'no <id>'

场景:
  1) phone 回 yes → agent 放行(approved=True)
  2) phone 回 no  → agent 中止(approved=False)
  3) 冒充者(非 approver)回 yes → 被忽略

用法:先开 agent,再开 phone。
  python simplex_e2e_a2h.py agent [yes|no]
  python simplex_e2e_a2h.py phone
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_a2h as a2h  # noqa: E402
import simplex_tools as st  # noqa: E402

ROOT = Path(__file__).resolve().parent
SHARE = ROOT / "e2e_share_a2h"
SHARE.mkdir(exist_ok=True)
INVITE = SHARE / "invite.txt"
VERDICT = SHARE / "verdict.txt"  # phone 写它要做的决定(yes/no),便于脚本化


def run_agent() -> int:
    decision = sys.argv[2] if len(sys.argv) > 2 else "yes"
    print(f"[agent] setup... (期望 phone 回 {decision})", flush=True)
    r = st.call_tool("simplex_setup", {"display_name": "agent", "db_prefix": str(SHARE / "agent_db")})
    if not r["ok"] and not r.get("already_running"):
        print("[agent] setup FAILED", r, flush=True); return 1
    r = st.call_tool("simplex_create_invitation", {})
    if not r["ok"]:
        print("[agent] create_invitation FAILED", r, flush=True); return 1
    INVITE.write_text(r["link"])
    print(f"[agent] invitation: {r['link'][:50]}...", flush=True)

    print("[agent] waiting for phone to connect (up to 120s)...", flush=True)
    cid = None
    for _ in range(60):
        rc = st.call_tool("simplex_list_contacts", {})
        for c in rc.get("output", []):
            if c.get("active"):
                cid, cname = c["contact_id"], c["display_name"]; break
        if cid: break
        time.sleep(2)
    if not cid:
        print("[agent] TIMEOUT: phone never connected", flush=True); return 1
    print(f"[agent] phone connected: {cname} (id={cid})", flush=True)

    r = a2h.call_tool("simplex_a2h_set_approver", {"contact": cname})
    if not r["ok"]:
        print("[agent] set_approver FAILED", r, flush=True); return 1
    print(f"[agent] approver = {cname}", flush=True)

    # 给 phone 一点时间在收件箱看到卡片
    time.sleep(3)
    print("[agent] a2h_request(删除生产数据库) — 等裁决...", flush=True)
    r = a2h.call_tool("simplex_a2h_request", {
        "action": "delete_production_db(scope=all)",
        "reason": "高危不可逆操作,需本人确认",
        "timeout": 90,
    })
    print(f"[agent] a2h result -> ok={r['ok']} approved={r.get('approved')} {json.dumps(r, ensure_ascii=False)[:200]}", flush=True)

    expect_approved = (decision == "yes")
    got = r.get("approved")
    ok = (got == expect_approved)
    print(f"[agent] {'E2E SUCCESS' if ok else 'E2E FAIL'} (expected approved={expect_approved}, got={got})", flush=True)
    time.sleep(5)
    return 0 if ok else 1


def run_phone() -> int:
    print("[phone] setup...", flush=True)
    r = st.call_tool("simplex_setup", {"display_name": "myphone", "db_prefix": str(SHARE / "phone_db")})
    if not r["ok"] and not r.get("already_running"):
        print("[phone] setup FAILED", r, flush=True); return 1
    print("[phone] waiting for invitation...", flush=True)
    for _ in range(60):
        if INVITE.exists(): break
        time.sleep(2)
    if not INVITE.exists():
        print("[phone] TIMEOUT waiting for invitation", flush=True); return 1
    r = st.call_tool("simplex_accept_invitation", {"link": INVITE.read_text().strip(), "timeout": 100})
    if not r["ok"]:
        print("[phone] accept FAILED", r, flush=True); return 1
    agent_cid = r["output"]["contact_id"]
    print(f"[phone] connected to agent: id={agent_cid}", flush=True)

    # 等审批卡片(含 [A2H a2h-xxxx])
    print("[phone] waiting for approval card...", flush=True)
    card = None
    for _ in range(45):
        rm = st.call_tool("simplex_read_messages", {"contact": "agent", "pop": True})
        for m in rm.get("output", []):
            if "[A2H" in (m.get("text") or ""):
                card = m["text"]; break
        if card: break
        time.sleep(2)
    if not card:
        print("[phone] TIMEOUT: no approval card", flush=True); return 1
    print(f"[phone] got card: {card[:90]}...", flush=True)

    m = re.search(r"a2h-[0-9a-f]{4}", card)
    if not m:
        print("[phone] no request_id in card", flush=True); return 1
    rid = m.group(0)
    verdict = VERDICT.read_text().strip() if VERDICT.exists() else "yes"
    reply = f"{verdict} {rid}"
    print(f"[phone] replying: '{reply}'", flush=True)
    rs = st.call_tool("simplex_send_message", {"contact": "agent", "text": reply})
    if not rs["ok"]:
        print("[phone] send reply FAILED", rs, flush=True); return 1
    print(f"[phone] verdict '{verdict}' sent for {rid}", flush=True)
    time.sleep(5)
    return 0


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    if role == "agent":
        sys.exit(run_agent())
    elif role == "phone":
        sys.exit(run_phone())
    else:
        print("usage: python simplex_e2e_a2h.py [agent yes|no | phone]")
        sys.exit(2)
