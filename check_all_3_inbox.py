#!/usr/bin/env python3
# check_all_3_inbox.py — 查 3 个 inbox 邮件数
import sys
sys.path.insert(0, "/workspace/agentmail_helper")
from helper import AgentMail
import json

am = AgentMail()
for name in ["yiwei@agentmail.to", "babelspan@agentmail.to", "prisiragent@agentmail.to"]:
    try:
        ms = am.list_messages(inbox=name, limit=20)
        count = ms.get('count', '?')
        msgs = ms.get("messages", [])
        print(f"=== {name} count={count} ===")
        for m in msgs[:5]:
            f = m.get('from', '?')[:30]
            s = m.get('subject', '?')[:50]
            print(f"  {f:32s} | {s}")
        print()
    except Exception as e:
        print(f"=== {name} ERR: {e}")