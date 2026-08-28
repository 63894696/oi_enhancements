#!/usr/bin/env python3
"""fed_task_client.py — v0.28 Federation task queue 多节点实跑客户端

在 VPS 本地跑(走 127.0.0.1:18791),用 node-win-home 身份签名调本节点 daemon。
模拟"本机 Claude → VPS OIagent"的 Federation task 调用链。

用法(在 VPS 上):
    python3 fed_task_client.py submit "title" "content"
    python3 fed_task_client.py status <task_id>
    python3 fed_task_client.py list [--ready|--blocked|--status STATUS]
    python3 fed_task_client.py cancel <task_id> "reason"
"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

# cryptography 应该装了(aureon-oiagent.py 依赖)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

DAEMON_URL = "http://127.0.0.1:18791"

# 本机 node-win-home 的 keypair(在 VPS peers/ 里已有公钥,所以 VPS 能验签)
# 但本机的 sk 在本机,不在 VPS。所以这个脚本得用 VPS 自己的 node-vps-1 身份调自己
# —— 或者,我们让 VPS 用自己的 sk 签,from=node-vps-1(自调自)
LOCAL_NODE_FILE = Path.home() / ".local" / "share" / "aureon" / "federation" / "local.json"


def load_sk():
    """加载本节点私钥"""
    data = json.loads(LOCAL_NODE_FILE.read_text())
    sk_hex = data["sk_hex"]
    node_id = data["node_id"]
    # hex → bytes → Ed25519PrivateKey
    sk_bytes = bytes.fromhex(sk_hex)
    sk = Ed25519PrivateKey.from_private_bytes(sk_bytes)
    return sk, node_id


def sign_and_post(kind: str, payload: dict) -> dict:
    """签名 + POST /federation/<kind>"""
    sk, node_id = load_sk()
    payload["from"] = node_id
    if "trace_id" not in payload:
        import secrets
        payload["trace_id"] = secrets.token_hex(8)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = sk.sign(body)
    sig_hex = sig.hex()

    url = f"{DAEMON_URL}/federation/{kind}"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Aureon-Sig": sig_hex,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read().decode("utf-8", errors="replace")}
    except Exception as e:
        return {"error": str(e)}


def main():
    if len(sys.argv) < 2:
        print("Usage: fed_task_client.py {submit|status|list|cancel} ...")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "submit":
        if len(sys.argv) < 4:
            print("Usage: submit <title> <content> [--depends-on ID1 ID2] [--priority N]")
            sys.exit(1)
        title = sys.argv[2]
        content = sys.argv[3]
        depends_on = []
        priority = 0
        if "--depends-on" in sys.argv:
            i = sys.argv.index("--depends-on") + 1
            while i < len(sys.argv) and not sys.argv[i].startswith("--"):
                depends_on.append(int(sys.argv[i]))
                i += 1
        if "--priority" in sys.argv:
            priority = int(sys.argv[sys.argv.index("--priority") + 1])
        result = sign_and_post("task/submit", {
            "task_def": {"title": title, "content": content, "depends_on": depends_on, "priority": priority}
        })
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "status":
        if len(sys.argv) < 3:
            print("Usage: status <task_id>")
            sys.exit(1)
        result = sign_and_post("task/status", {"task_id": int(sys.argv[2])})
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "list":
        payload = {}
        if "--ready" in sys.argv:
            payload["ready"] = True
        elif "--blocked" in sys.argv:
            payload["blocked"] = True
        elif "--status" in sys.argv:
            payload["status"] = sys.argv[sys.argv.index("--status") + 1]
        result = sign_and_post("task/list", payload)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == "cancel":
        if len(sys.argv) < 3:
            print("Usage: cancel <task_id> [reason]")
            sys.exit(1)
        reason = sys.argv[3] if len(sys.argv) > 3 else ""
        result = sign_and_post("task/cancel", {"task_id": int(sys.argv[2]), "reason": reason})
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        print(f"unknown cmd: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()