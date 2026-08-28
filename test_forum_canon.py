"""test_forum_canon.py — F-1a 契约产物:跨语言 canon/签名/PoW 测试向量生成器

用法: python test_forum_canon.py > forum_test_vectors.json
F-1c 的 test_forum_canon.js 读此文件逐条断言 JS 端 canon/sha256/Ed25519 输出与本文档一致。
向量中 canon/post_id/sig 全部真实计算(PyCA Ed25519),不假造。

post 对象共识(见 docs/prisir-forum-protocol-2026-08-21.md):
  - 签名载荷: canon(post 除 "sig" 外全字段,含 pow)  ← solve_pow 完成后签
  - post_id:   sha256(canon(含 sig 的完整 post)) → b64url16
"""
import hashlib, json, base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

def canon(o) -> str:
    """与 group.html:163-167 的 JS canon() 逐字节一致:
    对象键递归排序、无空白分隔符、ensure_ascii=False(非 ASCII 直出 UTF-8)。"""
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()

def b64url16(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")[:16]

def check_pow(digest: bytes, bits: int) -> bool:
    """digest 前 bits 个最高有效位全 0(大端逐字节)。"""
    n_full, rem = divmod(bits, 8)
    if digest[:n_full] != b"\x00" * n_full:
        return False
    if rem and (digest[n_full] >> (8 - rem)) != 0:
        return False
    return True

def solve_pow(post: dict, bits: int, max_iter: int = 3_000_000) -> dict:
    """固定其余字段,递增 pow.nonce 直到 sha256(canon(post 无 sig)) 满足前 bits 位为 0。"""
    for nonce in range(max_iter):
        post["pow"] = {"alg": "sha256-b64", "bits": bits, "nonce": nonce}
        d = hashlib.sha256(canon(post).encode("utf-8")).digest()
        if check_pow(d, bits):
            return post
    raise RuntimeError(f"PoW 未在 {max_iter} 次内求解(bits={bits})")

def make_post(priv: Ed25519PrivateKey, body: str, *, kind="post", parent=None,
              ts="2026-08-21T08:00:00Z", pow_bits=8) -> dict:
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    fp = b64url16(hashlib.sha256(pub_raw).digest())
    post = {"v": 1, "kind": kind, "board": "general", "parent": parent,
            "body": body, "author_pub": b64(pub_raw), "author_fp": fp,
            "ts": ts, "pow": None}
    post = solve_pow(post, pow_bits)
    post["sig"] = b64(priv.sign(canon(post).encode("utf-8")))  # 签名载荷含 pow、不含 sig
    full = dict(post)
    full["post_id"] = b64url16(hashlib.sha256(canon(full).encode("utf-8")).digest())
    return full

# 固定种子的确定性测试密钥(仅测试用,严禁用于生产身份)
def test_priv(seed: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(seed)

ALICE = test_priv(b"A" * 32)
BOB = test_priv(b"B" * 32)

CASES = [
    ("ascii", "hello prisir forum"),
    ("unicode", "免注册论坛第一帖:公钥即账号"),
    ("emoji", "签名即发帖 🔏🔐"),
    ("mixed", "mixed 混排 test ✓ 123"),
    ("json_meta", 'tricky "quotes" \\backslash\nnewline\ttab'),
]

out = {"pow_bits_vectors": 8, "note": "post_id=sha256(canon(含sig全帖))→b64url 前16字符", "cases": []}
for tag, body in CASES:
    priv = ALICE if tag in ("ascii", "unicode", "emoji") else BOB
    p = make_post(priv, body)
    # 自验:签名真有效、PoW 真满足、post_id 真一致
    signed = {k: v for k, v in p.items() if k not in ("sig", "post_id")}
    priv.public_key().verify(base64.b64decode(p["sig"]), canon(signed).encode("utf-8"))
    assert check_pow(hashlib.sha256(canon(signed).encode("utf-8")).digest(), 8)
    assert p["post_id"] == b64url16(hashlib.sha256(canon({k: v for k, v in p.items() if k != "post_id"}).encode("utf-8")).digest())
    out["cases"].append({"tag": tag, "post": p})

print(json.dumps(out, ensure_ascii=False, indent=2))
