"""screenshot_bridge.py — 桌面截图子进程桥(2026-09-26 ship, P3j T28)。

复现 joeblack-lha/screenshot-mcp(npm 全平台 CLI, ~2026 头部
screenshot MCP server)的核心截图能力,让 PrisirAI 主对话可以:

  · 截全屏 / 截窗口 / 截区域(macOS screencapture / Win PowerShell /
    Linux grim-scrot-maim-import 自动探测)
  · 列已保存截图 + 读 PNG 元数据(尺寸 / 文件大小 / 时间戳)
  · 探测当前平台 + 已装后端(健康 4 档)

跟 T25/T26 浏览器内截图的差异:
  · T25 playwright-mcp / T26 agent-browser:只能截「浏览器渲染的页面」
  · T28 screenshot-mcp:能截「整个桌面 / 任意窗口 / 指定区域」,补完
    用户真实场景(微信/钉钉/游戏/IDE 等桌面应用界面)
  · T28 输出的 PNG 通过 capability_exec_result 链路自动送给 LLM
    vision(若 LLM 多模态已开),实现「LLM 看图」

设计:
  · 复用 gh_bridge._run 模式(subprocess.run + JSON 解析)
  · CLI 形态(npm 全局安装 / npx 一次性),无需长连子进程
  · 健康 4 档:missing_cli / missing_node / no_backend / ready
  · 默认输出 ~/.screenshot-mcp/captures/,可指定 --output

公开 API(6 个 fn):
  · ss_health()                          → {ok, mode, version, backend, ...}
  · ss_capture(mode, *, area, filename, output_dir, timeout) → {ok, path, ...}
  · ss_list(*, limit, dir)               → list[{path, size, mtime}]
  · ss_read(path)                        → {ok, path, exists, size, ...}
  · ss_active_backend()                  → {ok, platform, backend, available}
  · ss_install_hint()                    → {ok, hint} (静态安装提示)
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SS_BIN = os.environ.get("SS_BIN", "screenshot-mcp")
SS_TIMEOUT = 30.0
# 默认输出目录(screenshot-mcp CLI 默认 ~/.screenshot-mcp/captures/)
SS_DEFAULT_OUTPUT_DIR = Path.home() / ".screenshot-mcp" / "captures"

# 截图模式枚举
SS_MODES = ("fullscreen", "window", "area")

# 各平台原生后端优先级(参考 joeblack-lha/screenshot-mcp README)
BACKENDS_LINUX = ("grim", "scrot", "maim", "import", "xwd")
BACKENDS_MACOS = ("screencapture",)
BACKENDS_WINDOWS = ("powershell",)  # PowerShell + System.Windows.Forms

# 安装提示
_INSTALL_HINT_NPM = ("screenshot-mcp 未装。\n"
                     "安装命令:npm install -g screenshot-mcp\n"
                     "(首次会下 Playwright + Chromium 约 200MB)")

_INSTALL_HINT_BACKEND_WIN = ("Windows 截图后端缺失。"
                              "检查 PowerShell 可用且 .NET System.Windows.Forms 能加载。")

_INSTALL_HINT_BACKEND_MAC = ("macOS 截图后端缺失。screencapture 是系统自带,"
                              "理论上必装;PATH 错了检查 /usr/sbin。")

_INSTALL_HINT_BACKEND_LINUX = ("Linux 截图后端缺失。\n"
                                "Wayland 装 grim(推荐:sudo apt install grim)\n"
                                "X11 装 scrot / maim 任一:sudo apt install scrot")


# ---------------------------------------------------------------------------
# 内部:跑 screenshot-mcp 子命令
# ---------------------------------------------------------------------------

def _run(args: list[str], *, timeout: float = SS_TIMEOUT,
         need_json: bool = False) -> dict[str, Any]:
    """调 screenshot-mcp 子命令,统一异常处理。

    返回值形态:
      成功 + 退出码 0 + stdout → {ok: True, stdout, stderr, returncode}
      失败                     → {ok: False, error: "ss_xxx", ...}
    """
    cmd = [SS_BIN, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "installed": False,
                "error": "ss_cli_not_found",
                "hint": _INSTALL_HINT_NPM}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ss_timeout",
                "hint": f"screenshot-mcp 操作超时 ({timeout}s)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"ss_{type(e).__name__}",
                "detail": str(e)[:200]}
    if proc.returncode != 0:
        stderr = (proc.stderr or "")[-400:]
        stdout = (proc.stdout or "")[-400:]
        return {"ok": False, "error": "ss_failed",
                "returncode": proc.returncode,
                "stderr": stderr, "stdout": stdout}
    if need_json:
        try:
            return {"ok": True, "data": json.loads(proc.stdout or "{}"),
                    "stderr": proc.stderr or ""}
        except json.JSONDecodeError:
            return {"ok": True, "data": {"raw": proc.stdout or ""},
                    "stderr": proc.stderr or ""}
    return {"ok": True, "stdout": proc.stdout or "",
            "stderr": proc.stderr or "", "returncode": proc.returncode}


# ---------------------------------------------------------------------------
# 公开 API 1: ss_health(健康探活)
# ---------------------------------------------------------------------------

def ss_health(*, timeout: float = 10.0) -> dict[str, Any]:
    """4 档 mode:missing_cli / missing_node / no_backend / ready。

    检测步骤:
      1. screenshot-mcp 二进制在不在 PATH(否则 missing_cli)
      2. Node.js 版本是否 ≥ 18(screenshot-mcp 要求,否则 missing_node)
      3. 当前平台原生后端是否就绪(否则 no_backend)
      4. 跑 --version 拿版本号
    """
    bin_path = shutil.which(SS_BIN)
    if not bin_path:
        return {"ok": True, "installed": False, "mode": "missing_cli",
                "bin": "", "version": "", "node": "",
                "backend": "", "platform": _detect_platform(),
                "hint": _INSTALL_HINT_NPM}
    # Node.js 版本探测
    node_path = shutil.which("node")
    node_ver = ""
    if node_path:
        try:
            nv = subprocess.run([node_path, "--version"],
                                capture_output=True, text=True,
                                timeout=3, encoding="utf-8",
                                errors="replace")
            if nv.returncode == 0:
                node_ver = (nv.stdout or "").strip().lstrip("v")
        except Exception:
            node_ver = ""
    # 简单版本检查
    if node_ver:
        try:
            major = int(node_ver.split(".")[0])
            if major < 18:
                return {"ok": True, "installed": True, "mode": "missing_node",
                        "bin": bin_path, "version": "",
                        "node": node_ver, "backend": "",
                        "platform": _detect_platform(),
                        "hint": (f"Node.js 版本 {node_ver} 过低,"
                                 f"screenshot-mcp 要求 ≥ 18;"
                                 f"装 https://nodejs.org/")}
        except (ValueError, IndexError):
            pass
    # 后端探测
    backend, backend_available = _detect_backend()
    if not backend_available:
        return {"ok": True, "installed": True, "mode": "no_backend",
                "bin": bin_path, "version": "",
                "node": node_ver, "backend": backend,
                "platform": _detect_platform(),
                "hint": _backend_hint(backend)}
    # 版本
    r = _run(["--version"], timeout=5.0)
    version = ""
    if r.get("ok"):
        version = (r.get("stdout") or "").strip().split("\n")[0]
    return {"ok": True, "installed": True, "mode": "ready",
            "bin": bin_path, "version": version,
            "node": node_ver, "backend": backend,
            "platform": _detect_platform()}


# ---------------------------------------------------------------------------
# 公开 API 2: ss_capture(截图主操作)
# ---------------------------------------------------------------------------

def ss_capture(mode: str = "fullscreen", *,
               area: str = "",
               filename: str = "",
               output_dir: str = "",
               timeout: float = SS_TIMEOUT) -> dict[str, Any]:
    """截图(macOS / Win / Linux 全平台)。

    Args:
      mode: 'fullscreen' | 'window' | 'area'
      area: 'x,y,w,h'(仅 mode='area' 时必填)
      filename: 文件名(留空自动生成 timestamp)
      output_dir: 输出目录(默认 ~/.screenshot-mcp/captures/)
      timeout: 超时秒数

    返回:{ok, path, mode, backend, ...}
    失败:{ok: False, error: "ss_xxx"}
    """
    if mode not in SS_MODES:
        return {"ok": False, "error": "ss_bad_mode",
                "hint": f"mode 必须是 {SS_MODES}",
                "got": mode}
    if mode == "area" and not area:
        return {"ok": False, "error": "ss_invalid_area",
                "hint": "mode='area' 必须传 area='x,y,w,h'"}
    # area 格式校验(防注入)
    if area:
        try:
            parts = [int(p.strip()) for p in area.split(",")]
            if len(parts) != 4 or any(p < 0 for p in parts):
                raise ValueError
            if parts[2] <= 0 or parts[3] <= 0:
                raise ValueError
        except (ValueError, IndexError):
            return {"ok": False, "error": "ss_invalid_area",
                    "hint": "area 必须是 'x,y,w,h' 非负整数(>0 的宽高)"}

    args: list[str] = ["capture", "--mode", mode]
    if area:
        args += ["--area", area]
    if filename:
        # 防路径穿越
        if "/" in filename or "\\" in filename or ".." in filename:
            return {"ok": False, "error": "ss_bad_filename",
                    "hint": "filename 不能含路径分隔符或 '..'"}
        args += ["--filename", filename]
    if output_dir:
        args += ["--output-dir", output_dir]

    r = _run(args, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "mode": mode, "area": area, **r}
    # screenshot-mcp CLI 退出 0 + 在 stdout 末尾打印 'Saved to: <path>'
    out = (r.get("stdout") or "").strip()
    path = _parse_saved_path(out) or ""
    return {"ok": True, "mode": mode, "area": area,
            "path": path, "stdout": out[-400:],
            "stderr": (r.get("stderr") or "")[-200:]}


def _parse_saved_path(stdout: str) -> str | None:
    """从 screenshot-mcp capture 的 stdout 抽 'Saved to: <path>'。"""
    import re as _re
    m = _re.search(r"Saved\s+to[:\s]+(\S+)", stdout)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# 公开 API 3: ss_list(列已保存截图)
# ---------------------------------------------------------------------------

def ss_list(*, limit: int = 20,
            output_dir: str = "") -> dict[str, Any]:
    """列已保存截图(glob PNG/JPG,按 mtime desc)。

    不走 subprocess(直接读文件系统,screenshot-mcp 没 list CLI 子命令,
    自己 glob 更稳)。
    """
    base = Path(output_dir) if output_dir else SS_DEFAULT_OUTPUT_DIR
    if not base.exists():
        return {"ok": True, "entries": [], "total": 0,
                "dir": str(base), "warnings": ["dir_not_exist"]}
    entries: list[dict[str, Any]] = []
    safe_limit = max(1, min(int(limit), 200))
    try:
        for fp in sorted(base.glob("*.png"), key=lambda p: p.stat().st_mtime,
                          reverse=True)[:safe_limit]:
            stat = fp.stat()
            entries.append({
                "path": str(fp),
                "name": fp.name,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            })
        # 同时列 jpg(部分后端会存 jpg)
        for fp in sorted(base.glob("*.jpg"), key=lambda p: p.stat().st_mtime,
                          reverse=True)[:safe_limit]:
            stat = fp.stat()
            entries.append({
                "path": str(fp),
                "name": fp.name,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            })
        entries.sort(key=lambda e: e["mtime"], reverse=True)
        entries = entries[:safe_limit]
    except Exception as e:  # noqa: BLE001
        return {"ok": True, "entries": [], "total": 0,
                "dir": str(base), "warnings": [type(e).__name__]}
    return {"ok": True, "entries": entries,
            "total": len(entries), "dir": str(base)}


# ---------------------------------------------------------------------------
# 公开 API 4: ss_read(读截图元数据)
# ---------------------------------------------------------------------------

def ss_read(path: str) -> dict[str, Any]:
    """读截图 PNG/JPG 元数据(尺寸 / 文件大小 / mtime)。

    故意不走 subprocess(screenshot-mcp CLI 没 read 子命令)。
    """
    if not path:
        return {"ok": False, "error": "ss_empty_path"}
    fp = Path(path)
    if not fp.exists():
        return {"ok": False, "error": "ss_not_found",
                "path": path, "hint": "截图文件不存在(可能被清理或路径错)"}
    if not fp.is_file():
        return {"ok": False, "error": "ss_not_file", "path": path}
    try:
        stat = fp.stat()
        size_bytes = stat.st_size
        # 读 PNG 头 8 字节 + IHDR(13 字节 = width/height)拿尺寸
        width = height = 0
        if fp.suffix.lower() == ".png":
            with fp.open("rb") as f:
                f.read(16)  # 跳过 8 字节 PNG sig + 8 字节 IHDR 头
                w, h = _read_png_dimensions(f)
                width, height = w, h
        elif fp.suffix.lower() in (".jpg", ".jpeg"):
            width, height = _read_jpeg_dimensions(fp)
        return {"ok": True, "path": path,
                "exists": True, "size": size_bytes,
                "width": width, "height": height,
                "mtime": int(stat.st_mtime),
                "suffix": fp.suffix.lower()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__,
                "path": path, "detail": str(e)[:200]}


def _read_png_dimensions(f) -> tuple[int, int]:
    import struct as _s
    data = f.read(8)
    if len(data) != 8:
        return (0, 0)
    w, h = _s.unpack(">II", data)
    return (w, h)


def _read_jpeg_dimensions(fp: Path) -> tuple[int, int]:
    """简易 JPEG 尺寸读取(扫描 SOF0/SOF2 marker)。失败返 (0, 0)。"""
    with fp.open("rb") as f:
        if f.read(2) != b"\xff\xd8":  # JPEG SOI
            return (0, 0)
        import struct as _s
        while True:
            byte = f.read(1)
            if not byte or byte != b"\xff":
                return (0, 0)
            marker = f.read(1)
            if not marker:
                return (0, 0)
            # SOF0 / SOF2 标记含尺寸
            if marker in (b"\xc0", b"\xc2"):
                length_bytes = f.read(2)
                if len(length_bytes) != 2:
                    return (0, 0)
                length = _s.unpack(">H", length_bytes)[0]
                f.read(1)  # precision
                h_bytes = f.read(2)
                w_bytes = f.read(2)
                if len(h_bytes) != 2 or len(w_bytes) != 2:
                    return (0, 0)
                h = _s.unpack(">H", h_bytes)[0]
                w = _s.unpack(">H", w_bytes)[0]
                return (w, h)
            else:
                # 跳过这段 marker
                length_bytes = f.read(2)
                if len(length_bytes) != 2:
                    return (0, 0)
                length = _s.unpack(">H", length_bytes)[0]
                f.read(length - 2)


# ---------------------------------------------------------------------------
# 公开 API 5: ss_active_backend(当前平台 + 后端)
# ---------------------------------------------------------------------------

def ss_active_backend() -> dict[str, Any]:
    """返回当前 OS 平台 + 已选后端 + 是否可用(纯本地探查,无 subprocess)。"""
    plat = _detect_platform()
    backend, available = _detect_backend()
    return {"ok": True, "platform": plat, "backend": backend,
            "available": available,
            "hint": None if available else _backend_hint(backend)}


def ss_install_hint() -> dict[str, Any]:
    """返回当前平台的安装提示(静态,无 subprocess)。"""
    plat = _detect_platform()
    if plat == "windows":
        return {"ok": True, "platform": plat,
                "hint": ("screenshot-mcp 装好后 Windows 截图不需要额外后端,"
                         "PowerShell + .NET 即可。如果缺失,检查 System.Windows.Forms"
                         "能加载。"),
                "command": "npm install -g screenshot-mcp"}
    if plat == "darwin":
        return {"ok": True, "platform": plat,
                "hint": ("screenshot-mcp 在 macOS 上用系统自带 screencapture 命令,"
                         "理论必装。如果 PATH 不到 /usr/sbin/screencapture,"
                         "重装 macOS 或加到 PATH。"),
                "command": "npm install -g screenshot-mcp"}
    # Linux
    backend, avail = _detect_backend()
    if avail:
        return {"ok": True, "platform": plat, "backend": backend,
                "hint": f"已检测到后端:{backend}",
                "command": "npm install -g screenshot-mcp"}
    return {"ok": True, "platform": plat, "backend": backend or "未检测到",
            "hint": _INSTALL_HINT_BACKEND_LINUX,
            "command": "npm install -g screenshot-mcp"}


# ---------------------------------------------------------------------------
# 内部 helpers
# ---------------------------------------------------------------------------

def _detect_platform() -> str:
    sysname = sys.platform.lower()
    if sysname.startswith("win"):
        return "windows"
    if sysname.startswith("darwin") or sysname.startswith("mac"):
        return "darwin"
    if sysname.startswith("linux"):
        return "linux"
    return sysname or "unknown"


def _detect_backend() -> tuple[str, bool]:
    """返回 (backend_name, available)。"""
    plat = _detect_platform()
    if plat == "windows":
        # PowerShell 必装(Windows 系统自带)
        if shutil.which("powershell") or shutil.which("pwsh") or shutil.which("powershell.exe"):
            return ("powershell", True)
        return ("powershell", False)
    if plat == "darwin":
        # screencapture 系统自带
        if shutil.which("screencapture"):
            return ("screencapture", True)
        return ("screencapture", False)
    if plat == "linux":
        for b in BACKENDS_LINUX:
            if shutil.which(b):
                return (b, True)
        return ("", False)
    return ("", False)


def _backend_hint(backend: str) -> str:
    plat = _detect_platform()
    if plat == "windows":
        return _INSTALL_HINT_BACKEND_WIN
    if plat == "darwin":
        return _INSTALL_HINT_BACKEND_MAC
    return _INSTALL_HINT_BACKEND_LINUX


# ---------------------------------------------------------------------------
# CLI(便于调试)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    cmd = (_sys.argv[1:] or ["health"])[0]
    if cmd == "health":
        print(json.dumps(ss_health(), ensure_ascii=False, indent=2))
    elif cmd == "capture":
        mode = _sys.argv[2] if len(_sys.argv) > 2 else "fullscreen"
        area = _sys.argv[3] if len(_sys.argv) > 3 else ""
        print(json.dumps(ss_capture(mode, area=area), ensure_ascii=False, indent=2))
    elif cmd == "list":
        print(json.dumps(ss_list(), ensure_ascii=False, indent=2))
    elif cmd == "backend":
        print(json.dumps(ss_active_backend(), ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd}", file=_sys.stderr)
        _sys.exit(2)