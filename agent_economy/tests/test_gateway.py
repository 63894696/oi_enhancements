"""tests/test_gateway.py — L3 受治理入口测试(真三件套 + daemon 桩,不依赖真实 daemon)。

覆盖:完整治理链放行(验签+授权+扣费→转发) / 无签名 401 / 错 capability 403 /
配额耗尽 402 / daemon 不可达 503。daemon 用本地桩,不碰真实 18791、不触发真 LLM。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

# 共享装置:整个测试进程只起一次三件套
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _shared  # noqa: E402,F401
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import HOST, PORT_IDENTITY, PORT_AUTHZ, PORT_METER  # noqa: E402
from agent_economy import agent_gateway as G  # noqa: E402
from agent_economy._base import gen_keypair, make_handler  # noqa: E402
from agent_economy.client import AgentClient, build_signature_headers, _post  # noqa: E402

GW_PORT = 18919
G.PORT_GATEWAY = GW_PORT  # 测试用独立端口
DAEMON_STUB_PORT = 18799


def _daemon_stub(path, query, body):
    d = json.loads(body or b"{}")
    return 200, {"ok": True, "answer": "pong",
                 "tool_trace": [{"name": "echo", "ok": True}],
                 "echo_action": d.get("action")}


def _serve(routes, port, name):
    srv = ThreadingHTTPServer((HOST, port), make_handler(name, routes))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _shared.ensure_services()  # 共享三件套(幂等)
        G.DAEMON_URL = f"http://{HOST}:{DAEMON_STUB_PORT}/"
        cls._servers = [
            _serve({("POST", "/"): _daemon_stub}, DAEMON_STUB_PORT, "daemon-stub"),
            _serve(G.ROUTES, GW_PORT, "gateway"),
        ]
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        for s in cls._servers:
            s.shutdown()

    def setUp(self):
        self.priv, _ = gen_keypair()
        self.agent_id = f"gw-{self._testMethodName}"
        self.client = AgentClient(self.agent_id, self.priv)
        assert self.client.register().get("ok")
        _post(PORT_METER, "/meter/grant", {"agent_id": self.agent_id, "quota": 2})

    def _ask(self, vc=None, sign=True):
        headers = (build_signature_headers(self.priv, "POST", "/gateway/ask",
                                           f"{HOST}:{GW_PORT}") if sign else {})
        if vc is None:
            vc = self.client.request_vc("llm:ask").get("vc")
        return _post(GW_PORT, "/gateway/ask", {
            "agent_id": self.agent_id, "headers": headers, "vc": vc,
            "ask": {"action": "ask", "messages": [{"role": "user", "content": "hi"}]}})

    def test_full_governed_ask(self):
        status, obj = self._ask()
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj["answer"], "pong")
        self.assertEqual(obj["governance"]["agent_id"], self.agent_id)
        self.assertEqual(obj["governance"]["balance"], 1)  # 2 - 1

    def test_no_signature_401(self):
        status, obj = self._ask(sign=False)
        self.assertEqual(status, 401)
        self.assertEqual(obj["stage"], "identity")

    def test_wrong_capability_403(self):
        vc = self.client.request_vc("tool:read_file").get("vc")  # 非 llm:ask
        status, obj = self._ask(vc=vc)
        self.assertEqual(status, 403)
        self.assertEqual(obj["stage"], "authz")

    def test_quota_exhausted_402(self):
        self.assertEqual(self._ask()[0], 200)  # 扣 1
        self.assertEqual(self._ask()[0], 200)  # 扣 1
        status, obj = self._ask()              # 第 3 次 → 402
        self.assertEqual(status, 402)
        self.assertEqual(obj["stage"], "meter")

    def test_daemon_down_503(self):
        orig = G.DAEMON_URL
        G.DAEMON_URL = f"http://{HOST}:1/"  # 不可达
        try:
            status, obj = self._ask()
            self.assertEqual(status, 503)
        finally:
            G.DAEMON_URL = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
