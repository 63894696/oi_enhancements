"""agent_identity.py — identity 服务(:18901):RFC 9421 签名验签 + Ed25519 agent 目录。

对齐 Web Bot Auth:covered components = @method @path @authority date,
Signature-Input/Signature 头,Ed25519。未来对外零改动。

端点:
  GET  /identity/health
  POST /identity/register   {agent_id, pubkey, display?, caps?}        → {ok, fp}
  GET  /identity/{agent_id}                                            → {ok, pubkey, fp, ...}
  GET  /identity                                                     → {ok, agents}
  POST /identity/verify     {method, path, authority, headers, body?}  → {ok, agent_id}
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import PORT_IDENTITY, HOST, schemas  # noqa: E402
from agent_economy._base import (  # noqa: E402
    AgentDirectory, data_path, err, ok, serve, verify_b64,
)

_DIR = AgentDirectory()


def _build_signature_base(method: str, path: str, authority: str,
                          date: str) -> bytes:
    """RFC 9421 signature base:covered components 每行 `"<name>": <value>`,
    末行 `"@signature-params": <params>`。与 client 端构造必须一致。"""
    lines = [
        f'"@method": {method.lower()}',
        f'"@path": {path}',
        f'"@authority": {authority.lower()}',
        f'"date": {date}',
    ]
    params = " ".join(f'"{c}"' for c in schemas.COVERED_COMPONENTS)
    lines.append(f'"@signature-params": ({params})')
    return "\n".join(lines).encode("utf-8")


def _parse_signature_input(header: str) -> dict | None:
    """解析 `sig1=("@method" ...);created=...;keyid=<fp>`。"""
    if "=" not in header:
        return None
    label, rest = header.split("=", 1)
    if label.strip() != schemas.SIGNATURE_LABEL:
        return None
    out: dict = {"label": label.strip()}
    # covered components 在 (...) 内
    if rest.startswith("("):
        close = rest.find(")")
        out["components"] = rest[1:close]
        rest = rest[close + 1:]
    for part in rest.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip().strip('"')
    return out


def _h_register(path: str, query: dict, body: bytes):
    d = json.loads(body or b"{}")
    agent_id, pub = d.get("agent_id"), d.get("pubkey")
    if not agent_id or not pub:
        return err("agent_id 与 pubkey 必填")
    try:
        base64.b64decode(pub)
    except Exception:  # noqa: BLE001
        return err("pubkey 非合法 base64")
    res = _DIR.register(agent_id, pub, d.get("display", ""),
                        d.get("caps"), d.get("allow_overwrite", False))
    if not res["ok"]:
        return err(res["error"], 409)
    return ok(res)


def _h_get(path: str, query: dict, body: bytes):
    agent_id = path[len("/identity/"):]
    entry = _DIR.get(agent_id)
    if not entry:
        return err(f"agent '{agent_id}' 未注册", 404)
    return ok({"agent_id": agent_id, **entry})


def _h_list(path: str, query: dict, body: bytes):
    return ok({"agents": _DIR.list_all()})


def _h_verify(path: str, query: dict, body: bytes):
    d = json.loads(body or b"{}")
    headers = {k.lower(): v for k, v in (d.get("headers") or {}).items()}
    sig_in = headers.get("signature-input", "")
    sig = headers.get("signature", "")
    if not sig_in or not sig:
        return err("缺 Signature-Input / Signature 头", 401)
    params = _parse_signature_input(sig_in)
    if not params or "keyid" not in params:
        return err("Signature-Input 解析失败", 401)
    # 时钟偏移
    try:
        created = float(params.get("created", 0))
        if abs(time.time() - created) > schemas.SIG_MAX_SKEW_SEC:
            return err("签名已过期(时钟偏移超限)", 401)
    except ValueError:
        return err("created 非数字", 401)
    # keyid = agent fp → 反查 agent
    keyid = params["keyid"]
    agent = next((aid for aid, e in _DIR.list_all().items()
                  if e.get("fp") == keyid), None)
    if not agent:
        return err("未知 keyid(agent 未注册)", 401)
    date = headers.get("date", "")
    base = _build_signature_base(d.get("method", "GET"), d.get("path", "/"),
                                 d.get("authority", ""), date)
    # Signature 头: sig1=<b64>
    sig_b64 = sig.split("=", 1)[1] if "=" in sig else sig
    entry = _DIR.get(agent)
    if not verify_b64(entry["pubkey"], base, sig_b64):
        return err("签名校验失败", 401)
    return ok({"agent_id": agent, "fp": keyid})


ROUTES = {
    ("GET", "/identity/health"): lambda p, q, b: ok({"status": "up"}),
    ("POST", "/identity/register"): _h_register,
    ("POST", "/identity/verify"): _h_verify,
    ("GET", "/identity"): _h_list,
    ("GET", "/identity/"): _h_get,   # 前缀匹配带参
}


if __name__ == "__main__":
    data_path("")  # 确保目录存在
    print(f"[identity] agents 目录: {_DIR._path}", flush=True)
    serve("identity", PORT_IDENTITY, ROUTES, HOST)
