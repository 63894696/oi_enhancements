"""tests/_verify_port_status_api.py — M3.34 /api/port_status 端点 E2E 测试。

策略:启动一个 ThreadingHTTPServer(同款 Handler)on 0 端口(OS 分配),
  模拟主进程端口 fallback 场景,fetch /api/port_status 验证:
    - 默认 changed=False,reason=""
    - 设了 _CONFIGURED_PORT=99999, _REAL_PORT=18811 → changed=True, reason=conflict_fallback
    - 设了 _CONFIGURED_PORT=0, _REAL_PORT=54321 → changed=True, reason=os_allocated

注意:不真启动主进程(避免启动所有 LLM/SQLite/perm_gate 依赖),
   直接 spawn 一个最小 HTTP server 用 prisIragent_web.Handler 子类,
   在测试里手动设 _CONFIGURED_PORT / _REAL_PORT。
"""
import importlib.util
import os
import socket
import sys
import threading
import time
from urllib import request as urlreq

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "prisIragent_web", os.path.join(ROOT, "prisIragent_web.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["prisIragent_web"] = mod
    spec.loader.exec_module(mod)
    return mod


def pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_default_state():
    """默认状态:configured == actual → changed=False。"""
    print("=== test 1: 默认状态 ===")
    mod = load_module()
    # 模块导入后 _CONFIGURED_PORT / _REAL_PORT 是从 env 来的,默认应相等
    assert mod._CONFIGURED_PORT == mod._REAL_PORT, \
        f"want equal, got {mod._CONFIGURED_PORT} vs {mod._REAL_PORT}"
    print(f"  configured={mod._CONFIGURED_PORT} actual={mod._REAL_PORT}")
    print("  PASS")


def test_handler_endpoint_no_change():
    """起一个最小 server, fetch /api/port_status,默认应 changed=False。"""
    print("=== test 2: Handler /api/port_status 默认状态 ===")
    mod = load_module()
    # 用裸 Handler 跑最小 server
    port = pick_free_port()
    server = mod.ThreadingHTTPServer(("127.0.0.1", port), mod.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="test-server")
    t.start()
    try:
        time.sleep(0.3)
        # 确保 _CONFIGURED_PORT == _REAL_PORT
        mod._CONFIGURED_PORT = port
        mod._REAL_PORT = port
        url = f"http://127.0.0.1:{port}/prisiragent/api/port_status"
        with urlreq.urlopen(url, timeout=2.0) as r:
            data = r.read().decode("utf-8")
        import json
        d = json.loads(data)
        print(f"  response: {d}")
        assert d.get("ok"), f"want ok=True, got {d}"
        web = d["web"]
        assert web["configured"] == port
        assert web["actual"] == port
        assert web["changed"] is False
        assert web["reason"] == ""
        print("  PASS")
    finally:
        server.shutdown()
        server.server_close()


def test_handler_endpoint_conflict():
    """conflict_fallback 场景:configured=18802, actual=18811。"""
    print("=== test 3: 端口冲突 fallback ===")
    mod = load_module()
    port = pick_free_port()
    server = mod.ThreadingHTTPServer(("127.0.0.1", port), mod.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="test-server")
    t.start()
    try:
        time.sleep(0.3)
        # 模拟 fallback:configured=18802(原值),actual=18811(真值)
        mod._CONFIGURED_PORT = 18802
        mod._REAL_PORT = 18811
        url = f"http://127.0.0.1:{port}/prisiragent/api/port_status"
        with urlreq.urlopen(url, timeout=2.0) as r:
            data = r.read().decode("utf-8")
        import json
        d = json.loads(data)
        print(f"  response: {d}")
        assert d.get("ok")
        web = d["web"]
        assert web["configured"] == 18802
        assert web["actual"] == 18811
        assert web["changed"] is True
        assert web["reason"] == "conflict_fallback"
        print("  PASS")
    finally:
        server.shutdown()
        server.server_close()


def test_handler_endpoint_os_allocated():
    """OS 自动分配场景:configured=0(让 OS 选),actual=54321。"""
    print("=== test 4: OS 自动分配 ===")
    mod = load_module()
    port = pick_free_port()
    server = mod.ThreadingHTTPServer(("127.0.0.1", port), mod.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True, name="test-server")
    t.start()
    try:
        time.sleep(0.3)
        mod._CONFIGURED_PORT = 0
        mod._REAL_PORT = 54321
        url = f"http://127.0.0.1:{port}/prisiragent/api/port_status"
        with urlreq.urlopen(url, timeout=2.0) as r:
            data = r.read().decode("utf-8")
        import json
        d = json.loads(data)
        print(f"  response: {d}")
        assert d.get("ok")
        web = d["web"]
        assert web["configured"] == 0
        assert web["actual"] == 54321
        assert web["changed"] is True
        assert web["reason"] == "os_allocated"
        print("  PASS")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_default_state()
    test_handler_endpoint_no_change()
    test_handler_endpoint_conflict()
    test_handler_endpoint_os_allocated()
    print("ALL OK")