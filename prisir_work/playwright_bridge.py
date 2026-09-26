"""playwright_bridge.py — Playwright MCP 子进程桥(2026-09-26 ship,P3j T25)。

复现 Microsoft Playwright MCP 的核心浏览器交互能力,只走子进程 + JSON-RPC 2.0 over stdio:
  · 打开 URL / a11y 树快照 / 点击 / 输入 / JS 执行 / 截图 / 关闭

为什么不直接 pip install playwright:
  · playwright Python 包会拉 Chromium(~200MB)+ 25+ 依赖
  · 我们只需要 LLM 主对话用 7 个高层 API,不需要完整 Playwright API
  · Node.js + npx + @playwright/mcp 子进程 = 版本隔离 + 零额外 Python 依赖
  · 跟 agent_reach_bridge.py / gh_bridge.py 范式一致

JSON-RPC 2.0 over stdio 协议(MCP 2025-06-18):
  · client → server: {"jsonrpc":"2.0","id":N,"method":"initialize","params":{...}}
  · server → client: {"jsonrpc":"2.0","id":N,"result":{...}}
  · 通知:{"method":"notifications/initialized"} 无 id
  · tools/call:{"jsonrpc":"2.0","id":N,"method":"tools/call",
                "params":{"name":"browser_navigate","arguments":{"url":"..."}}}
  · 响应:{"jsonrpc":"2.0","id":N,"result":{
            "content":[{"type":"text","text":"Navigated to ..."}],
            "isError":false}}

设计:
  · 第一次 _client() 才 Popen 子进程(懒启动,避免没装就阻塞)
  · read thread 监听 stdout,按 id 分配响应到 queue.Queue
  · 子进程崩溃 → 下次 call_tool 自动重启一次
  · 默认 headless + browser=chromium
  · 默认 timeout 30s

公开 API(7 个 fn):
  · pw_health()                          → {ok, mode: missing_node/not_installed/ready, ...}
  · pw_navigate(url, *, timeout=30)      → {ok, content: "Navigated to ..."}
  · pw_snapshot(*, depth=3, timeout=30)  → {ok, content: "...a11y tree..."}
  · pw_click(element, ref, *, timeout=30)→ {ok, content: "Clicked ..."}
  · pw_type(text, ref, *, submit=False, slowly=False, timeout=30) → {ok, content: "Typed ..."}
  · pw_evaluate(function, *, timeout=30) → {ok, content: "<JS 返回值>"}
  · pw_screenshot(filename="", *, full_page=False, timeout=30) → {ok, content: "..."}
  · pw_close()                           → {ok, message: "Browser closed"}
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

NPM_PACKAGE = "@playwright/mcp@latest"
NODE_BIN = os.environ.get("PW_NODE_BIN", "node")
NPX_BIN = os.environ.get("PW_NPX_BIN", "npx")
PW_TIMEOUT = 30.0
MCP_PROTOCOL_VERSION = "2025-06-18"

# 启动子进程的固定参数(默认 headless + chromium)
_DEFAULT_CMD = [
    "--headless",
    "--browser", "chromium",
]


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 over stdio 客户端
# ---------------------------------------------------------------------------

class _JSONRPCClient:
    """跟单个 MCP 子进程双向 JSON-RPC 通信。

    线程模型:
      · 主线程:发起 _request,分配 id,阻塞等 queue.Queue 响应
      · read thread:从 stdout 读 JSON 行,按 id 路由到对应 queue

    失败语义:
      · 进程崩溃(read thread EOF)→ _closed=True,下次 call_tool 自动重启
      · 超时 → 返回 ok=False + error=playwright_call_timeout
      · RPC error → 返回 ok=False + error=playwright_rpc_error + rpc_code
    """

    def __init__(self, cmd: list[str], *,
                 protocol_version: str = MCP_PROTOCOL_VERSION):
        self._cmd = list(cmd)
        self._protocol_version = protocol_version
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self._read_thread: threading.Thread | None = None
        self._closed = False
        self._initialized = False
        self._server_info: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """启动子进程 + 启动 read thread + initialize handshake。

        返回 {ok: True, ...server_info} 或 {ok: False, error: ...}
        失败 → 不会留 zombie 进程。
        """
        if self._proc is not None and self._proc.poll() is None:
            return {"ok": True, "already_started": True,
                    **self._server_info}
        # 关掉旧 state(若进程死了但 _proc 没清)
        self._proc = None
        self._closed = False
        self._initialized = False
        self._server_info = {}
        self._pending.clear()

        try:
            self._proc = subprocess.Popen(
                self._cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as e:
            return {"ok": False, "error": "playwright_npx_not_found",
                    "detail": str(e),
                    "hint": f"找不到 {self._cmd[0]};装 Node.js ≥ 18 (https://nodejs.org)"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False,
                    "error": f"playwright_{type(e).__name__}",
                    "detail": str(e)[:300]}

        self._read_thread = threading.Thread(target=self._read_loop,
                                             name="pw-mcp-read",
                                             daemon=True)
        self._read_thread.start()

        return self._initialize()

    def _initialize(self) -> dict[str, Any]:
        """JSON-RPC initialize + notifications/initialized。"""
        r = self._request(
            "initialize",
            {"protocolVersion": self._protocol_version,
             "capabilities": {},
             "clientInfo": {"name": "PrisirAI", "version": "1.0"}},
            timeout=15.0,
        )
        if not r.get("ok"):
            return r
        self._server_info = r.get("result") or {}
        # 通知 initialized(无 id)_send 自己持 _send_lock,这里别再套一层)
        try:
            self._send({"jsonrpc": "2.0",
                        "method": "notifications/initialized"})
        except Exception as e:  # noqa: BLE001
            log.warning("playwright_mcp initialized 通知失败: %s",
                        type(e).__name__)
        self._initialized = True
        return {"ok": True, "initialized": True, **self._server_info}

    def close(self) -> dict[str, Any]:
        """优雅关闭:发 shutdown(best-effort)+ terminate + 兜底 kill。

        shutdown 用 2s 短超时(子进程可能不响应,不阻塞关闭流程)。
        """
        if self._proc is None:
            return {"ok": True, "already_closed": True}
        # best-effort shutdown(2s 短超时,失败立即走 terminate)
        try:
            self._request("shutdown", timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5.0)
        except Exception:  # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._proc = None
        self._initialized = False
        self._closed = True
        # 清理 pending queue(避免悬挂请求)
        for q in self._pending.values():
            try:
                q.put_nowait({"raw": {}, "error":
                              {"code": -1, "message": "closed"}})
            except Exception:  # noqa: BLE001
                pass
        self._pending.clear()
        return {"ok": True, "message": "Browser closed"}

    # ------------------------------------------------------------------
    # read / send / request
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """read thread:从 stdout 读 JSON 行,按 id 路由到 queue。

        EOF 或异常 → _closed=True + 把所有 pending queue 标记失败。
        """
        try:
            assert self._proc is not None
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("playwright_mcp 非 JSON 行: %s", line[:200])
                    continue
                msg_id = msg.get("id")
                if msg_id is None:
                    # notification — 忽略
                    continue
                q = self._pending.pop(int(msg_id), None)
                if q is None:
                    log.warning("playwright_mcp 收到未注册 id=%s 的响应",
                                msg_id)
                    continue
                q.put({"raw": msg,
                       "error": msg.get("error"),
                       "result": msg.get("result")})
        except Exception as e:  # noqa: BLE001
            log.warning("playwright_mcp read loop 异常: %s: %s",
                        type(e).__name__, e)
        finally:
            # EOF → process died → 标记 closed,失败 pending
            self._closed = True
            for q in self._pending.values():
                q.put({"raw": {}, "error": {"code": -1, "message": "EOF"}})
            self._pending.clear()

    def _send(self, msg: dict[str, Any]) -> None:
        """写一行 JSON 到 stdin(必须持 _send_lock)。"""
        with self._send_lock:
            if self._proc is None or self._proc.stdin is None:
                raise RuntimeError("playwright_mcp subprocess 已关闭")
            line = json.dumps(msg, ensure_ascii=False)
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()

    def _request(self, method: str,
                 params: dict[str, Any] | None = None,
                 *, timeout: float = PW_TIMEOUT) -> dict[str, Any]:
        """JSON-RPC 请求/响应:分配 id → 写 stdin → 等 queue 响应。

        返回:
          · 成功:{ok: True, result: <rpc result>}
          · 失败:{ok: False, error: "playwright_xxx", ...}
        """
        if self._closed or self._proc is None or self._proc.poll() is not None:
            return {"ok": False, "error": "playwright_not_started",
                    "hint": "子进程已退出,需要重启(下次 call_tool 自动)"}

        with self._id_lock:
            self._next_id += 1
            rid = self._next_id
            q: queue.Queue = queue.Queue(maxsize=1)
            self._pending[rid] = q

        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            self._send(msg)
        except Exception as e:  # noqa: BLE001
            self._pending.pop(rid, None)
            return {"ok": False,
                    "error": f"playwright_send_{type(e).__name__}",
                    "detail": str(e)[:200]}

        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(rid, None)
            return {"ok": False, "error": "playwright_call_timeout",
                    "method": method, "timeout": timeout}

        if resp.get("error"):
            err = resp["error"]
            return {"ok": False, "error": "playwright_rpc_error",
                    "rpc_code": err.get("code"),
                    "rpc_message": (err.get("message") or "")[:300]}

        return {"ok": True, "result": resp["result"]}

    # ------------------------------------------------------------------
    # 高层: tools/call
    # ------------------------------------------------------------------

    def call_tool(self, name: str,
                  arguments: dict[str, Any] | None = None,
                  *, timeout: float = PW_TIMEOUT) -> dict[str, Any]:
        """调 playwright-mcp 的某个 tool,统一异常处理。

        MCP tools/call 返回 shape:
          {"content": [{"type": "text", "text": "..."}, ...],
           "isError": false}

        返回(成功):
          {"ok": True, "is_error": False, "content": "<拼接 text>",
           "raw": <rpc result>}

        返回(失败):
          {"ok": False, "error": "playwright_xxx", ...}
        """
        # 首次自动 initialize
        if not self._initialized:
            ir = self._initialize()
            if not ir.get("ok"):
                return ir

        r = self._request("tools/call",
                          {"name": name,
                           "arguments": arguments or {}},
                          timeout=timeout)
        if not r.get("ok"):
            return r
        result = r.get("result") or {}
        is_err = bool(result.get("isError"))
        content = result.get("content") or []
        text_parts: list[str] = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                text_parts.append(str(c.get("text", "")))
        text = "\n".join(text_parts).strip()
        return {"ok": not is_err, "is_error": is_err,
                "content": text, "raw": result}


# ---------------------------------------------------------------------------
# Singleton + lazy start
# ---------------------------------------------------------------------------

_singleton: _JSONRPCClient | None = None


def _client() -> _JSONRPCClient:
    """拿全局 singleton client(懒启动 + 死了重启)。"""
    global _singleton
    if _singleton is None:
        _singleton = _JSONRPCClient([NPX_BIN, "-y", NPM_PACKAGE, *_DEFAULT_CMD])
        _singleton.start()
        return _singleton
    # 已存在但死了 → 重启
    if _singleton._closed or _singleton._proc is None or \
            _singleton._proc.poll() is not None:
        try:
            _singleton.close()
        except Exception:  # noqa: BLE001
            pass
        _singleton = _JSONRPCClient(
            [NPX_BIN, "-y", NPM_PACKAGE, *_DEFAULT_CMD])
        _singleton.start()
    return _singleton


# ---------------------------------------------------------------------------
# Health:三档 mode(missing_node / not_installed / ready)
# ---------------------------------------------------------------------------

def pw_health() -> dict[str, Any]:
    """检查 Node + npx + playwright-mcp 可用性。

    不会强制起子进程(若已 close / 报错也只是 mode=start_failed,不会抛栈)。
    """
    node = shutil.which(NODE_BIN)
    if not node:
        return {"ok": False, "mode": "missing_node",
                "hint": "装 Node.js ≥ 18(https://nodejs.org)"}
    npx = shutil.which(NPX_BIN)
    if not npx:
        return {"ok": False, "mode": "missing_npx",
                "node": node,
                "hint": "npx 找不到,检查 Node 安装 PATH"}
    # 试 npx @playwright/mcp --help(看是否能拉到包 + 不立即启动 Chromium)
    try:
        proc = subprocess.run(
            [NPX_BIN, "-y", NPM_PACKAGE, "--help"],
            capture_output=True, text=True, timeout=20.0,
        )
    except FileNotFoundError:
        return {"ok": False, "mode": "missing_npx",
                "hint": "npx 命令找不到"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "mode": "npx_timeout",
                "hint": "npx 调用超时(可能网络慢,首次拉包需 30s+)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mode": "npx_failed",
                "error": type(e).__name__,
                "detail": str(e)[:200]}
    if proc.returncode != 0:
        return {"ok": False, "mode": "not_installed",
                "hint": "npx 拉 @playwright/mcp 失败,检查网络",
                "stderr_tail": (proc.stderr or "")[-300:]}
    # 真启动一次看 handshake 是否成功(轻探:只 initialize,不调 tool)
    try:
        c = _JSONRPCClient([NPX_BIN, "-y", NPM_PACKAGE, *_DEFAULT_CMD])
        init_r = c.start()
        if not init_r.get("ok"):
            return {"ok": False, "mode": "start_failed",
                    "error": init_r.get("error", ""),
                    "detail": init_r.get("detail", "")[:200]}
        # health 检测成功 → 立即关掉(不占资源,call_tool 才真起)
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "mode": "ready",
                "node": node, "npx": npx,
                "browser": "chromium", "headless": True,
                "protocol_version": MCP_PROTOCOL_VERSION,
                "server_info": init_r}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mode": "start_failed",
                "error": type(e).__name__, "detail": str(e)[:200]}


# ---------------------------------------------------------------------------
# 7 个公开 API(高层)
# ---------------------------------------------------------------------------

def pw_navigate(url: str, *,
                timeout: float = PW_TIMEOUT) -> dict[str, Any]:
    """打开 URL(browser_navigate)。

    成功:{"ok": True, "is_error": False, "content": "Navigated to ..."}
    失败:{"ok": False, "error": "playwright_xxx", ...}
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "empty_url"}
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "bad_url",
                "hint": "URL 必须 http(s) 开头"}
    return _client().call_tool("browser_navigate",
                               {"url": url}, timeout=timeout)


