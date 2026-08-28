"""tests/test_identity.py — identity 服务单测(直接测处理函数,不起 HTTP)。

覆盖:注册 / 验签成功 / 篡改拒绝 / 过期拒绝 / 未知 keyid 拒绝。
签名构造与 client 端 signed_request 走同一份 _build_signature_base 逻辑。
"""

from __future__ import annotations

import base64
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import schemas  # noqa: E402
from agent_economy import agent_identity as I  # noqa: E402
from agent_economy._base import gen_keypair, sign_b64  # noqa: E402


def _make_signed(agent_fp: str, priv, method="POST", path="/tool/bash",
                 authority="127.0.0.1:18791", created=None) -> dict:
    """构造一份 RFC9421 签名头(模拟 client)。"""
    created = created if created is not None else int(time.time())
    date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(created))
    base = I._build_signature_base(method, path, authority, date)
    sig = sign_b64(priv, base)
    comps = " ".join(f'"{c}"' for c in schemas.COVERED_COMPONENTS)
    headers = {
        "Signature-Input": f'{schemas.SIGNATURE_LABEL}=({comps});created={created};keyid={agent_fp}',
        "Signature": f"{schemas.SIGNATURE_LABEL}=:{sig}:",
        "Date": date,
    }
    return {"method": method, "path": path, "authority": authority,
            "headers": headers}


class TestIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 用临时目录隔离
        import tempfile, os
        cls._tmp = tempfile.mkdtemp()
        os.environ["AGENT_ECONOMY_DIR"] = cls._tmp

    def setUp(self):
        # 每个用例重置目录
        I._DIR._path.parent.mkdir(parents=True, exist_ok=True)
        I._DIR._write({"agents": {}})
        self.priv, self.pub = gen_keypair()
        from agent_economy._base import fingerprint
        self.fp = fingerprint(self.pub)
        I._DIR.register("agent-a", self.pub, "测试A")

    def _verify(self, payload: dict):
        status, obj = I._h_verify("/identity/verify", {},
                                  json.dumps(payload).encode())
        return status, obj

    def test_register_and_get(self):
        status, obj = I._h_get("/identity/agent-a", {}, b"")
        self.assertEqual(status, 200)
        self.assertEqual(obj["fp"], self.fp)
        # 重复注册拒绝
        status, obj = I._h_register("/identity/register", {}, json.dumps(
            {"agent_id": "agent-a", "pubkey": self.pub}).encode())
        self.assertEqual(status, 409)

    def test_verify_ok(self):
        status, obj = self._verify(_make_signed(self.fp, self.priv))
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj["agent_id"], "agent-a")

    def test_tampered_rejected(self):
        payload = _make_signed(self.fp, self.priv)
        payload["path"] = "/tool/evil"  # 篡改签名基线
        status, obj = self._verify(payload)
        self.assertEqual(status, 401)

    def test_unknown_keyid_rejected(self):
        priv2, pub2 = gen_keypair()
        from agent_economy._base import fingerprint
        payload = _make_signed(fingerprint(pub2), priv2)  # 未注册的 key
        status, obj = self._verify(payload)
        self.assertEqual(status, 401)
        self.assertIn("未注册", obj["error"])

    def test_expired_rejected(self):
        old = int(time.time()) - schemas.SIG_MAX_SKEW_SEC - 60
        payload = _make_signed(self.fp, self.priv, created=old)
        status, obj = self._verify(payload)
        self.assertEqual(status, 401)
        self.assertIn("过期", obj["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
