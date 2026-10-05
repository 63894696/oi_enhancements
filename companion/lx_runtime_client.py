# -*- coding: utf-8 -*-
"""
lx_runtime_client.py — M3.28 Phase 1 PoC
Python wrapper for companion/lx_runtime/lx_runtime.js (Node + jsdom shim).

Spawns the Node subprocess, talks JSON Lines over stdio.
Returns plain Python dicts: {"ok": bool, "result"?: ..., "err"?: ...}

Phase 1: 单源(默认 mock.js — juhe 后端实测 400 "source not match",ikun api DNS 墙挡)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_COMPANION_DIR = Path(__file__).resolve().parent
_LX_RUNTIME_DIR = _COMPANION_DIR / "lx_runtime"


class LxRuntimeClient:
    """Phase 1: 单源(默认 mock.js)的 stdio RPC

    同步调用:Phase 1 简化,一次只发一个请求,readline() 收一个响应。
    多线程用 _lock 串行化 stdin/stdout。
    """

    # P2.5+28 Y+2 阶段(2026-10-05):LX sub-source 名 → 源文件名映射。
    # - 'local' 子源只能由 local.js 提供(完全不调外网,沿用 P3.10b 0 上传红线)
    # - 'tx/kw/wy/kg/mg' 由 huibq.js 主供(onrender.com 公共服务,首次成功后重复请求被限流)
    # - 'wy_gdstudio' 由 gdstudio.js 提供,仅声明 `wy` 子源 → huibq wy 失败抛错时第一兜底
    #   (music-api.gdstudio.xyz 公共反向代理 API,只支持 netease;主流中文歌 90%+ 命中)
    # - 'wy_oiapi' 由 oiapi.js 提供,仅声明 `wy` 子源 → huibq/gdstudio 都失败时第二兜底
    #   (oiapi.net 公共反向代理 API,只支持 netease;三层冗余保稳)
    # - mock 兼容老测试
    SUB_TO_FILE = {
        "local": "local.js",
        "tx": "huibq.js",
        "kw": "huibq.js",
        "wy": "huibq.js",
        "kg": "huibq.js",
        "mg": "huibq.js",
        "wy_gdstudio": "gdstudio.js",
        "wy_oiapi": "oiapi.js",
        "mock": "mock.js",
    }

    def __init__(
        self,
        sources: Optional[List[str]] = None,
        startup_timeout: float = 8.0,
        call_timeout: float = 12.0,
    ) -> None:
        # sources 接受 sub-source 名(新约定)或源文件名(老兼容)。统一 dedupe 出源文件清单。
        self._sources = sources or ["mock.js"]
        self._source_files = self._resolve_source_files(self._sources)
        self._call_timeout = call_timeout
        self._lock = threading.Lock()
        self._next_id = 0
        self._stderr_lines: List[str] = []
        self._stderr_thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._start()

    @classmethod
    def _resolve_source_files(cls, sources: List[str]) -> List[str]:
        """把 LX sub-source 名(新) 或 源文件名(老兼容) 转成实际加载的源文件清单,去重保序。"""
        seen: set = set()
        out: List[str] = []
        for s in sources:
            file = cls.SUB_TO_FILE.get(s, s)  # 不在 SUB_TO_FILE 里就当作源文件名(老兼容)
            if file not in seen:
                seen.add(file)
                out.append(file)
        return out

    def _start(self) -> None:
        env = os.environ.copy()
        env["LX_SOURCES"] = ",".join(self._source_files)
        # 在 Windows 上,subprocess 默认不带 CREATE_NO_WINDOW;避免弹黑窗
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        self._proc = subprocess.Popen(
            ["node", "lx_runtime.js"],
            cwd=str(_LX_RUNTIME_DIR),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
            creationflags=creationflags,
        )
        # 后台线程排干 stderr,避免阻塞
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="lx_runtime.stderr", daemon=True
        )
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for raw in iter(self._proc.stderr.readline, b""):
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:
                line = repr(raw)
            self._stderr_lines.append(line)

    def _drain_stdout_to_stderr(self) -> None:
        # 占位:真正的 console.log → stderr 重写在 lx_runtime.js 里完成。
        pass

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def call(self, action: str, source: str, info: Dict[str, Any]) -> Dict[str, Any]:
        """发一条 RPC,等响应。Phase 1 单请求串行(锁内)。"""
        if not self.is_alive():
            return {"ok": False, "err": f"lx_runtime not alive (pid={self._proc.pid if self._proc else None}, rc={self._proc.returncode if self._proc else None})"}

        with self._lock:
            self._next_id += 1
            req_id = str(self._next_id)
            req = json.dumps(
                {"id": req_id, "action": action, "source": source, "info": info or {}},
                ensure_ascii=False,
            )
            try:
                self._proc.stdin.write(req.encode("utf-8") + b"\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                return {"ok": False, "err": f"stdin write failed: {e}"}

            try:
                line = self._proc.stdout.readline()
            except Exception as e:
                return {"ok": False, "err": f"stdout readline failed: {e}"}

            if not line:
                return {"ok": False, "err": "empty response (subprocess exited?)"}
            try:
                resp = json.loads(line.decode("utf-8", errors="replace").strip())
            except json.JSONDecodeError as e:
                return {"ok": False, "err": f"bad response json: {e}; raw={line!r}"}
            # 用我们发的 id 校验(避免历史残留)
            if resp.get("id") not in (req_id, None) and "id" in resp:
                # 不致命,仅日志
                self._stderr_lines.append(
                    f"[client] id mismatch: sent={req_id} got={resp.get('id')}\n"
                )
            return resp

    @property
    def stderr_tail(self) -> str:
        """最近 50 行 stderr(诊断用)"""
        return "".join(self._stderr_lines[-50:])

    def shutdown(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            except Exception:
                pass

    def __enter__(self) -> "LxRuntimeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.shutdown()


def quick_smoke() -> Dict[str, Any]:
    """独立可跑:cd companion && python -c 'from lx_runtime_client import quick_smoke; print(quick_smoke())'"""
    c = LxRuntimeClient(sources=["mock.js"])
    try:
        return c.call("musicUrl", "mock", {"musicInfo": {"hash": "demo"}, "type": "320k"})
    finally:
        c.shutdown()


if __name__ == "__main__":
    print(json.dumps(quick_smoke(), ensure_ascii=False, indent=2))
