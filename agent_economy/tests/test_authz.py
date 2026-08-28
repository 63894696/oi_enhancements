"""tests/test_authz.py — authz 服务单测(直接测处理函数)。

覆盖:只读 capability 直签 / VC 校验通过 / 过期拒绝 / capability 不匹配拒绝 /
篡改 VC 拒绝 / 副作用 capability 触发 policy_engine mandate。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["AGENT_ECONOMY_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import schemas  # noqa: E402
from agent_economy import agent_authz as A  # noqa: E402
from agent_economy._base import now_iso  # noqa: E402


def issue(aid, cap, scope=None, ttl=3600):
    return A._h_issue("/authz/issue", {}, json.dumps(
        {"subject_agent": aid, "capability": cap, "scope": scope or {},
         "ttl": ttl}).encode())


def verify(vc, cap):
    return A._h_verify("/authz/verify", {}, json.dumps(
        {"vc": vc, "required_capability": cap}).encode())


class TestAuthz(unittest.TestCase):
    def setUp(self):
        A._DB.execute("DELETE FROM audit")

    def test_readonly_issue_and_verify(self):
        status, obj = issue("agent-a", "tool:read_file")
        self.assertEqual(status, 200, obj)
        vc = obj["vc"]
        self.assertEqual(vc["issuer"], schemas.VC_ISSUER_ROOT)
        status, vobj = verify(vc, "tool:read_file")
        self.assertEqual(status, 200, vobj)
        self.assertEqual(vobj["agent_id"], "agent-a")

    def test_capability_mismatch_rejected(self):
        _, obj = issue("agent-a", "tool:read_file")
        status, vobj = verify(obj["vc"], "tool:delete_file")
        self.assertEqual(status, 403)
        self.assertIn("不匹配", vobj["error"])

    def test_expired_rejected(self):
        status, obj = issue("agent-a", "tool:read_file", ttl=-10)  # 已过期
        self.assertEqual(status, 200)
        status, vobj = verify(obj["vc"], "tool:read_file")
        self.assertEqual(status, 403)
        self.assertIn("过期", vobj["error"])

    def test_tampered_vc_rejected(self):
        _, obj = issue("agent-a", "tool:read_file")
        vc = obj["vc"]
        vc["credentialSubject"]["capability"] = "tool:delete_file"  # 篡改
        status, vobj = verify(vc, "tool:delete_file")
        self.assertEqual(status, 403)
        self.assertIn("签名", vobj["error"])

    def test_side_effect_requires_mandate(self):
        """副作用 capability:有 policy_engine 且返回 allow 才签发。"""
        if not A._HAS_POLICY:
            status, obj = issue("agent-a", "tool:bash", {"cmd": "ls"})
            self.assertEqual(status, 403)  # 无 policy_engine 拒签
            return
        # 有 policy_engine:inject 一个 allow 的桩
        orig = A.policy_check_daemon
        try:
            A.policy_check_daemon = lambda tool, scope: ("allow", "test")
            status, obj = issue("agent-a", "tool:bash", {"cmd": "ls"})
            self.assertEqual(status, 200, obj)
            # deny 时拒签
            A.policy_check_daemon = lambda tool, scope: ("deny", "危险命令")
            status, obj = issue("agent-a", "tool:bash", {"cmd": "rm -rf /"})
            self.assertEqual(status, 403)
        finally:
            A.policy_check_daemon = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
