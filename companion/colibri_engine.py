# -*- coding: utf-8 -*-
# companion/colibri_engine.py — colibri 推理引擎子进程管理(2026-09-28 ship Phase A)
#
# 定位:
#   - 把 colibri 纯 C 推理引擎作为一个常驻 HTTP 子进程拉起 / 监控 / 重启 / 停止
#   - 对外暴露简单的状态查询接口:`status()` / `try_start()` / `stop()` / `ensure_running()`
#   - 跨平台 binary 路径解析:Windows `coli.exe` / Linux/macOS `coli`
#   - 进程死亡保护:max 3 次重启,指数退避(1s/4s/16s),失败后 state="crashed"
#   - HTTP 探活:`GET {base}/v1/models` 5s 超时(OpenAI 兼容标准)
#
# 设计取舍:
#   - 不引入 aiohttp / 第三方 HTTP 客户端(复用 fastlane.base.tls13_client)
#   - 不在子进程里跑 Popen + 后台线程(用 asyncio.create_subprocess_exec,可 await + cancel)
#   - 子进程 stdout/stderr 写日志文件(<DATA_DIR>/colibri.log),便于故障诊断
#   - PID 文件存 state 里,无需额外文件
#   - 不主动 gc 后台任务 —— asyncio.Task 由事件循环管
#
# 已知坑:
#   - Windows 上 subprocess 弹 UAC 需 `coli.exe` 是 MSI/NSIS 安装签名过;装包时再处理
#   - macOS Gatekeeper quarantine 属性 — 用户首次双击需右键打开(装包后处理)
#   - Linux `coli` binary 必须有可执行位(`os.chmod 0o755`)
#   - 启动慢(0.05–0.1 tok/s),5min 内不返回首 token 正常,探活只看 /v1/models 是否 200
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Optional

from fastlane.providers.base import tls13_client

from . import colibri_state as _state_mod
from .colibri_state import (
    ColibriState,
    DEFAULT_PORT,
    is_model_path_set,
    load_state,
    save_state,
    state_path,
    update_state,
)

log = logging.getLogger("prisiragent-companion.colibri.engine")


# ============================================================
# 跨平台 binary 路径解析
# ============================================================

# 候选顺序:
#   - <DATA_DIR>/bin/{coli,coli.exe}     — 装包自带(主)
#   - <HERE>/bin/{coli,coli.exe}           — 开发态(源码树)
#   - 系统 PATH(开发态 `make` 后)
_BIN_NAME = "coli.exe" if sys.platform == "win32" else "coli"


def find_colibri_binary() -> Optional[Path]:
    """返回 colibri 二进制绝对路径,找不到返 None(从不抛 — 上层 fail-soft)。"""
    candidates: list[Path] = []

    # 1) 装包路径:<DATA_DIR>/bin/coli{,.exe}
    try:
        from .colibri_state import _data_dir
        candidates.append(_data_dir() / "bin" / _BIN_NAME)
    except Exception:
        pass

    # 2) 源码树:companion/bin/{coli,coli.exe}
    here = Path(__file__).resolve().parent
    candidates.append(here / "bin" / _BIN_NAME)

    # 3) 装包根:<DATA_DIR>/coli{,.exe}(老路径兼容)
    try:
        from .colibri_state import _data_dir
        candidates.append(_data_dir() / _BIN_NAME)
    except Exception:
        pass

    for p in candidates:
        if p.is_file():
            return p

    # 4) 系统 PATH(开发态 `make -C c olmoe` 后 ./coli 已可用)
    which = shutil.which("coli")
    return Path(which) if which else None


def model_dir_exists() -> bool:
    """OLMoE 容器目录是否存在且非空(快捷检查)。"""
    return is_model_path_set()


# ============================================================
# HTTP 探活
# ============================================================

