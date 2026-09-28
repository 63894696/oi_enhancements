#!/usr/bin/env python3
# list_all_inboxes.py — 列当前 API key 可见的所有 inbox
import sys
sys.path.insert(0, "/workspace/agentmail_helper")
from helper import AgentMail
import json

am = AgentMail()
try:
    inboxes = am.list_inboxes()
    print(f"=== {len(inboxes)} inboxes visible ===")
    for i in inboxes:
        print(f"  id={i.get('inbox_id') or i.get('id') or '?'} username={i.get('username','?')} display={i.get('display_name','?')}")
except Exception as e:
    print(f"ERR: {e}")