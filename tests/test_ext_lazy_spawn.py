"""
test_ext_lazy_spawn.py — P3j T24 user 反馈:「找不到 free-for-dev 扩展的实际路径」修复验证

bug: `_ext_rpc_call` 见 `_EXT_PROCS` 缺失就 ext_not_running,不尝试 spawn。
    L0 资源扩展用户没手动 enable 时,主对话 EXEC 一调就 fail,LLM 答「找不到资源」。

修: `_ext_rpc_call` 内插 lazy-spawn 段,ext 缺失/死进程时先 `_ext_spawn`,再走原 RPC。

测试设计:用 fake proc 模拟 Node ext。fake proc 的 stdin.write 是**同步 callback**:
   - `_ext_rpc_call` 写请求到 stdin → callback 立刻把 response 填进 pending box + ev.set
   - **不需要** reader thread / select / 异步 driver,Windows pipe 的 select 不可靠
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import unittest
from typing import Optional
from unittest import mock as _umock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# NTFS + Python 大小写敏感: `prisIragent_web.py` 不能直接 import,需先 import 兼容垫片
import prisir_case_compat  # noqa: F401, PLC0415


# ── fake proc ───────────────────────────────────────────────
class _FakeProc:
    """同步 fake: stdin.write 立刻触发 on_write callback,无 reader 线程,无 select。"""

    def __init__(self, response: dict, alive: bool = True) -> None:
        self._stdin_r, self._stdin_w = os.pipe()
        self._stdout = _FakeStdout()
        self._stderr = io.BytesIO()
        self._returncode: Optional[int] = None
        self._poll_lock = threading.Lock()
        self._alive = alive
        self._response = response
        # on_write 在 stdin.write 时同步触发:典型用法是 callback 内写 box + ev.set
        self._on_write: Optional[callable] = None

    def set_on_write(self, cb) -> None:
        self._on_write = cb

    def mark_dead(self, code: int = 1) -> None:
        with self._poll_lock:
            self._alive = False
            self._returncode = code

    def poll(self) -> Optional[int]:
        with self._poll_lock:
            return self._returncode

    def kill(self, *args, **kwargs):
        with self._poll_lock:
            self._alive = False
            self._returncode = -9
        try:
            os.close(self._stdin_w)
        except OSError:
            pass

    @property
    def stdin(self):
        return _FakeStdinProxy(self._stdin_w, self._on_write)

    @property
    def stdout(self):
        return self._stdout

    @property
    def stderr(self):
        return self._stderr


class _FakeStdinProxy:
    def __init__(self, fd: int, on_write) -> None:
        self._fd = fd
        self._on_write = on_write

    def write(self, data: bytes) -> None:
        os.write(self._fd, data)
        if self._on_write is not None:
            try:
                self._on_write(data)
            except Exception:  # noqa: BLE001
                pass

    def flush(self) -> None:
        pass


class _FakeStdout:
    """unused(走 callback 路径),保留以兼容 Popen 接口。"""

    def __init__(self) -> None:
        self._buf = b""
        self._cv = threading.Condition()

    def readline(self) -> bytes:
        with self._cv:
            self._cv.wait(timeout=0.5)
        return b""


# ── helper: 给 fake proc 装同步响应 ──────────────────────
def _wire_sync_response(proc: "_FakeProc", W, ext_id: str) -> None:
    """proc.stdin.write 同步触发:模拟 _ext_reader_loop L411 `box["result"] = msg.get("result")`。
    fake proc 的 _response 字段是『node ext 会发的原始 JSON』,我们取它的 result 子字段写 box。
    """
    def _on_write(data: bytes) -> None:
        try:
            req = json.loads(data.decode("utf-8"))
        except Exception:
            return
        rid = req.get("id")
        if not rid:
            return
        st = W._EXT_PROCS.get(ext_id)
        if not st:
            return
        pend = st["pending"].get(rid)
        if not pend:
            return
        ev, box = pend
        # 镜像 _ext_reader_loop L411: box["result"] = msg.get("result")
        msg_result = proc._response.get("result")
        if msg_result is None:
            # 兼容测试直接传 payload 的写法(没包 jsonrpc)
            msg_result = proc._response
        box["result"] = msg_result
        ev.set()
    proc.set_on_write(_on_write)


# ── test class ──────────────────────────────────────────────
class TestExtLazySpawn(unittest.TestCase):

    def setUp(self) -> None:
        # 重新 import 确保干净模块状态。
        # NTFS case-folding + Python 严格匹配:别 test (test_free_for_dev_capabilities)
        # 直接 `sys.modules['prisiragent_web'] = MagicMock(...)` 桩整个 module,我们得绕过。
        # 办法: 强制 del 两个 case 形式的 sys.modules key + reload prisir_case_compat 让 alias 重装。
        for k in list(sys.modules):
            kl = k.lower()
            if kl in ("prisIragent_web", "prisiragent_web"):
                del sys.modules[k]
        import importlib as _il
        _il.reload(_il.import_module("prisir_case_compat"))
        import prisiragent_web as W  # type: ignore[import-not-found]
        self.W = W
        W._EXT_PROCS.clear()
        # 保险兜底:再次确保是函数不是 mock
        if isinstance(W._ext_rpc_call, _umock.MagicMock):
            raise RuntimeError("W._ext_rpc_call still polluted: %r" % (W._ext_rpc_call,))
        # stub 掉 reader_loop —— 测试用同步 on_write 替代
        self._real_reader_loop = W._ext_reader_loop
        W._ext_reader_loop = lambda ext_id: None  # type: ignore[assignment]

        # 建 extensions/free-for-dev-promo/index.js 占位
        ext_root = os.path.join(ROOT, "extensions")
        self._fake_id = "free-for-dev-promo"
        self._entry_dir = os.path.join(ext_root, self._fake_id)
        os.makedirs(self._entry_dir, exist_ok=True)
        self._entry_path = os.path.join(self._entry_dir, "index.js")
        with open(self._entry_path, "w", encoding="utf-8") as f:
            f.write("// fake entry for lazy-spawn test\n")

    def tearDown(self) -> None:
        # 还原 reader_loop + _ext_rpc_call 防止污染其他测试
        if hasattr(self, "_real_reader_loop"):
            self.W._ext_reader_loop = self._real_reader_loop  # type: ignore[assignment]
        # kill 所有 fake proc
        for st in list(self.W._EXT_PROCS.values()):
            try:
                p = st.get("proc")
                if p and hasattr(p, "kill"):
                    p.kill()
            except Exception:
                pass
        self.W._EXT_PROCS.clear()
        # 清理 fake entry
        try:
            if os.path.exists(self._entry_path):
                os.unlink(self._entry_path)
        except OSError:
            pass

    def _make_fake_spawn(self, response: dict):
        """返一个 _fake_spawn 函数:它会建 alive fake proc + 装同步响应 + 注册 _EXT_PROCS。"""
        W = self.W
        ext_id = self._fake_id

        def _fake_spawn(eid):
            p = _FakeProc(response, alive=True)
            _wire_sync_response(p, W, ext_id)
            home = W._ext_home(ext_id)
            W._EXT_PROCS[ext_id] = {
                "proc": p, "home": home, "pending": {},
                "last_alive_at": 0, "crash_count": 0,
                "reader_thread": None, "enabled": True,
            }
        return _fake_spawn

    # ── 1. happy path:missing → lazy-spawn → 真 RPC ──────────
    def test_1_lazy_spawn_when_proc_missing(self) -> None:
        W = self.W
        assert self._fake_id not in W._EXT_PROCS

        with _umock.patch.object(
            W, "_ext_spawn",
            side_effect=self._make_fake_spawn(
                {"result": {"ok": True, "items": [{"name": "Supabase"}]}}
            ),
        ):
            rv = W._ext_rpc_call(self._fake_id, "free.find",
                                 {"query": "postgres"}, timeout=2.0)

        assert "result" in rv, f"expected result, got {rv}"
        assert rv["result"]["ok"] is True
        assert rv["result"]["items"][0]["name"] == "Supabase"
        assert self._fake_id in W._EXT_PROCS

    # ── 2. 入口不存在 → 不 spawn,返 ext_not_running ────────────
    def test_2_lazy_spawn_entry_not_found(self) -> None:
        W = self.W
        bogus_id = "does-not-exist-ext-zzz"
        with _umock.patch.object(W, "_ext_spawn") as spawn_mock:
            rv = W._ext_rpc_call(bogus_id, "x.find", {}, timeout=2.0)
        assert rv == {"error": f"ext_not_running: {bogus_id}"}
        spawn_mock.assert_not_called()
        assert bogus_id not in W._EXT_PROCS

    # ── 3. spawn 抛异常 → graceful ext_not_running ────────────
    def test_3_lazy_spawn_spawn_exception(self) -> None:
        W = self.W
        with _umock.patch.object(W, "_ext_spawn",
                                  side_effect=OSError("node.exe not found")):
            rv = W._ext_rpc_call(self._fake_id, "free.find",
                                 {"query": "postgres"}, timeout=2.0)
        assert rv == {"error": f"ext_not_running: {self._fake_id}"}
        assert self._fake_id not in W._EXT_PROCS

    # ── 4. 已 alive → 不再 lazy spawn ────────────────────────
    def test_4_no_double_spawn_when_alive(self) -> None:
        W = self.W
        existing = _FakeProc({"result": {"ok": True, "marker": "A"}}, alive=True)
        _wire_sync_response(existing, W, self._fake_id)
        W._EXT_PROCS[self._fake_id] = {
            "proc": existing, "home": "x", "pending": {},
            "last_alive_at": 0, "crash_count": 0,
            "reader_thread": None, "enabled": True,
        }

        with _umock.patch.object(W, "_ext_spawn") as spawn_mock:
            rv = W._ext_rpc_call(self._fake_id, "free.find",
                                 {"query": "postgres"}, timeout=2.0)

        assert "result" in rv
        assert rv["result"]["marker"] == "A"
        spawn_mock.assert_not_called()
        assert W._EXT_PROCS[self._fake_id]["proc"] is existing

    # ── 5. 已 dead → lazy-spawn 替代 ────────────────────────
    def test_5_lazy_spawn_after_crash(self) -> None:
        W = self.W
        dead = _FakeProc({}, alive=True)
        dead.mark_dead(code=1)  # 标记为已退出
        W._EXT_PROCS[self._fake_id] = {
            "proc": dead, "home": "x", "pending": {},
            "last_alive_at": 0, "crash_count": 1,
            "reader_thread": None, "enabled": True,
        }

        with _umock.patch.object(
            W, "_ext_spawn",
            side_effect=self._make_fake_spawn(
                {"result": {"ok": True, "marker": "B"}}
            ),
        ):
            rv = W._ext_rpc_call(self._fake_id, "free.find",
                                 {"query": "postgres"}, timeout=2.0)

        assert "result" in rv
        assert rv["result"]["marker"] == "B"
        assert W._EXT_PROCS[self._fake_id]["proc"] is not dead


if __name__ == "__main__":
    unittest.main(verbosity=2)