async def _health_probe(port: int, timeout: float = 5.0) -> bool:
    """打 `GET http://127.0.0.1:{port}/v1/models`,200 视为健康。

    用 stdlib http.client + run_in_executor,避开 aiohttp 依赖 + 跨平台事件循环坑。
    """
    import json as _json
    import http.client

    def _probe() -> bool:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
            conn.request("GET", "/v1/models")
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            if resp.status != 200:
                return False
            # 200 + 合法 JSON 视为 ok(colibri /v1/models 返回 {"object":"list",...})
            try:
                obj = _json.loads(body)
                return isinstance(obj, dict)
            except Exception:
                return False
        except Exception:
            return False

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _probe)


# ============================================================
# 子进程管理
# ============================================================

class ColibriEngine:
    """colibri 子进程单例(模块级 _ENGINE 共享)。

    状态机由 _state_mod.ColibriState.state 字段管理:
        not_downloaded / downloading / ready / stopped / crashed
    """

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._restart_task: Optional[asyncio.Task] = None
        self._supervised: bool = False
        self._log_file: Optional[Path] = None

    # ----- public API -----
    async def status(self) -> dict[str, Any]:
        """返 {ok, state, port, pid, downloaded, model_path, version, last_error, binary_found}."""
        s = load_state()
        binary = find_colibri_binary()
        downloaded = is_model_path_set(s)
        alive = bool(s.pid and _pid_alive(s.pid))
        # 引擎真在跑 + /health 200 → state="ready"
        if s.state == "ready" and not alive:
            log.warning("colibri pid=%s 已死亡,自动降级 state", s.pid)
            update_state(state="crashed", pid=None)
            s = load_state()
        return {
            "ok": True,
            "state": s.state,
            "port": s.port,
            "pid": s.pid,
            "alive": alive,
            "downloaded": downloaded,
            "model_path": s.model_path,
            "version": s.version,
            "last_error": s.last_error,
            "binary_found": binary is not None,
            "binary_path": str(binary) if binary else "",
        }

    async def try_start(self) -> dict[str, Any]:
        """尝试启动 colibri 子进程(幂等,已 running 时不重复 spawn)。

        成功 → state="ready",返 {ok: True, ...}
        失败 → state="crashed" or "not_downloaded",返 {ok: False, err: ...}
        """
        s = load_state()

        # 已 running → 直接返 ok
        if s.pid and _pid_alive(s.pid):
            return await self.status()

        # 模型目录不存在 → 不能启动
        if not is_model_path_set(s):
            update_state(state="not_downloaded", pid=None, last_error="")
            return {
                "ok": False,
                "err": "model_not_downloaded",
                "hint": "请下载 OLMoE 容器或去设置页填云端 key",
                "model_path": s.model_path,
            }

        # binary 不在 → 不能启动
        binary = find_colibri_binary()
        if not binary:
            update_state(state="crashed", pid=None,
                       last_error="colibri binary not found")
            return {
                "ok": False,
                "err": "binary_not_found",
                "hint": "colibri 引擎未安装,请重新安装 PriserAI 或联系支持",
            }

        # 内存检查(Phase D 2026-09-28 — < 6 GB 禁止启动)
        mem = check_memory_ok()
        if not mem["ok"]:
            update_state(state="crashed", pid=None,
                       last_error=f"memory insufficient: {mem['hint']}")
            return {
                "ok": False,
                "err": "memory_insufficient",
                "hint": mem["hint"],
                "available_bytes": mem["available_bytes"],
                "required_bytes": mem["required_bytes"],
            }

        # spawn
        try:
            ok = await self._spawn(binary, s)
        except Exception as e:  # noqa: BLE001
            log.exception("colibri spawn 异常: %s", e)
            update_state(state="crashed", pid=None,
                       last_error=f"{type(e).__name__}: {e}")
            return {"ok": False, "err": f"{type(e).__name__}: {e}"}

        if not ok:
            return {"ok": False, "err": "spawn_failed",
                    "last_error": load_state().last_error}

        # 启动 supervisor(只起一次)
        if not self._supervised:
            self._supervised = True
            self._restart_task = asyncio.create_task(self._supervisor())

        return await self.status()

    async def stop(self, *, reason: str = "user_stop") -> dict[str, Any]:
        """停止 colibri 子进程(state → "stopped",pid 保留备查)。"""
        s = load_state()
        if s.pid and _pid_alive(s.pid):
            try:
                import signal as _sig
                if sys.platform == "win32":
                    # Windows:taskkill /F /PID;subprocess.terminate 不够利索
                    import subprocess
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(s.pid)],
                        capture_output=True, timeout=5)
                else:
                    os.kill(s.pid, _sig.SIGTERM)
            except Exception as e:  # noqa: BLE001
                log.warning("colibri kill err: %s", e)
        # 等 1s 让进程退出
        await asyncio.sleep(1.0)
        if s.pid and _pid_alive(s.pid):
            log.warning("colibri SIGTERM 未生效,pid=%s 仍在", s.pid)
        update_state(state="stopped", pid=None, restart_attempts=0,
                   last_error=f"stopped by {reason}")
        # 关掉 supervisor
        if self._restart_task and not self._restart_task.done():
            self._restart_task.cancel()
            self._restart_task = None
        self._supervised = False
        return await self.status()

    async def ensure_running(self) -> dict[str, Any]:
        """若 state 不在 ready/starting → 调 try_start。

        入口在 companion-web 启动时 + /api/creds/status 被拉时。
        """
        s = load_state()
        if s.state == "ready" and s.pid and _pid_alive(s.pid):
            return await self.status()
        if s.state in ("downloading",):
            return await self.status()  # 等下载完再启
        return await self.try_start()

    # ----- 内部 -----
    async def _spawn(self, binary: Path, s: ColibriState) -> bool:
        """实际 asyncio.create_subprocess_exec 拉起。

        成功 → True,更新 state="ready" + pid。
        失败 → False,last_error 写明。
        """
        log_file = self._log_file or self._open_log_file(s.port)
        self._log_file = log_file
        cmd = [
            str(binary),
            "serve",
            "--model", s.model_path,
            "--port", str(s.port),
            "--host", "127.0.0.1",
        ]
        log.info("spawn colibri: %s", " ".join(cmd))
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except Exception as e:  # noqa: BLE001
            log.error("create_subprocess_exec 失败: %s", e)
            update_state(state="crashed", pid=None,
                       last_error=f"spawn: {type(e).__name__}: {e}")
            return False

        update_state(state="ready", pid=self._proc.pid, restart_attempts=0,
                   last_error="")

        # 等 3s,看进程是否还活着(快速失败检测)
        await asyncio.sleep(3.0)
        if self._proc.returncode is not None:
            err = f"exited immediately rc={self._proc.returncode}"
            log.error("colibri %s;log=%s", err, log_file)
            update_state(state="crashed", pid=None,
                       last_error=f"{err} (see {log_file})")
            return False
        return True

    async def _supervisor(self) -> None:
        """后台守护:每 10s 探活,死了就重启(指数退避,max 3)。"""
        backoff = [1, 4, 16]
        try:
            while True:
                await asyncio.sleep(10.0)
                s = load_state()
                if s.state in ("stopped", "not_downloaded", "downloading", "crashed"):
                    return  # 不再守护
                alive = bool(s.pid and _pid_alive(s.pid))
                if alive:
                    # 健康 — 探 /v1/models
                    healthy = await _health_probe(s.port)
                    if healthy:
                        _state_mod.touch_health()
                        update_state(restart_attempts=0, last_error="")
                    else:
                        log.warning("colibri alive but /v1/models failed")
                    continue

                # 死了 → 重启
                if s.restart_attempts >= 3:
                    log.error("colibri 连续重启 3 次失败,放弃")
                    update_state(state="crashed", restart_attempts=3,
                               last_error="restart limit reached")
                    return
                wait = backoff[min(s.restart_attempts, len(backoff) - 1)]
                log.warning("colibri died,重启 attempt=%d,等 %ds",
                            s.restart_attempts + 1, wait)
                await asyncio.sleep(wait)
                update_state(restart_attempts=s.restart_attempts + 1)
                await self.try_start()
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            log.exception("colibri supervisor 异常退出: %s", e)

    def _open_log_file(self, port: int) -> Any:
        """打开 <DATA_DIR>/colibri.log(append),作为子进程 stdout。"""
        from .colibri_state import _data_dir
        log_path = _data_dir() / "colibri.log"
        try:
            return open(log_path, "ab", buffering=0)
        except Exception as e:  # noqa: BLE001
            log.warning("open colibri.log 失败,fallback DEVNULL: %s", e)
            return asyncio.subprocess.DEVNULL


