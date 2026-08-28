"""agent_authz.py — authz 服务(:18902):W3C VC 授权签发/校验 + policy_engine mandate。

对齐 AP2 Mandate / W3C VC:VC 用精简 JSON-LD 格式,proof=Ed25519 签名。
签发人是 root(组织信任根);副作用 capability 先经 policy_engine 审批(human mandate),
批准后才签发 VC — 复用现有审批,不重造。

端点:
  GET  /authz/health
  POST /authz/issue   {subject_agent, capability, scope?, ttl?}  → {ok, vc} | 403
  POST /authz/verify  {vc, required_capability}                  → {ok, agent_id} | 403
  GET  /authz/audit                                             → 签发/校验审计流水
"""

from __future__ import annotations

import base64
import calendar
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import PORT_AUTHZ, HOST, schemas  # noqa: E402
from agent_economy._base import (  # noqa: E402
    AuditDB, data_path, err, gen_keypair, load_private_key, now_iso, ok,
    pubkey_b64, save_private_key, serve, sign_b64, verify_b64,
)

# policy_engine 复用(副作用 capability 的 human mandate)
try:
    from policy_engine import policy_check_daemon  # type: ignore
    _HAS_POLICY = True
except Exception:  # noqa: BLE001
    _HAS_POLICY = False

# 副作用 capability 前缀(需 human mandate 审批才签发)
SIDE_EFFECT_PREFIXES = ("tool:bash", "tool:shell", "tool:write_file", "tool:edit_file")

_DDL = """
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  action TEXT NOT NULL,       -- issue | verify
  agent_id TEXT, capability TEXT,
  decision TEXT NOT NULL,     -- allow | deny
  detail TEXT NOT NULL DEFAULT ''
);
"""

_DB = AuditDB(data_path("authz.db"), _DDL)
_ROOT_KEY_FILE = data_path("root_ed25519.key")


def _root_key():
    """组织信任根密钥(签发 VC)。首次启动生成,0600。"""
    if _ROOT_KEY_FILE.exists():
        return load_private_key(_ROOT_KEY_FILE)
    priv, _ = gen_keypair()
    save_private_key(priv, _ROOT_KEY_FILE)
    return priv


def _canonical(obj: dict) -> bytes:
    """VC 规范化签名输入:去 proof、键排序、紧凑分隔符。"""
    body = {k: v for k, v in obj.items() if k != "proof"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _sign_vc(vc: dict) -> dict:
    priv = _root_key()
    vc = dict(vc)
    vc["proof"] = {
        "type": "Ed25519Signature2020",
        "created": now_iso(),
        "verificationMethod": f"{schemas.VC_ISSUER_ROOT}#key-1",
        "proofValue": sign_b64(priv, _canonical(vc)),
    }
    return vc


def _verify_vc_signature(vc: dict) -> bool:
    proof = vc.get("proof") or {}
    pv = proof.get("proofValue")
    if not pv:
        return False
    return verify_b64(pubkey_b64(_root_key()), _canonical(vc), pv)


def _needs_mandate(capability: str) -> bool:
    return any(capability.startswith(p) for p in SIDE_EFFECT_PREFIXES)


def _audit(action: str, agent_id: str, capability: str, decision: str, detail: str = ""):
    _DB.execute(
        "INSERT INTO audit(ts,action,agent_id,capability,decision,detail) VALUES(?,?,?,?,?,?)",
        (time.time(), action, agent_id, capability, decision, detail))


def _h_issue(path: str, query: dict, body: bytes):
    d = json.loads(body or b"{}")
    agent_id, capability = d.get("subject_agent"), d.get("capability")
    if not agent_id or not capability:
        return err("subject_agent 与 capability 必填")
    scope = d.get("scope", {})
    ttl = int(d.get("ttl", 3600))

    # human mandate:副作用 capability 先经 policy_engine 审批
    if _needs_mandate(capability):
        if not _HAS_POLICY:
            _audit("issue", agent_id, capability, "deny", "policy_engine 不可用")
            return err("policy_engine 不可用,副作用 capability 拒签", 403)
        # 复用现有审批仲裁:返回 (decision, reason)
        tool = capability.split(":", 1)[1] if ":" in capability else capability
        decision, reason = policy_check_daemon(tool, scope)
        if decision != "allow":
            _audit("issue", agent_id, capability, "deny", f"mandate:{decision}:{reason}")
            return err(f"human mandate 未通过({decision}): {reason}", 403)

    now = time.time()
    vc = schemas.vc_credential(agent_id, capability, scope,
                               now_iso(now), now_iso(now + ttl))
    vc = _sign_vc(vc)
    _audit("issue", agent_id, capability, "allow", "signed")
    return ok({"vc": vc})


def _h_verify(path: str, query: dict, body: bytes):
    d = json.loads(body or b"{}")
    vc, required = d.get("vc"), d.get("required_capability")
    if not vc or not required:
        return err("vc 与 required_capability 必填")
    subj = (vc.get("credentialSubject") or {})
    agent_id = subj.get("id", "").replace("agent:", "")

    if not _verify_vc_signature(vc):
        _audit("verify", agent_id, required, "deny", "bad signature")
        return err("VC 签名校验失败", 403)
    # 过期
    exp = vc.get("expirationDate", "")
    try:
        # VC 时间戳是 UTC(Z 结尾),必须用 timegm,不能用本地时区的 mktime
        exp_ts = calendar.timegm(time.strptime(exp, "%Y-%m-%dT%H:%M:%SZ"))
        if time.time() > exp_ts:
            _audit("verify", agent_id, required, "deny", "expired")
            return err("VC 已过期", 403)
    except (ValueError, OverflowError):
        pass
    # capability 匹配(支持前缀:required "tool:bash" 匹配 vc "tool:bash")
    if subj.get("capability") != required:
        _audit("verify", agent_id, required, "deny", "capability mismatch")
        return err(f"capability 不匹配(持 {subj.get('capability')},需 {required})", 403)
    _audit("verify", agent_id, required, "allow", "")
    return ok({"agent_id": agent_id, "capability": required})


def _h_audit(path: str, query: dict, body: bytes):
    rows = _DB.query(
        "SELECT ts,action,agent_id,capability,decision,detail FROM audit "
        "ORDER BY id DESC LIMIT 200")
    entries = [{"ts": r[0], "action": r[1], "agent_id": r[2], "capability": r[3],
                "decision": r[4], "detail": r[5]} for r in rows]
    return ok({"entries": entries, "count": len(entries)})


ROUTES = {
    ("GET", "/authz/health"): lambda p, q, b: ok({"status": "up"}),
    ("POST", "/authz/issue"): _h_issue,
    ("POST", "/authz/verify"): _h_verify,
    ("GET", "/authz/audit"): _h_audit,
}


if __name__ == "__main__":
    _root_key()  # 确保根密钥已生成
    print(f"[authz] policy_engine 对接: {'可用' if _HAS_POLICY else '不可用(副作用 capability 将拒签)'}", flush=True)
    serve("authz", PORT_AUTHZ, ROUTES, HOST)
