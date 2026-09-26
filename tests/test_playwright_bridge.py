# -*- coding: utf-8 -*-
"""tests/test_playwright_bridge.py — P3j T25 Playwright MCP 桥接测试。

8 个 mock case 覆盖(全程 mock subprocess,不真起 Chromium / npx):
  · pw_health 3 路(missing_node / npx_failed / ready 三档)
  · _JSONRPCClient 请求/响应 1 路(mock 子进程 stdin/stdout 协奏)
  · pw_navigate 成功 1 路
  · pw_navigate timeout 1 路
  · pw_click / pw_type 1 路(共享)
  · pw_evaluate 1 路
  · pw_close 终止子进程 1 路

依赖:pytest + monkeypatch。
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# helper:mock shutil.which
# ---------------------------------------------------------------------------

def _patch_which(monkeypatch, mapping: dict[str, str | None]):
    """monkeypatch shutil.which(name) → mapping[name] 或 None。"""
    import shutil as _sh
    def fake_which(name):
        return mapping.get(name)
    monkeypatch.setattr(_sh, "which", fake_which)


# ---------------------------------------------------------------------------
# helper:patch Popen 到 _FakeProc(stub 子进程)
# ---------------------------------------------------------------------------

class _FakeStdin:
    def __init__(self):
        self.writes: list[str] = []

    def write(self, s: str):
        self.writes.append(s)

    def flush(self):
        pass


class _FakeStdout:
    """block-on-stdin stdout:每次 __next__ 阻塞等 stdin 写入后才返下一行。

    超过响应数 → 返 ""(EOF marker,read_loop 检测会停)。
    """
    def __init__(self, stdin: _FakeStdin, responses: list[str]):
        self._stdin = stdin
        self._responses = list(responses)
        self._i = 0
        self._last_seen = 0
        self._lock = threading.Lock()

    def __iter__(self):
        return self

    def __next__(self) -> str:
        import time
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with self._lock:
                cur_writes = len(self._stdin.writes)
                if cur_writes > self._last_seen:
                    self._last_seen = cur_writes
                    if self._i < len(self._responses):
                        line = self._responses[self._i]
                        self._i += 1
                        return line + "\n"
                    # 已无响应 → EOF
                    raise StopIteration
            time.sleep(0.005)
        raise StopIteration


class _FakeProc:
    """mock subprocess.Popen:捕获 stdin.write,提供 stdout 队列。"""
    def __init__(self, responses: list[str]):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(self.stdin, responses)
        self.stderr_lines: list[str] = []
        self._poll_return = None  # None=alive
        self._terminated = False

    def poll(self):
        return self._poll_return

    def terminate(self):
        self._terminated = True
        self._poll_return = 0  # exited with 0

    def kill(self):
        self._poll_return = -9

    def wait(self, *, timeout=None):
        return 0


def _patch_popen(monkeypatch, proc_factory):
    """monkeypatch subprocess.Popen → 我们的 factory(cmd, ...)。"""
    import subprocess as _sp
    monkeypatch.setattr(_sp, "Popen", proc_factory)


# ---------------------------------------------------------------------------
# 1. pw_health missing_node(Node 不在 PATH)
# ---------------------------------------------------------------------------

def test_pw_health_missing_node(monkeypatch):
    """shutil.which('node') → None → mode=missing_node。"""
    import shutil as _sh
    monkeypatch.setattr(_sh, "which",
                        lambda n: None if n == "node" else "/usr/bin/npx")
    from prisir_work import playwright_bridge as _pw
    h = _pw.pw_health()
    assert h["ok"] is False
    assert h["mode"] == "missing_node"
    assert "nodejs.org" in h["hint"]


# ---------------------------------------------------------------------------
# 2. pw_health npx_failed(npx 调用 raise)
# ---------------------------------------------------------------------------

def test_pw_health_npx_failed(monkeypatch):
    """npx 找不到 → mode=npx_failed。"""
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda n: "/usr/bin/" + n)
    import subprocess as _sp
    def boom(*a, **kw):
        raise FileNotFoundError("npx gone")
    monkeypatch.setattr(_sp, "run", boom)
    from prisir_work import playwright_bridge as _pw
    h = _pw.pw_health()
    assert h["ok"] is False
    assert h["mode"] == "missing_npx"


# ---------------------------------------------------------------------------
# 3. pw_health not_installed(npx 返非 0)
# ---------------------------------------------------------------------------

def test_pw_health_not_installed(monkeypatch):
    """npx 拉包返 rc≠0 → mode=not_installed。"""
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda n: "/usr/bin/" + n)
    import subprocess as _sp
    class _R:
        returncode = 1
        stdout = ""
        stderr = "ERR npm 404"
    monkeypatch.setattr(_sp, "run", lambda *a, **kw: _R())
    from prisir_work import playwright_bridge as _pw
    h = _pw.pw_health()
    assert h["ok"] is False
    assert h["mode"] == "not_installed"
    assert "ERR npm 404" in h["stderr_tail"]


# ---------------------------------------------------------------------------
# 4. pw_health ready(模拟握手成功)
# ---------------------------------------------------------------------------

def test_pw_health_ready(monkeypatch):
    """npx --help 返 0,Popen 返 initialize 响应 → mode=ready。"""
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda n: "/usr/bin/" + n)
    import subprocess as _sp
    class _R:
        returncode = 0
        stdout = "Usage: ..."
        stderr = ""
    monkeypatch.setattr(_sp, "run", lambda *a, **kw: _R())
    # mock Popen:返 initialize 响应 + 立刻 EOF
    init_resp = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"protocolVersion": "2025-06-18",
                   "serverInfo": {"name": "playwright-mcp", "version": "0.5"}}
    })
    proc = _FakeProc([init_resp])  # EOF 后 read_loop 退出
    def factory(cmd, **kw):
        return proc
    monkeypatch.setattr(_sp, "Popen", factory)
    from prisir_work import playwright_bridge as _pw
    h = _pw.pw_health()
    assert h["ok"] is True
    assert h["mode"] == "ready"
    assert h["browser"] == "chromium"
    assert h["headless"] is True


# ---------------------------------------------------------------------------
# 5. _JSONRPCClient 请求/响应(mock 子进程 stdin/stdout 协奏)
# ---------------------------------------------------------------------------

def test_jsonrpc_request_ok(monkeypatch):
    """_request(method, params) → 分配 id + 写 stdin + 等 stdout 响应。"""
    import subprocess as _sp
    # 模拟 initialize + tools/call 2 响应
    init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    call_resp = json.dumps({
        "jsonrpc": "2.0", "id": 2,
        "result": {"content": [{"type": "text", "text": "Done"}],
                   "isError": False}})
    proc = _FakeProc([init_resp, call_resp])
    monkeypatch.setattr(_sp, "Popen", lambda cmd, **kw: proc)
    from prisir_work import playwright_bridge as _pw
    c = _pw._JSONRPCClient(["echo", "x"])
    init_r = c.start()
    assert init_r["ok"] is True
    r = c.call_tool("browser_navigate", {"url": "https://x"})
    assert r["ok"] is True
    assert r["content"] == "Done"
    # 验证 stdin 写了 3 行(initialize + notifications/initialized + tools/call)
    writes = proc.stdin.writes
    assert len(writes) == 3
    init_msg = json.loads(writes[0])
    notif_msg = json.loads(writes[1])
    call_msg = json.loads(writes[2])
    assert init_msg["method"] == "initialize"
    assert init_msg["id"] == 1
    assert notif_msg["method"] == "notifications/initialized"
    assert call_msg["method"] == "tools/call"
    assert call_msg["id"] == 2
    assert call_msg["params"]["name"] == "browser_navigate"


# ---------------------------------------------------------------------------
# 6. _request timeout(queue.Empty → ok=False + playwright_call_timeout)
# ---------------------------------------------------------------------------

def test_request_timeout(monkeypatch):
    """子进程不响应 → 30s 后 timeout → ok=False + error。"""
    import subprocess as _sp
    # 给个空 stdout:read_line 立即 None,read_loop 退出 → closed=True
    proc = _FakeProc([])
    monkeypatch.setattr(_sp, "Popen", lambda cmd, **kw: proc)
    from prisir_work import playwright_bridge as _pw
    c = _pw._JSONRPCClient(["echo", "x"], protocol_version="x")
    r = c._request("test", timeout=0.5)
    assert r["ok"] is False
    # 进程没起来 → playwright_not_started(已 close / proc is None)
    assert r["error"] in ("playwright_not_started", "playwright_call_timeout")


# ---------------------------------------------------------------------------
# 7. pw_navigate / pw_click / pw_type / pw_evaluate 走 _client
# ---------------------------------------------------------------------------

def test_pw_navigate_calls_call_tool(monkeypatch):
    """pw_navigate 调 browser_navigate tool。"""
    import subprocess as _sp
    init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    call_resp = json.dumps({
        "jsonrpc": "2.0", "id": 2,
        "result": {"content": [{"type": "text", "text": "Navigated"}],
                   "isError": False}})
    proc = _FakeProc([init_resp, call_resp])
    monkeypatch.setattr(_sp, "Popen", lambda cmd, **kw: proc)
    # 清掉 _singleton(避免前一个测试污染)
    from prisir_work import playwright_bridge as _pw
    _pw._singleton = None
    r = _pw.pw_navigate("https://example.com")
    assert r["ok"] is True
    assert "Navigated" in r["content"]


def test_pw_navigate_bad_url(monkeypatch):
    """pw_navigate 不接 http(s) → error=bad_url,不调子进程。"""
    from prisir_work import playwright_bridge as _pw
    _pw._singleton = None
    r = _pw.pw_navigate("ftp://x")
    assert r["ok"] is False
    assert r["error"] == "bad_url"


def test_pw_navigate_empty(monkeypatch):
    """pw_navigate("") → error=empty_url。"""
    from prisir_work import playwright_bridge as _pw
    _pw._singleton = None
    r = _pw.pw_navigate("")
    assert r["ok"] is False
    assert r["error"] == "empty_url"


def test_pw_click_missing_element_or_ref():
    """pw_click 缺 element/ref → error=missing_element_or_ref。"""
    from prisir_work import playwright_bridge as _pw
    _pw._singleton = None
    r = _pw.pw_click("", "e5")
    assert r["ok"] is False
    assert r["error"] == "missing_element_or_ref"


def test_pw_type_missing_ref():
    """pw_type 缺 ref → error=missing_ref。"""
    from prisir_work import playwright_bridge as _pw
    _pw._singleton = None
    r = _pw.pw_type("hello", "")
    assert r["ok"] is False
    assert r["error"] == "missing_ref"


def test_pw_evaluate_empty_function():
    """pw_evaluate("") → error=empty_function。"""
    from prisir_work import playwright_bridge as _pw
    _pw._singleton = None
    r = _pw.pw_evaluate("")
    assert r["ok"] is False
    assert r["error"] == "empty_function"


# ---------------------------------------------------------------------------
# 8. pw_close → 终止子进程
# ---------------------------------------------------------------------------

def test_pw_close_terminates_process(monkeypatch):
    """pw_close → _singleton.close() 触发 terminate。"""
    import subprocess as _sp
    init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    proc = _FakeProc([init_resp])
    monkeypatch.setattr(_sp, "Popen", lambda cmd, **kw: proc)
    from prisir_work import playwright_bridge as _pw
    _pw._singleton = None
    # 触发 start(走 pw_health 内部路径)
    h = _pw.pw_health()
    # 现在 _pw._singleton 应已被 close()(health 流程最后 close)
    # 真正的 close 测试:
    # 新建一个 client 起子进程
    client = _pw._JSONRPCClient(["echo", "x"])
    client.start()
    r = client.close()
    assert r["ok"] is True
    assert proc._terminated is True


# ---------------------------------------------------------------------------
# 9. _request RPC error 响应 → playwright_rpc_error
# ---------------------------------------------------------------------------

def test_request_rpc_error(monkeypatch):
    """server 返 RPC error → ok=False + playwright_rpc_error。"""
    import subprocess as _sp
    init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})
    err_resp = json.dumps({"jsonrpc": "2.0", "id": 2,
                           "error": {"code": -32601,
                                     "message": "method not found"}})
    proc = _FakeProc([init_resp, err_resp])
    monkeypatch.setattr(_sp, "Popen", lambda cmd, **kw: proc)
    from prisir_work import playwright_bridge as _pw
    c = _pw._JSONRPCClient(["echo", "x"])
    c.start()
    r = c.call_tool("nonexistent_tool", {})
    assert r["ok"] is False
    assert r["error"] == "playwright_rpc_error"
    assert r["rpc_code"] == -32601


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))