# ============================================================
# 跨平台 PID 检查
# ============================================================

def _pid_alive(pid: int) -> bool:
    """跨平台检测 pid 是否存活。

    - Windows:OpenProcess + GetExitCodeProcess;无 psutil 也能跑
    - Unix:os.kill(pid, 0) → 不发信号,只检查权限(权限够 = 进程在)
    """
    if pid is None or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return False
            finally:
                kernel32.CloseHandle(h)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except Exception:
            return False


# ============================================================
# 内存检查(Phase D 2026-09-28 — < 6 GB 禁止启动)
# ============================================================

MIN_MEMORY_BYTES = 6 * 1024 ** 3  # OLMoE-7B int8 + colibri runtime 最低需要 ~5.5 GB


def check_memory_ok() -> dict:
    """跨平台检查可用内存是否够跑 OLMoE。

    返回:{ok: bool, available_bytes: int, required_bytes: int,
          source: "psutil"|"ctypes", hint: str}

    fail-soft:任何异常返 ok=True(让上层跑,失败再由 try_start 兜住)。
    """
    # 1) psutil(首选 — 跨平台 + 简单)
    try:
        import psutil  # type: ignore
        mem = psutil.virtual_memory()
        ok = mem.available >= MIN_MEMORY_BYTES
        return {
            "ok": ok,
            "available_bytes": mem.available,
            "required_bytes": MIN_MEMORY_BYTES,
            "source": "psutil",
            "hint": "" if ok else
                f"可用内存 {mem.available / 1024**3:.1f} GB < 最低 {MIN_MEMORY_BYTES / 1024**3:.0f} GB,无法启动本地模型",
        }
    except ImportError:
        pass

    # 2) ctypes fallback(Windows:GlobalMemoryStatusEx;Unix:sysconf)
    try:
        import ctypes
        if sys.platform == "win32":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            avail = int(stat.ullAvailPhys)
            ok = avail >= MIN_MEMORY_BYTES
            return {
                "ok": ok,
                "available_bytes": avail,
                "required_bytes": MIN_MEMORY_BYTES,
                "source": "ctypes",
                "hint": "" if ok else
                    f"可用内存 {avail / 1024**3:.1f} GB 不足",
            }
        else:
            # Unix:sysconf(_SC_AVPHYS_PAGES) * _SC_PAGESIZE
            import ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
            pages = libc.sysconf(0x0006)  # _SC_AVPHYS_PAGES
            pagesize = libc.sysconf(0x0004)  # _SC_PAGESIZE
            if pages > 0 and pagesize > 0:
                avail = pages * pagesize
                ok = avail >= MIN_MEMORY_BYTES
                return {
                    "ok": ok,
                    "available_bytes": avail,
                    "required_bytes": MIN_MEMORY_BYTES,
                    "source": "ctypes",
                    "hint": "" if ok else
                        f"可用内存 {avail / 1024**3:.1f} GB 不足",
                }
    except Exception:
        pass

    # 全失败 → fail-soft 放行
    return {
        "ok": True,
        "available_bytes": 0,
        "required_bytes": MIN_MEMORY_BYTES,
        "source": "unknown",
        "hint": "无法检测内存,放行(由 try_start 兜底失败)",
    }


# ============================================================
# 模块级单例
# ============================================================

_ENGINE: Optional[ColibriEngine] = None


def engine() -> ColibriEngine:
    """模块级单例(确保 supervisor 只起一次)。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = ColibriEngine()
    return _ENGINE


__all__ = [
    "ColibriEngine",
    "engine",
    "find_colibri_binary",
    "model_dir_exists",
    "check_memory_ok",
    "MIN_MEMORY_BYTES",
    "DEFAULT_PORT",
]