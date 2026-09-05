"""mcp_client_bridge.py — Prisiragent 的 MCP client 桥(路线 B)

v0.45 把本地起的第三方 MCP server 的工具并入 Prisiragent 工具体系。

架构要点:
- Prisiragent daemon 是同步架构(ThreadingHTTPServer + threading),
  而官方 mcp python sdk 的 ClientSession 是 async(anyio)。
- 桥的做法:常驻一个后台 asyncio event loop 线程,所有 async MCP 调用
  用 asyncio.run_coroutine_threadsafe 提交过去,包成同步接口给 daemon 用。
  不碰 daemon 的同步主流程。

配置: ~/.local/share/aureon/etc/mcp_servers.json
  [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/allowed/dir"],
      "env": {"FOO": "bar"},          # 可选
      "enabled": true                  # 可选,默认 true
    }
  ]

工具命名: mcp__<server>__<tool>  — 前缀防与内置/用户工具冲突,也标识来源。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("mcp_oiagent.mcp_client_bridge")

# 配置路径(与 daemon 的 TOOLS_CONFIG 同目录)
MCP_SERVERS_CONFIG = (
    Path.home() / ".local" / "share" / "aureon" / "etc" / "mcp_servers.json"
)

# 官方 mcp sdk(已确认本机安装)
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    _MCP_AVAILABLE = True
except Exception as _e:  # pragma: no cover
    log.warning(f"官方 mcp sdk 不可用,MCP 桥禁用: {_e}")
    _MCP_AVAILABLE = False


# ─────────────────────────────────────────────────
# 专属 event loop(每连接一个)— 保证 stdio 子进程管道的线程亲和性
# ─────────────────────────────────────────────────
# 关键教训(两次实测踩坑):
# 1) Windows ProactorEventLoop 下,MCP stdio 子进程管道绑定创建它的 loop。
# 2) 更致命:stdio_client / ClientSession 是 anyio task group,其内部流读取跑在
#    session 的 task 里。若 connect 在一个 future、list_tools 在另一个 future
#    (跨 run_coroutine_threadsafe 边界),底层流的 task 已断 → 第二次调用永久卡死。
#
# 正确做法(actor 模型):每个连接是一个跑在专属 loop 线程里的**长跑协程**,
# 从 asyncio.Queue 取调用请求、在 session 存活的同一 task 上下文里执行、把结果
# 放进响应 future。整个 session 生命周期(connect→所有调用)都在同一 task 内,
# 不跨 future 边界。跨线程同步侧用 queue + 线程事件收结果。


class _ConnLoop:
    """一个连接专属的常驻 event loop 线程。

    支持 boot_coro:loop 线程内**先 run_until_complete(boot) 再 run_forever**。
    这是诊断验证可行的关键结构 — 队列/actor 必须在 run_forever 之前、
    同一 loop 上下文里建好,否则 run_coroutine_threadsafe 提交的 boot 与
    actor 首次调度存在竞态(实测卡死)。
    """

    def __init__(self, name: str):
        self.name = name
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._boot_coro = None

    def start(self, boot_coro=None) -> None:
        self._boot_coro = boot_coro

        def _run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            if self._boot_coro is not None:
                self.loop.run_until_complete(self._boot_coro)
            self._ready.set()
            self.loop.run_forever()

        self._thread = threading.Thread(
            target=_run, name=f"mcp-conn-{self.name}", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=15.0)

    def submit(self, coro) -> "asyncio.Future":
        """把协程提交到本 loop(不等待),返回 concurrent.futures.Future。"""
        if self.loop is None:
            raise RuntimeError(f"conn loop {self.name} 未启动")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


# ─────────────────────────────────────────────────
# 单个 MCP server 的连接(actor 模型:session 跑在一个长跑协程里)
# ─────────────────────────────────────────────────
class MCPServerConn:
    """一个本地 MCP server 的 stdio 连接。

    session 生命周期托管给一个跑在专属 loop 里的 actor 协程;
    同步侧通过 (请求队列 + 响应 future) 与之通信,不跨 future 复用 session。
    """

    def __init__(self, name: str, command: str, args: list[str],
                 env: dict[str, str] | None = None):
        self.name = name
        self.command = command
        self.args = args
        self.env = env or {}
        self._tools: list[dict] = []
        # RLock(可重入):list_tools/call_tool 拿锁后会调 connect(),connect 也拿锁,
        # 普通 Lock 同线程不可重入会死锁(实测卡死的真正根因)。
        self._lock = threading.RLock()
        self.last_error: str = ""
        self._loop = _ConnLoop(name)
        self._req_q: asyncio.Queue | None = None      # (op, payload, loop-side fut)
        self._actor_started = False
        self._connected_evt = threading.Event()        # 同步侧等 actor 完成 initialize

    async def _actor(self):
        """长跑协程:建立 session,然后循环处理请求队列。"""
        try:
            params = StdioServerParameters(
                command=self.command,
                args=self.args,
                env={**os.environ, **self.env},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session_ok = True
                    self._connected_evt.set()          # 通知同步侧:已连接
                    # 请求处理循环
                    while True:
                        item = await self._req_q.get()
                        if item is None:               # 关闭信号
                            break
                        op, payload, fut = item
                        try:
                            if op == "list_tools":
                                resp = await session.list_tools()
                                fut.set_result(resp.tools)
                            elif op == "call_tool":
                                tool, arguments = payload
                                resp = await session.call_tool(tool, arguments)
                                fut.set_result(resp)
                            else:
                                fut.set_exception(RuntimeError(f"未知 op: {op}"))
                        except Exception as e:
                            fut.set_exception(e)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            self._connected_evt.set()                  # 即使失败也要解开等待
            log.warning(f"MCP actor {self.name} 异常退出: {self.last_error}")

    def _start_actor(self):
        if self._actor_started:
            return
        # 在 loop 线程内先 run_until_complete(boot) 建队列+起 actor,再 run_forever
        async def _boot():
            self._req_q = asyncio.Queue()
            asyncio.ensure_future(self._actor())
        self._loop.start(boot_coro=_boot())
        self._actor_started = True

    def _call_actor(self, op: str, payload, timeout: float) -> Any:
        """同步侧:往 actor 队列放一个请求,跨线程等结果。

        用诊断验证过的模式:在单个 enqueue 协程里 put 请求 + await loop-future 收结果,
        整体经 run_coroutine_threadsafe 提交,同步侧等 concurrent future。
        """
        self._start_actor()

        async def _enqueue():
            loop_fut = asyncio.get_event_loop().create_future()
            await self._req_q.put((op, payload, loop_fut))
            return await loop_fut

        return self._loop.submit(_enqueue()).result(timeout=timeout)

    # ── 同步公开接口 ──
    def connect(self) -> bool:
        with self._lock:
            if self._connected_evt.is_set() and not self.last_error:
                return True
            self.last_error = ""
            self._start_actor()
            # 等 actor 完成 initialize(或失败)
            ok = self._connected_evt.wait(timeout=60.0)
            return ok and not self.last_error

    def list_tools(self) -> list[dict]:
        with self._lock:
            if not self.connect():
                return []
            try:
                tools = self._call_actor("list_tools", None, timeout=30.0)
                out = []
                for t in tools:
                    out.append({
                        "name": f"mcp__{self.name}__{t.name}",
                        "description": f"[MCP:{self.name}] {t.description or ''}",
                        "parameters": t.inputSchema or {"type": "object", "properties": {}},
                        "_mcp_server": self.name,
                        "_mcp_tool": t.name,
                    })
                self._tools = out
                return out
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning(f"MCP server {self.name} list_tools 失败: {self.last_error}")
                return []

    def call_tool(self, tool: str, arguments: dict, timeout: float = 60.0) -> dict:
        with self._lock:
            if not self.connect():
                return {"ok": False, "error": f"MCP server {self.name} 未连接: {self.last_error}"}
            try:
                resp = self._call_actor("call_tool", (tool, arguments), timeout=timeout)
                parts = []
                for block in resp.content or []:
                    text = getattr(block, "text", None)
                    parts.append(text if text is not None else str(block))
                is_error = getattr(resp, "isError", False)
                return {"ok": not is_error, "output": "\n".join(parts), "is_error": is_error}
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                self._connected_evt.clear()  # 可能已断,下次重连
                return {"ok": False, "error": f"MCP call_tool {self.name}/{tool} 失败: {self.last_error}"}


# ─────────────────────────────────────────────────
# 桥管理器:加载配置、管理所有 server 连接、路由工具调用
# ─────────────────────────────────────────────────
class MCPBridge:
    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or MCP_SERVERS_CONFIG
        self._servers: dict[str, MCPServerConn] = {}
        self._tool_index: dict[str, tuple[str, str]] = {}  # 全名 → (server, 原 tool 名)
        self._loaded = False
        self._load_lock = threading.Lock()

    def _load_config(self) -> list[dict]:
        if not self.config_path.exists():
            return []
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "servers" in data:
                data = data["servers"]
            return [s for s in data if isinstance(s, dict) and s.get("enabled", True)]
        except Exception as e:
            log.warning(f"加载 {self.config_path} 失败: {e}")
            return []

    def load(self) -> None:
        """加载配置并建立 server 连接(只连接,list_tools 按需)。"""
        with self._load_lock:
            if self._loaded:
                return
            if not _MCP_AVAILABLE:
                log.warning("MCP sdk 不可用,桥不加载")
                self._loaded = True
                return
            for spec in self._load_config():
                name = spec.get("name")
                command = spec.get("command")
                args = spec.get("args", [])
                env = spec.get("env", {})
                if not name or not command:
                    log.warning(f"MCP server 配置缺 name/command,跳过: {spec}")
                    continue
                self._servers[name] = MCPServerConn(name, command, args, env)
            self._loaded = True
            if self._servers:
                log.info(f"MCP 桥加载 {len(self._servers)} 个 server: {list(self._servers)}")

    def refresh_tools(self) -> list[dict]:
        """拉取所有 server 的工具并更新索引。返回 Prisiragent 格式工具列表。"""
        self.load()
        all_tools: list[dict] = []
        self._tool_index.clear()
        for name, conn in self._servers.items():
            for t in conn.list_tools():
                full = t["name"]
                self._tool_index[full] = (name, t["_mcp_tool"])
                # 去掉内部字段再给上层
                all_tools.append({k: v for k, v in t.items() if not k.startswith("_mcp_")})
        return all_tools

    def is_mcp_tool(self, tool_name: str) -> bool:
        return tool_name.startswith("mcp__")

    def call(self, tool_name: str, arguments: dict, timeout: float = 60.0) -> dict:
        """把全名(mcp__server__tool)路由到对应 server 执行。"""
        self.load()
        if tool_name not in self._tool_index:
            # 索引可能未刷新,尝试刷新一次
            self.refresh_tools()
        if tool_name not in self._tool_index:
            return {"ok": False, "error": f"未知 MCP 工具: {tool_name}(桥未加载或无此工具)"}
        server, orig_tool = self._tool_index[tool_name]
        conn = self._servers.get(server)
        if conn is None:
            return {"ok": False, "error": f"MCP server {server} 未配置"}
        return conn.call_tool(orig_tool, arguments, timeout=timeout)

    def server_status(self) -> dict:
        """供状态查询:各 server 连接状态 + 工具数 + 最近错误。"""
        self.load()
        return {
            name: {
                "connected": conn._connected,
                "tools": len(conn._tools),
                "last_error": conn.last_error,
                "command": f"{conn.command} {' '.join(conn.args)}",
            }
            for name, conn in self._servers.items()
        }


# ─────────────────────────────────────────────────
# 全局单例
# ─────────────────────────────────────────────────
_bridge: MCPBridge | None = None
_bridge_lock = threading.Lock()


def get_bridge() -> MCPBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = MCPBridge()
        return _bridge


def get_mcp_tools() -> list[dict]:
    """给 daemon get_all_tools 用:返回所有 MCP server 的工具(Prisiragent schema)。"""
    if not _MCP_AVAILABLE:
        return []
    try:
        return get_bridge().refresh_tools()
    except Exception as e:
        log.warning(f"get_mcp_tools 失败: {e}")
        return []


def is_mcp_tool(tool_name: str) -> bool:
    return tool_name.startswith("mcp__")


def call_mcp_tool(tool_name: str, arguments: dict, timeout: float = 60.0) -> dict:
    """给 daemon dispatch 用:执行一个 MCP 工具。"""
    try:
        return get_bridge().call(tool_name, arguments, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": f"MCP 桥异常: {type(e).__name__}: {e}"}