def pw_snapshot(*, depth: int = 3,
                timeout: float = PW_TIMEOUT) -> dict[str, Any]:
    """取 a11y 树(browser_snapshot)— LLM 用来找元素 ref。

    返回的 content 是大段 a11y 树,含 eN 引用,可直接喂 LLM 找 ref。
    """
    d = max(1, min(int(depth), 8))
    return _client().call_tool("browser_snapshot",
                               {"depth": d}, timeout=timeout)


def pw_click(element: str, ref: str, *,
             timeout: float = PW_TIMEOUT) -> dict[str, Any]:
    """点元素(browser_click)— L1 副作用,前端弹确认卡。

    element:人读描述(如 "登录按钮")
    ref:browser_snapshot 树里的 eN 引用
    """
    element = (element or "").strip()
    ref = (ref or "").strip()
    if not element or not ref:
        return {"ok": False, "error": "missing_element_or_ref",
                "hint": "element(人读描述) + ref(snapshot eN) 都必填"}
    return _client().call_tool("browser_click",
                               {"element": element, "ref": ref},
                               timeout=timeout)


def pw_type(text: str, ref: str, *,
            submit: bool = False, slowly: bool = False,
            timeout: float = PW_TIMEOUT) -> dict[str, Any]:
    """在 ref 输入框打字(browser_type)— L1 副作用,前端弹确认卡。

    submit:打完后按 Enter
    slowly:逐字符慢打(防反爬)
    """
    text = text or ""
    ref = (ref or "").strip()
    if not ref:
        return {"ok": False, "error": "missing_ref",
                "hint": "ref(snapshot eN) 必填"}
    return _client().call_tool("browser_type",
                               {"element": "input", "ref": ref,
                                "text": text,
                                "submit": bool(submit),
                                "slowly": bool(slowly)},
                               timeout=timeout)


