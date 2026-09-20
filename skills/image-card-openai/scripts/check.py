import os, sys, urllib.request, urllib.error

KEY = os.environ.get("OPENAI_API_KEY", "")


def check():
    msgs = []
    if not KEY:
        msgs.append(("fail", "OPENAI_API_KEY 未设"))
        return msgs
    try:
        req = urllib.request.Request("https://api.openai.com/v1/models",
                                     headers={"Authorization": f"Bearer {KEY}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            msgs.append(("ok", f"Reachable, HTTP {r.status}"))
    except urllib.error.HTTPError as e:
        msgs.append(("fail", f"HTTP {e.code} — key 可能无效"))
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