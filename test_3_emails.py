#!/usr/bin/env python3
# test_3_emails.py — 一次性 batch 跑 3 封历史邮件(M3.48.2 端到端验证)
# 用 inbox 列表里现存的 3 封邮件,跑 email_conf 看分类结果
import sys
sys.path.insert(0, "/workspace/agentmail_helper")
sys.path.insert(0, "/workspace/companion")
from helper import AgentMail
from adapter_registry import get_adapter
from classify_email import classify_email
import json

am = AgentMail()
adapter = get_adapter("email_conf")
print(f"[1/2] adapter 加载: {adapter.spec.description[:80]}\n")

ms = am.list_messages(inbox="prisiragent@agentmail.to", limit=20)
messages = ms.get("messages", [])
print(f"[2/2] 跑 {len(messages)} 封邮件分类:\n")
for m in messages:
    sender = m.get("from", "?")
    subject = m.get("subject", "?")
    body = (m.get("text") or m.get("body") or "")[:200]
    # received_hours 用 created_at
    received_hours = 1.0
    if "created_at" in m:
        from datetime import datetime
        try:
            t = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
            received_hours = max(0.1, (__import__("time").time() - t.timestamp()) / 3600)
        except Exception:
            pass
    has_attachment = bool(m.get("attachments"))

    out = classify_email(adapter, sender, subject, body,
                         received_hours=received_hours,
                         has_attachment=has_attachment)
    print(f"---")
    print(f"FROM   : {sender[:50]}")
    print(f"SUBJ   : {subject[:60]}")
    print(f"BODY_0 : {body[:80]}")
    print(f"DECIDE : risk={out['risk']:8s} action={out['action']:8s}  "
          f"conf={out['risk_conf']} latency={out['latency_ms']}ms "
          f"parse_fail={out['parse_fail']}")
    if not out["parse_fail"]:
        print(f"  ↳ 期望: Amazon→low/delete, OpenAI→low/delete, 验证码→critical/keep")