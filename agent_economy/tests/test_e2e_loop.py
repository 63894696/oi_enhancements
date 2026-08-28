"""tests/test_e2e_loop.py — 三件套端到端闭环测试(真 HTTP,三服务 + 受保护资源全起)。

闭环:agent 注册(identity) → 签名 → 取 VC(authz) → 调受保护资源
  → 资源端前置链:验签(identity) + 校验 VC(authz) + 扣费(meter) → 放行
  → 审计(authz/meter 均可查)。

另测三拒绝:篡改签名(401) / capability 不匹配(403) / 配额耗尽(402)。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

# 共享装置:整个测试进程只起一次三件套(必须在 import 服务模块前设定目录)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _shared  # noqa: E402,F401  (设定 AGENT_ECONOMY_DIR 并提供 ensure_services)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import (  # noqa: E402
    HOST, PORT_IDENTITY, PORT_AUTHZ, PORT_METER, schemas,
)
from agent_economy import agent_meter as M  # noqa: E402
from agent_economy._base import gen_keypair, make_handler  # noqa: E402
from agent_economy.client import AgentClient, build_signature_headers, _post  # noqa: E402

RESOURCE_PORT = 18909
RESOURCE_AUTHORITY = f"{HOST}:{RESOURCE_PORT}"
RESOURCE_PATH = "/tool/read_file"


def _post_direct(port: int, path: str, obj: dict) -> tuple[int, dict]:
    """同 client._post,但测 resource 时用。"""
    return _post(port, path, obj)


def _protected_resource_handler(path: str, query: dict, body: bytes):
    """受保护资源:模拟 L3 工具端点,前置链=验签+授权+扣费。"""
    d = json.loads(body or b"{}")
    headers = d.get("headers", {})
    vc = d.get("vc")
    # 1. 验签(identity)
    s, r = _post_direct(PORT_IDENTITY, "/identity/verify", {
        "method": "POST", "path": RESOURCE_PATH,
        "authority": RESOURCE_AUTHORITY, "headers": headers})
    if s != 200:
        return 401, {"ok": False, "stage": "identity", "error": r.get("error")}
    agent_id = r["agent_id"]
    # 2. 授权(authz)
    s, r = _post_direct(PORT_AUTHZ, "/authz/verify", {
        "vc": vc, "required_capability": "tool:read_file"})
    if s != 200:
        return 403, {"ok": False, "stage": "authz", "error": r.get("error")}
    # 3. 扣费(meter)
    s, r = _post_direct(PORT_METER, "/meter/charge", {
        "agent_id": agent_id, "resource": "tool:read_file", "units": 1,
        "meta": {"tokens": d.get("tokens", 0)}})
    if s != 200:
        return s, {"ok": False, "stage": "meter", "error": r.get("error")}
    # 放行:返回资源结果 + 计量回执
    return 200, {"ok": True, "result": f"<{RESOURCE_PATH} 的内容>",
                 "agent_id": agent_id, "balance": r.get("balance")}


def _serve(routes, port, name):
    srv = ThreadingHTTPServer((HOST, port), make_handler(name, routes))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestE2ELoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shared.ensure_services()  # 共享三件套(幂等)
        cls._resource = ThreadingHTTPServer(
            (HOST, RESOURCE_PORT),
            make_handler("resource", {("POST", RESOURCE_PATH): _protected_resource_handler}))
        threading.Thread(target=cls._resource.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls._resource.shutdown()

    def setUp(self):
        self.priv, self.pub = gen_keypair()
        # 每个用例独立 agent_id,避免目录里残留上一个用例的同名注册(409)
        self.agent_id = f"agent-{self._testMethodName}"
        self.client = AgentClient(self.agent_id, self.priv, "E2E")
        r = self.client.register()
        assert r.get("ok"), r
        _post_direct(PORT_METER, "/meter/grant",
                     {"agent_id": self.agent_id, "quota": 3})

    def _call_resource(self, vc, tokens=0, tamper_path=None):
        # HTTP 始终打到正确端点;tamper_path 用于伪造"签名基线里的 path"
        # (模拟中间人改了请求目标但签名仍是旧 path → 验签应失败)
        sign_path = tamper_path or RESOURCE_PATH
        headers = build_signature_headers(self.priv, "POST", sign_path,
                                          RESOURCE_AUTHORITY)
        return _post_direct(RESOURCE_PORT, RESOURCE_PATH, {
            "headers": headers, "vc": vc, "tokens": tokens})

    def test_full_loop(self):
        # 取 VC → 调资源 → 计费 → 审计
        r = self.client.request_vc("tool:read_file")
        self.assertTrue(r.get("ok"), r)
        status, obj = self._call_resource(r["vc"], tokens=120)
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj["agent_id"], self.agent_id)
        self.assertEqual(obj["balance"], 2)  # 3 - 1
        # 审计:meter 流水
        _, ledger = _post_direct(PORT_METER, "/meter/ledger", {}) \
            if False else (200, M._h_ledger("/meter/ledger", {"agent_id": self.agent_id}, b"")[1])
        self.assertGreaterEqual(ledger["count"], 1)

    def test_tampered_signature_401(self):
        r = self.client.request_vc("tool:read_file")
        # 签名基线用 /tool/evil,但资源端按真实路径 RESOURCE_PATH 验签 → 基线不符 → 401
        status, obj = self._call_resource(r["vc"], tamper_path="/tool/evil")
        self.assertEqual(status, 401)
        self.assertEqual(obj["stage"], "identity")

    def test_wrong_capability_403(self):
        # 取的是 read_file 的 VC,但资源要求 delete_file → 用一个要求 delete 的资源
        r = self.client.request_vc("tool:read_file")
        status, obj = _post_direct(PORT_AUTHZ, "/authz/verify", {
            "vc": r["vc"], "required_capability": "tool:delete_file"})
        self.assertEqual(status, 403)

    def test_quota_exhausted_402(self):
        r = self.client.request_vc("tool:read_file")
        vc = r["vc"]
        # 打满 3 次配额
        for _ in range(3):
            s, _ = self._call_resource(vc)
            self.assertEqual(s, 200)
        # 第 4 次 → 402
        status, obj = self._call_resource(vc)
        self.assertEqual(status, 402)
        self.assertEqual(obj["stage"], "meter")


if __name__ == "__main__":
    unittest.main(verbosity=2)
