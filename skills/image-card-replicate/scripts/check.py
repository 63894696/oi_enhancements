# -*- coding: utf-8 -*-
"""image-card-replicate 前置检查。"""
import os, sys, urllib.request, urllib.error

TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")


def check():
    msgs = []
    if not TOKEN:
        msgs.append(("fail", "REPLICATE_API_TOKEN 未设(export REPLICATE_API_TOKEN=r8_...)"))
        return msgs
    try:
        req = urllib.request.Request("https://api.replicate.com/v1/account",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            j = r.read().decode("utf-8", errors="replace")[:120]
            msgs.append(("ok", f"Reachable, account info: {j}"))
    except urllib.error.HTTPError as e:
        msgs.append(("fail", f"HTTP {e.code} — token 可能无效"))
    except Exception as e:
        msgs.append(("fail", f"Network: {e}"))
    return msgs


if __name__ == "__main__":
    fail = False
    for level, msg in check():
        print(f"[{level}] {msg}")
        if level == "fail":
            fail = True
    sys.exit(1 if fail else 0)