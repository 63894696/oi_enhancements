"""schemas.py — 三件套接口契约(JSON 结构 + RFC9421/VC/x402 对齐定义)。

只定义数据结构与常量,不含服务实现。三服务与 client 共用此契约,避免字段漂移。

签名基线(RFC 9421 / Web Bot Auth 对齐):
  covered components = @method, @path, @authority, date
  Signature-Input: sig1=("@method" "@path" "@authority" "date");created=...;keyid=<fp>
  Signature: sig1=<base64(Ed25519(signature_base))>
"""

from __future__ import annotations

# ── identity(RFC 9421)─────────────────────────────────────────────
# covered components,顺序即签名基线行序
COVERED_COMPONENTS: tuple[str, ...] = ("@method", "@path", "@authority", "date")
SIGNATURE_LABEL = "sig1"  # Web Bot Auth 同款 label
SIG_MAX_SKEW_SEC = 300    # date 头允许的最大时钟偏移

# agent 目录条目(扩展 integrity_pubkeys.json 结构,保留原 contacts 兼容)
# {
#   "agents": {
#     "<agent_id>": {"pubkey": b64, "fp": sha256(pubkey)[:16],
#                    "display": str, "created": ts, "caps": [str]}
#   }
# }


def agent_entry(pubkey_b64: str, fp: str, display: str, created: float,
                caps: list[str] | None = None) -> dict:
    return {"pubkey": pubkey_b64, "fp": fp, "display": display,
            "created": created, "caps": caps or []}


# ── authz(W3C VC 精简 / AP2 Mandate)──────────────────────────────
# Verifiable Credential(精简 JSON,proof=Ed25519):
# {
#   "@context": ["https://www.w3.org/ns/credentials/v2"],
#   "type": ["VerifiableCredential", "AgentCapability"],
#   "issuer": "did:internal:root",          # 签发人(人/root)
#   "issuanceDate": iso8601, "expirationDate": iso8601,
#   "credentialSubject": {
#       "id": "agent:<agent_id>",
#       "capability": str,                   # 如 "tool:bash" / "tool:write_file"
#       "scope": dict
#   },
#   "proof": {"type": "Ed25519Signature2020", "created": iso,
#             "verificationMethod": "did:internal:root#key-1",
#             "proofValue": b64(Ed25519(canonical(vc-without-proof)))}
# }
VC_CONTEXT = "https://www.w3.org/ns/credentials/v2"
VC_TYPE_CAPABILITY = "AgentCapability"
VC_ISSUER_ROOT = "did:internal:root"


def vc_credential(agent_id: str, capability: str, scope: dict,
                  issuance_iso: str, expiration_iso: str) -> dict:
    """返回未带 proof 的 VC 主体(proof 由 authz 服务签名后填入)。"""
    return {
        "@context": [VC_CONTEXT],
        "type": ["VerifiableCredential", VC_TYPE_CAPABILITY],
        "issuer": VC_ISSUER_ROOT,
        "issuanceDate": issuance_iso,
        "expirationDate": expiration_iso,
        "credentialSubject": {
            "id": f"agent:{agent_id}",
            "capability": capability,
            "scope": scope,
        },
    }


# ── meter(x402 语义)──────────────────────────────────────────────
# 配额耗尽时返回 HTTP 402 + 如下头(x402 结构,内部用配额代替链上结算):
X402_HEADER = "X-Payment-Required"
# ledger 表:(agent_id, ts, resource, units, tokens, meta_json)
