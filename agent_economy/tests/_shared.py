"""tests/_shared.py — 测试公共装置:整个测试进程只起一次三件套服务。

问题:unittest discover 把 e2e/gateway 等多类收进同一进程,若每个测试类都在
18901-18903 起服务,第二次 bind 会 OSError,请求落到先起的实例(目录不同)→ 409。

解决:模块级单例 — 首次调用起一次,后续复用;端口被占则视为已起(幂等)。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

# 必须在 import 服务模块前设定共享测试数据目录
os.environ["AGENT_ECONOMY_DIR"] = os.environ.get(
    "AGENT_ECONOMY_TEST_DIR") or tempfile.mkdtemp(prefix="agent_econ_test_")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_economy import HOST, PORT_IDENTITY, PORT_AUTHZ, PORT_METER  # noqa: E402
from agent_economy import agent_identity as I, agent_authz as A, agent_meter as M  # noqa: E402
from agent_economy._base import make_handler  # noqa: E402

_started = False
_lock = threading.Lock()


def ensure_services() -> None:
    """起一次三件套(identity/authz/meter)。已起或被占则跳过。线程安全、幂等。"""
    global _started
    with _lock:
        if _started:
            return
        for routes, port, name in (
            (I.ROUTES, PORT_IDENTITY, "identity"),
            (A.ROUTES, PORT_AUTHZ, "authz"),
            (M.ROUTES, PORT_METER, "meter"),
        ):
            try:
                srv = ThreadingHTTPServer((HOST, port), make_handler(name, routes))
            except OSError:
                continue  # 端口已占:视为该服务已在跑(共享实例)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.3)
        _started = True
