"""obscura_tools — OIagent 侧 Obscura 浏览器 MCP 暴露

设计:
- 启动时 spawn stdio binary(obscura-mcp-stdio.exe),通过 stdin/stdout 走
  newline-delimited JSON-RPC,跟 Obscura 同一份 schema
- `tools/list` 一次性 introspection,生成 TOOL_DEFS 给 dynamic_registry
- `tools/call` 在 HANDLERS 里 proxy 转发到 stdio binary
- binary 路径默认 `C:/Users/Administrator/obscura-build/obscura-mcp-stdio/target/release/obscura-mcp-stdio.exe`,
  可通过环境变量 `OBSCURA_STDIO_PATH` 覆盖

跟 oiagent 解耦:动态发现,启动失败 → 报错给上层,不影响其他 tools
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_BINARY = Path(
    r"C:\Users\Administrator\obscura-build\obscura-mcp-stdio\target\release\obscura-mcp-stdio.exe"
)
BINARY_PATH = Path(os.environ.get("OBSCURA_STDIO_PATH", str(DEFAULT_BINARY)))
REQUEST_TIMEOUT_S = 60


class ObscuraClient:
    """Spawn the obscura-mcp-stdio binary, send JSON-RPC, get JSON-RPC responses.

    Thread-safe via a lock — multiple concurrent tool calls serialize. For
    single-process OIagent usage that's fine; Obscura's lib internally keeps
    tab state, so concurrent calls in the same process would race anyway.
    """

    def __init__(self, binary_path: Path = BINARY_PATH, stealth: bool = False):
        self.binary_path = binary_path
        self.stealth = stealth
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self._msg_id = 0

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if not self.binary_path.exists():
            raise FileNotFoundError(
                f"obscura-mcp-stdio binary not found: {self.binary_path}. "
                f"Build it with `cargo build --release` in "
                f"C:/Users/Administrator/obscura-build/obscura-mcp-stdio/."
            )
        args = [str(self.binary_path)]
        if self.stealth:
            args.append("--stealth")
        # bufsize=0 → unbuffered, newline mode for line-delimited JSON-RPC
        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            text=True,
            encoding="utf-8",
        )

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def call(self, method: str, params: dict | None = None, timeout_s: int = REQUEST_TIMEOUT_S) -> dict:
        with self.lock:
            self.start()
            assert self.proc and self.proc.stdin and self.proc.stdout
            msg_id = self._next_id()
            req = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
            line = json.dumps(req, ensure_ascii=False) + "\n"
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except BrokenPipeError:
                self.proc = None
                raise RuntimeError("obscura-mcp-stdio stdin closed (binary died?)")

            # Read one line; if binary has noise on stdout we trust the protocol
            # (one-message-per-line). If a call returns no obvious line within
            # timeout, raise.
            deadline = time.time() + timeout_s
            # The underlying reader is line-buffered; for long responses a
            # single call may still take up to timeout_s.
            while time.time() < deadline:
                line_out = self.proc.stdout.readline()
                if not line_out:
                    self.proc = None
                    raise RuntimeError("obscura-mcp-stdio closed stdout unexpectedly")
                line_out = line_out.strip()
                if not line_out:
                    continue
                try:
                    resp = json.loads(line_out)
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == msg_id:
                    return resp
            raise TimeoutError(f"obscura-mcp-stdio {method} timed out after {timeout_s}s")


# ── Singleton client ─────────────────────────────────────────────────
_CLIENT: ObscuraClient | None = None
_CLIENT_LOCK = threading.Lock()


def _client() -> ObscuraClient:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = ObscuraClient()
    return _CLIENT


def _err(stage: str, error: str) -> str:
    return json.dumps(
        {"ok": False, "stage": stage, "error": error},
        ensure_ascii=False,
    )


# ── Introspection at module-load time ─────────────────────────────────
def _fetch_tools() -> tuple[list[dict], dict[str, Any]]:
    """Call stdio binary's tools/list once and turn each entry into MCP shape.

    Returns (TOOL_DEFS-list, HANDLERS-dict). HANDLERS keys are tool names
    (as declared by the binary's tools/list); each handler is a closure that
    proxies `tools/call` to the binary.
    """
    cli = _client()
    try:
        resp = cli.call("tools/list", timeout_s=30)
    except Exception as exc:
        return [], {"_init_error": str(exc)}

    if "error" in resp:
        return [], {"_init_error": json.dumps(resp["error"], ensure_ascii=False)}

    tools = (resp.get("result") or {}).get("tools") or []
    defs: list[dict] = []
    handlers: dict[str, Any] = {}
    for t in tools:
        name = t.get("name")
        if not name:
            continue
        defs.append(
            {
                "name": name,
                "description": t.get("description") or f"Obscura tool: {name}",
                "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}},
            }
        )

        def _make_handler(n: str):
            def _handler(**kwargs) -> str:
                try:
                    resp = _client().call("tools/call", {"name": n, "arguments": kwargs})
                except Exception as exc:
                    return _err(f"call:{n}", str(exc))
                if "error" in resp and resp["error"] is not None:
                    return _err(f"call:{n}", json.dumps(resp["error"], ensure_ascii=False))
                return json.dumps({"ok": True, "result": resp.get("result")}, ensure_ascii=False)
            _handler.__name__ = f"obscura_{n}"
            return _handler

        handlers[name] = _make_handler(name)
    return defs, handlers


_TOOL_DEFS, _HANDLERS = _fetch_tools()


# ── Tool descriptions published to dynamic_registry ──────────────────
TOOL_DEFS: list[dict] = _TOOL_DEFS
HANDLERS: dict[str, Any] = {k: v for k, v in _HANDLERS.items() if not k.startswith("_")}

if not TOOL_DEFS:
    _init_msg = _HANDLERS.get("_init_error", "unknown")
    _placeholder = {
        "name": "obscura_unavailable",
        "description": (
            f"obscura-mcp-stdio binary not reachable ({BINARY_PATH}). "
            f"Build it via `cargo build --release` in "
            f"C:/Users/Administrator/obscura-build/obscura-mcp-stdio. "
            f"Error: {_init_msg}"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    }
    TOOL_DEFS = [_placeholder]
    HANDLERS = {
        "obscura_unavailable": lambda **_: _err(
            "init", f"obscura-mcp-stdio unavailable: {_init_msg}"
        ),
    }


if __name__ == "__main__":
    # Smoke test: list discovered tools.
    print(json.dumps(
        {"binary": str(BINARY_PATH), "exists": BINARY_PATH.exists(),
         "tool_count": len(TOOL_DEFS),
         "tools": [d["name"] for d in TOOL_DEFS]},
        ensure_ascii=False, indent=2,
    ))
