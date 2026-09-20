# -*- coding: utf-8 -*-
"""
port_registry.py — M3.29.1 music web 端口跨进程共享

写入 HKCU\\Software\\PrisirAI\\music_port (Windows 注册表)
非 Windows 退化为 <workdir>/_prisir_registry/music_port.json

约定:
  - prisiragent-music-web.py 启动时 bind(0) 拿空闲端口 → write_music_port
  - prisiragent-tauri / companion / agent 等进程启动时 read_music_port 探测
  - 写入时同时创建 <workdir>/_prisir_registry/music_port.lock 文件(进程存活心跳)

不抛异常:任何 OSError 都静默降级返 None,允许 music web 退化为"无注册表也能跑"。
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Optional, Tuple

# Windows 注册表路径
REG_KEY = r"Software\PrisirAI"
REG_VAL = "music_port"

# 退化为本地文件(macOS / Linux / 注册表权限不足时)
_REG_DIR = Path(os.environ.get("PRISIR_WORKDIR", str(Path.cwd()))) / "_prisir_registry"
_REG_DIR.mkdir(parents=True, exist_ok=True)
_FILE_PATH = _REG_DIR / "music_port.json"
_LOCK_PATH = _REG_DIR / "music_port.lock"


def pick_free_port() -> int:
    """bind 0 拿空闲端口,立即关 socket(端口由 OS 持有 ~30s,需立即 listen)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _write_winreg(port: int) -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg  # type: ignore
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, REG_VAL, 0, winreg.REG_DWORD, port)
        return True
    except Exception:
        return False


def _read_winreg() -> Optional[int]:
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            v, _ = winreg.QueryValueEx(k, REG_VAL)
            return int(v)
    except Exception:
        return None


def _write_file(port: int, pid: int) -> None:
    payload = {
        "port": port,
        "pid": pid,
        "started_at_ms": int(time.time() * 1000),
    }
    try:
        _FILE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _read_file() -> Optional[Tuple[int, int]]:
    """返 (port, pid) 或 None"""
    try:
        if not _FILE_PATH.exists():
            return None
        data = json.loads(_FILE_PATH.read_text(encoding="utf-8"))
        return int(data.get("port", 0)) or None, int(data.get("pid", 0)) or None
    except Exception:
        return None


def write_music_port(port: int, pid: int) -> None:
    """music web 启动时调用:写注册表 + 写文件锁(双通道)。"""
    _write_winreg(port)
    _write_file(port, pid)


def read_music_port() -> Optional[Tuple[int, int]]:
    """外部读取:返回 (port, pid) 或 None。

    优先级:注册表 → 文件。文件优先(因为有 PID 心跳)。"""
    f = _read_file()
    if f:
        return f
    p = _read_winreg()
    return (p, 0) if p else None


def is_music_alive(port: int, pid: int) -> bool:
    """探测 music web 进程是否还在 + 端口是否监听。"""
    if pid <= 0:
        return _probe_port(port)
    try:
        import psutil  # type: ignore
        if not psutil.pid_exists(pid):
            return False
    except Exception:
        # 没装 psutil → 走端口探测兜底
        pass
    return _probe_port(port)


def _probe_port(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def quick_smoke() -> dict:
    p = pick_free_port()
    write_music_port(p, pid=os.getpid())
    back = read_music_port()
    return {"picked": p, "read_back": back, "alive": is_music_alive(*back) if back else False}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(quick_smoke(), ensure_ascii=False, indent=2))