def pw_evaluate(function: str, *,
                timeout: float = PW_TIMEOUT) -> dict[str, Any]:
    """在页面执行 JS(browser_evaluate)— L0 只读(虽然功能上能改 DOM,但默认 L0)。

    function:JS 函数体(末尾自动 () 调用),返序列化值。
    """
    function = (function or "").strip()
    if not function:
        return {"ok": False, "error": "empty_function"}
    return _client().call_tool("browser_evaluate",
                               {"function": function}, timeout=timeout)


def pw_screenshot(filename: str = "", *,
                  full_page: bool = False,
                  timeout: float = PW_TIMEOUT) -> dict[str, Any]:
    """截图(browser_take_screenshot)— L0 只读。

    filename 留空 → MCP 自动命名;full_page → 整页截图。
    返回 content 是「Saved to <path>」之类的状态字符串。
    """
    args: dict[str, Any] = {}
    if filename:
        args["filename"] = filename.strip()
    if full_page:
        args["fullPage"] = True
    return _client().call_tool("browser_take_screenshot", args,
                               timeout=timeout)


def pw_close() -> dict[str, Any]:
    """关浏览器 + 终止子进程。"""
    global _singleton
    if _singleton is None:
        return {"ok": True, "already_closed": True}
    r = _singleton.close()
    _singleton = None
    return r


# ---------------------------------------------------------------------------
# CLI 自检(开发/调试用)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    import sys as _sys
    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "health"
    if cmd == "health":
        print(_json.dumps(pw_health(), ensure_ascii=False, indent=2))
    elif cmd == "navigate":
        url = _sys.argv[2] if len(_sys.argv) > 2 else "about:blank"
        print(_json.dumps(pw_navigate(url), ensure_ascii=False, indent=2))
    elif cmd == "snapshot":
        print(_json.dumps(pw_snapshot(), ensure_ascii=False, indent=2))
    elif cmd == "close":
        print(_json.dumps(pw_close(), ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd} (use health/navigate/snapshot/close)")