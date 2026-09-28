#!/usr/bin/env python3
# check_both_inbox.py — 查 prisiragent + oiagent 两个 inbox
import sys
sys.path.insert(0, "/workspace/agentmail_helper")
from helper import AgentMail
import json

am = AgentMail()
for name in ["prisiragent@agentmail.to", "oiagent@agentmail.to"]:
    try:
        ms = am.list_messages(inbox=name, limit=20)
        print(f"=== {name} ===")
        print(f"count={ms.get('count', '?')}")
        for m in ms.get("messages", [])[:10]:
            print(f"  from={m.get('from','?')[:40]:40s} subj={m.get('subject','?')[:50]} ts={m.get('created_at','?')}")
    except Exception as e:
        print(f"=== {name} === ERR: {e}")
    print()