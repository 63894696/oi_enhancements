"""agent_gateway.py — L3 受治理入口(:18910):agent 调 daemon 的三件套前置闸门。

定位:**独立可选服务,不改 daemon(18791)/l4_web 源码**。现有 l4_web → daemon 直连
完全不受影响;只有"想被治理"的新 agent 调用方才走本入口。这是三件套与 L3 的
零风险对接方式 — daemon 保持原样,治理逻辑全在这一层。

前置链(拦 ask):
  RFC9421 验签(identity) → VC 校验(authz, capability=llm:ask)
    → LLM 计量扣费(meter, resource=llm:ask) → policy 允许 → 转发 daemon → 回包

端点:
  GET  /gateway/health
  POST /gateway/ask   {agent_id, headers(RFC9421), vc, ask:{action:"ask",messages:[...]}}
                      → daemon 原始响应 + governance:{balance, ...} | 401/403/402
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import HOST, PORT_IDENTITY, PORT_AUTHZ, PORT_METER  # noqa: E402
from agent_economy._base import err, ok, serve  # noqa: E402

PORT_GATEWAY = 18910
DAEMON_URL = "http://127.0.0.1:18791/"
# 治理的 capability / 计量资源名(ask 一轮 = 1 单位,token 由 daemon tool_trace 估算)
ASK_CAPABILITY = "llm:ask"
ASK_RESOURCE = "llm:ask"


def _post(port: int, path: str, obj: dict, timeout: int = 15) -> tuple[int, dict]:
    url = f"http://{HOST}:{port}{path}"
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return 503, {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _forward_daemon(ask_payload: dict, timeout: int = 180) -> tuple[int, dict]:
    body = json.dumps(ask_payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(DAEMON_URL, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return 503, {"ok": False, "error": f"daemon 不可达: {e}"}


def _h_ask(path: str, query: dict, body: bytes):
    d = json.loads(body or b"{}")
    headers = d.get("headers") or {}
    vc = d.get("vc")
    ask = d.get("ask")
    if not ask or ask.get("action") != "ask":
        return err("ask.action 必须为 'ask'")

    # 1. 验签(identity)— 签名基线用 gateway 自身路径
    s, r = _post(PORT_IDENTITY, "/identity/verify", {
        "method": "POST", "path": "/gateway/ask",
        "authority": f"{HOST}:{PORT_GATEWAY}", "headers": headers})
    if s != 200:
        return 401, {"ok": False, "stage": "identity", "error": r.get("error")}
    agent_id = r["agent_id"]

    # 2. 授权(authz)
    s, r = _post(PORT_AUTHZ, "/authz/verify", {
        "vc": vc, "required_capability": ASK_CAPABILITY})
    if s != 200:
        return 403, {"ok": False, "stage": "authz", "error": r.get("error")}

    # 3. 计量(meter)— 先扣 1 单位,超额直接 402 不转发
    s, r = _post(PORT_METER, "/meter/charge", {
        "agent_id": agent_id, "resource": ASK_RESOURCE, "units": 1,
        "meta": {"tokens": 0}})
    if s != 200:
        return s, {"ok": False, "stage": "meter", "error": r.get("error")}

    # 4. 转发 daemon
    ds, dresp = _forward_daemon(ask)
    dresp.setdefault("governance", {})
    dresp["governance"].update({
        "agent_id": agent_id, "balance": r.get("balance"),
        "charged_resource": ASK_RESOURCE, "daemon_status": ds,
    })
    return (200 if ds == 200 else ds), dresp


ROUTES = {
    ("GET", "/gateway/health"): lambda p, q, b: ok({"status": "up"}),
    ("POST", "/gateway/ask"): _h_ask,
}


if __name__ == "__main__":
    print(f"[gateway] 受治理入口 → daemon {DAEMON_URL} (现有 l4_web 直连不受影响)", flush=True)
    serve("gateway", PORT_GATEWAY, ROUTES, HOST)
