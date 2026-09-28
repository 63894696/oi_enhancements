#!/usr/bin/env python3
# check_inbox.py — 查 prisiragent@agentmail.to inbox 邮件
import sys
sys.path.insert(0, "/workspace/agentmail_helper")
from helper import AgentMail
import json

am = AgentMail()
ms = am.list_messages(inbox="prisiragent@agentmail.to", limit=10)
print(json.dumps(ms, ensure_ascii=False, indent=1))