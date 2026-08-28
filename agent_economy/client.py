"""client.py — agent 端三件套客户端:签名构造 + 授权请求 + 计量上报串联。

`AgentClient.call(capability, ...)` 即端到端闭环的客户端半区:
  签名(RFC9421) → 向 authz 取 VC → 用 VC 调受保护资源 → meter 记账。
服务端的验签/授权/扣费由 L3 钩子(或受保护资源前置)执行 — 见 e2e 测试。
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import PORT_IDENTITY, PORT_AUTHZ, PORT_METER, HOST, schemas
from ._base import fingerprint, pubkey_b64, sign_b64


def _post(port: int, path: str, obj: dict, timeout: int = 15) -> tuple[int, dict]:
    url = f"http://{HOST}:{port}{path}"
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # 402/403 等非 2xx
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {"ok": False, "error": str(e)}


def _get(port: int, path: str, timeout: int = 15) -> tuple[int, dict]:
    url = f"http://{HOST}:{port}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def build_signature_headers(priv: Ed25519PrivateKey, method: str, path: str,
                            authority: str) -> dict:
    """构造 RFC9421 签名头(与 identity._build_signature_base 同基线)。"""
    created = int(time.time())
    date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(created))
    lines = [
        f'"@method": {method.lower()}',
        f'"@path": {path}',
        f'"@authority": {authority.lower()}',
        f'"date": {date}',
    ]
    params = " ".join(f'"{c}"' for c in schemas.COVERED_COMPONENTS)
    lines.append(f'"@signature-params": ({params})')
    base = "\n".join(lines).encode("utf-8")
    sig = sign_b64(priv, base)
    fp = fingerprint(pubkey_b64(priv))
    return {
        "Signature-Input": f'{schemas.SIGNATURE_LABEL}=({params});created={created};keyid={fp}',
        "Signature": f"{schemas.SIGNATURE_LABEL}=:{sig}:",
        "Date": date,
    }


class AgentClient:
    """一个 agent 的三件套客户端。持有身份私钥,封装注册/授权/计量。"""

    def __init__(self, agent_id: str, priv: Ed25519PrivateKey, display: str = ""):
        self.agent_id = agent_id
        self.priv = priv
        self.display = display or agent_id

    def register(self, caps: list[str] | None = None) -> dict:
        return _post(PORT_IDENTITY, "/identity/register", {
            "agent_id": self.agent_id, "pubkey": pubkey_b64(self.priv),
            "display": self.display, "caps": caps or []})[1]

    def request_vc(self, capability: str, scope: dict | None = None,
                   ttl: int = 3600) -> dict:
        return _post(PORT_AUTHZ, "/authz/issue", {
            "subject_agent": self.agent_id, "capability": capability,
            "scope": scope or {}, "ttl": ttl})[1]

    def charge(self, resource: str, units: int = 1, tokens: int = 0) -> tuple[int, dict]:
        return _post(PORT_METER, "/meter/charge", {
            "agent_id": self.agent_id, "resource": resource, "units": units,
            "meta": {"tokens": tokens}})

    def balance(self) -> dict:
        return _get(PORT_METER, f"/meter/balance/{self.agent_id}")[1]
