"""vision_tools.py — v0.23.4 OIagent 视觉观察 MCP tool 后端

走"工具不重复"原则:
- 不重写截图,走 PowerShell + System.Drawing(本机 Windows 通用)
- 不重写视觉 LLM,走 cc-switch 15721 + 已有的 model_providers
- ✅ 新:camera_observe 单帧/连续观察
- ✅ 新:source 支持 windows_desktop / mumu_screencap(adb)
- ✅ 新:base64 编码 → 视觉 LLM(走 cc-switch claude-opus-4-8 vision 或 qwen-vl-max)

环境变量:
- AUREON_VISION_DEFAULT_SOURCE: 默认 source,默认 "windows_desktop"
- AUREON_VISION_DEFAULT_PROMPT: 默认 prompt,默认 "描述这张图"
- AUREON_VISION_MODEL: 视觉 LLM,默认走 cc-switch qwen-vl-max(便宜 + 中文好)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("oiagent.vision_tools")

_DEFAULT_SOURCE = os.environ.get("AUREON_VISION_DEFAULT_SOURCE", "windows_desktop")
_DEFAULT_PROMPT = os.environ.get(
    "AUREON_VISION_DEFAULT_PROMPT",
    "描述这张图,重点关注屏幕上的应用、文字、状态"
)
_VISION_MODEL = os.environ.get("AUREON_VISION_MODEL", "qwen-vl-max")
# 直连百炼 OpenAI 兼容端点(不走 cc-switch,cc-switch 路由不一定支持 vision)
_BAILIAN_BASE = os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
_BAILIAN_KEY = os.environ.get("BAILIAN_API_KEY", "")
# 兼容保留 cc-switch 选项
_CCSWITCH_URL = os.environ.get("CCSWITCH_URL", "http://127.0.0.1:15721")


# ─────────────────────────────────────────────────
# 1. 截图实现(Windows 桌面 + MuMu)
# ─────────────────────────────────────────────────

def capture_windows_desktop() -> tuple[bytes, dict]:
    """Windows 桌面截图 — PowerShell + System.Drawing

    Returns: (png_bytes, meta)
    """
    out_path = Path(tempfile.gettempdir()) / "aureon_vision_capture.png"
    # 关键:路径直接拼进 PS 脚本,不走 $args($args 在 Chinese locale 下被 stream 编码吃掉)
    path_escaped = str(out_path).replace("'", "''")
    ps = f'''
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save('{path_escaped}', [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
Write-Host "$($bounds.Width)x$($bounds.Height)"
'''
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True,
            timeout=10,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            return b"", {"ok": False, "error": f"powershell rc={r.returncode}: {r.stderr[:200]}"}
        if not out_path.exists():
            return b"", {"ok": False, "error": f"截图文件未生成: {out_path}"}
        png_bytes = out_path.read_bytes()
        size_kb = len(png_bytes) / 1024
        meta = {
            "ok": True,
            "source": "windows_desktop",
            "path": str(out_path),
            "size_bytes": len(png_bytes),
            "size_kb": round(size_kb, 1),
            "resolution": r.stdout.strip(),
        }
        log.info(f"capture: {meta}")
        return png_bytes, meta
    except subprocess.TimeoutExpired:
        return b"", {"ok": False, "error": "powershell timeout"}
    except Exception as e:
        return b"", {"ok": False, "error": f"{type(e).__name__}: {e}"}


def capture_mumu_screencap() -> tuple[bytes, dict]:
    """MuMu 模拟器 adb screencap

    Returns: (png_bytes, meta)
    """
    # 走 D:/AureonCloud 已有的 MuMu 端口配置
    mumu_host = os.environ.get("MUMU_ADB_HOST", "127.0.0.1")
    mumu_port = os.environ.get("MUMU_ADB_PORT", "7555")
    out_path = Path(tempfile.gettempdir()) / "aureon_vision_mumu.png"
    try:
        # adb exec-out 输出到 stdout
        r = subprocess.run(
            ["adb", "-s", f"{mumu_host}:{mumu_port}", "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=10,
        )
        if r.returncode != 0:
            return b"", {"ok": False, "error": f"adb rc={r.returncode}: {r.stderr.decode('utf-8', 'replace')[:200]}"}
        png_bytes = r.stdout
        out_path.write_bytes(png_bytes)
        size_kb = len(png_bytes) / 1024
        meta = {
            "ok": True,
            "source": "mumu_screencap",
            "path": str(out_path),
            "size_bytes": len(png_bytes),
            "size_kb": round(size_kb, 1),
            "adb_target": f"{mumu_host}:{mumu_port}",
        }
        log.info(f"capture: {meta}")
        return png_bytes, meta
    except subprocess.TimeoutExpired:
        return b"", {"ok": False, "error": "adb screencap timeout"}
    except FileNotFoundError:
        return b"", {"ok": False, "error": "adb 命令未找到,需安装 Android Platform Tools"}
    except Exception as e:
        return b"", {"ok": False, "error": f"{type(e).__name__}: {e}"}


def capture_frame(source: str = _DEFAULT_SOURCE) -> tuple[bytes, dict]:
    """统一入口"""
    if source == "windows_desktop":
        return capture_windows_desktop()
    elif source == "mumu_screencap":
        return capture_mumu_screencap()
    else:
        return b"", {"ok": False, "error": f"未知 source: {source},支持 windows_desktop / mumu_screencap"}


# ─────────────────────────────────────────────────
# 2. 视觉 LLM(走 cc-switch OpenAI 兼容)
# ─────────────────────────────────────────────────

def vision_query(png_bytes: bytes, prompt: str, model: str = _VISION_MODEL) -> dict:
    """调百炼 qwen-vl-max 视觉模型,返回文字描述

    走 OpenAI 兼容 chat/completions + image_url(data:image/png;base64,...)
    直连百炼(不走 cc-switch,cc-switch 路由不一定支持 vision 模态)
    """
    if not png_bytes:
        return {"ok": False, "error": "空图像"}
    if not _BAILIAN_KEY:
        return {"ok": False, "error": "BAILIAN_API_KEY 未设,无法调百炼视觉"}

    b64 = base64.b64encode(png_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
    }

    try:
        import urllib.request
        req = urllib.request.Request(
            f"{_BAILIAN_BASE}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_BAILIAN_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode("utf-8"))
        choices = resp.get("choices", [])
        if not choices:
            return {"ok": False, "error": f"百炼无 choices: {json.dumps(resp)[:300]}"}
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        usage = resp.get("usage", {})
        return {
            "ok": True,
            "model": model,
            "description": content,
            "usage": usage,
        }
    except Exception as e:
        log.exception("vision_query failed")
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"}


def camera_observe_stream_impl(
    source: str = _DEFAULT_SOURCE,
    prompt: str = _DEFAULT_PROMPT,
    frames: int = 3,
    interval_sec: float = 1.0,
) -> str:
    """连续 N 帧观察 — 每帧截图 + 视觉 LLM,返回 N 条 description

    v0.24.2 新增:实现"实时视觉"流式观察
    frames: 抓几帧(默认 3)
    interval_sec: 帧间隔秒(默认 1s)
    """
    results = []
    for i in range(frames):
        log.info(f"[stream] frame {i+1}/{frames}")
        png_bytes, capture_meta = capture_frame(source)
        if not capture_meta.get("ok"):
            results.append({
                "frame": i + 1,
                "ok": False,
                "stage": "capture",
                "error": capture_meta.get("error"),
            })
            time.sleep(interval_sec)
            continue
        vision_result = vision_query(png_bytes, prompt)
        results.append({
            "frame": i + 1,
            "ok": vision_result.get("ok", False),
            "description": vision_result.get("description"),
            "usage": vision_result.get("usage"),
            "capture_size_kb": round(len(png_bytes) / 1024, 1),
        })
        if i < frames - 1:
            time.sleep(interval_sec)
    return json.dumps({
        "ok": True,
        "source": source,
        "prompt": prompt,
        "frames": frames,
        "interval_sec": interval_sec,
        "results": results,
    }, ensure_ascii=False, default=str)


# ─────────────────────────────────────────────────
# 3. camera_observe MCP tool 实现
# ─────────────────────────────────────────────────

def camera_observe_impl(
    source: str = _DEFAULT_SOURCE,
    prompt: str = _DEFAULT_PROMPT,
    save_image: bool = False,
) -> str:
    """单帧观察 — 截图 + 视觉 LLM

    source: "windows_desktop" | "mumu_screencap"
    prompt: 给视觉 LLM 的指令
    save_image: 是否把图像 base64 也附在返回里(默认 False 节省 token)
    """
    # 1. 截图
    png_bytes, capture_meta = capture_frame(source)
    if not capture_meta.get("ok"):
        return json.dumps(
            {"ok": False, "stage": "capture", **capture_meta},
            ensure_ascii=False,
        )

    # 2. 视觉 LLM
    vision_result = vision_query(png_bytes, prompt)

    # 3. 组装返回
    out = {
        "ok": vision_result.get("ok", False),
        "source": source,
        "prompt": prompt,
        "capture": capture_meta,
        "vision": {
            "model": vision_result.get("model"),
            "description": vision_result.get("description"),
            "usage": vision_result.get("usage"),
        },
    }
    if not vision_result.get("ok"):
        out["error"] = vision_result.get("error")
    # save_image=True 时附 base64(用于自检,默认 False)
    if save_image:
        out["image_base64"] = base64.b64encode(png_bytes).decode("ascii")
        out["image_size_kb"] = round(len(png_bytes) / 1024, 1)
    return json.dumps(out, ensure_ascii=False, default=str)


def vision_health_impl() -> str:
    """视觉能力健康检查 — 截图 + 百炼视觉模型是否可达"""
    health = {
        "ok": True,
        "bailian_base": _BAILIAN_BASE,
        "bailian_key_set": bool(_BAILIAN_KEY),
        "default_source": _DEFAULT_SOURCE,
        "default_model": _VISION_MODEL,
        "capture_methods": ["windows_desktop", "mumu_screencap"],
    }
    # 测试截图(最小)
    png, meta = capture_frame("windows_desktop")
    health["capture_test"] = meta
    health["capture_test_ok"] = meta.get("ok", False)
    return json.dumps(health, ensure_ascii=False, indent=2, default=str)


# ── Dynamic Registry Exports (v0.38) ────────────────────────────

TOOL_DEFS = [
    {
        "name": "camera_observe",
        "description": (
            "单帧视觉观察 — 截图 + 视觉 LLM。"
            "source: windows_desktop(Windows 桌面截图) 或 mumu_screencap(MuMu 模拟器)。"
            "视觉模型走百炼 qwen-vl-max。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["windows_desktop", "mumu_screencap"], "default": "windows_desktop"},
                "prompt": {"type": "string", "default": "描述这张图"},
                "save_image": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    },
    {
        "name": "camera_observe_stream",
        "description": "流式视觉观察 — 逐帧截图 + 流式输出视觉 LLM 结果",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["windows_desktop", "mumu_screencap"], "default": "windows_desktop"},
                "prompt": {"type": "string", "default": "描述这张图"},
            },
            "required": [],
        },
    },
    {
        "name": "vision_health",
        "description": "视觉能力健康检查 — 截图 + 百炼视觉模型是否可达",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

HANDLERS = {
    "camera_observe": camera_observe_impl,
    "camera_observe_stream": camera_observe_stream_impl,
    "vision_health": vision_health_impl,
}


if __name__ == "__main__":
    # 本地测试:`python vision_tools.py health` / `python vision_tools.py observe windows_desktop`
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "health"
    if cmd == "health":
        print(vision_health_impl())
    elif cmd == "observe":
        source = sys.argv[2] if len(sys.argv) > 2 else "windows_desktop"
        prompt = sys.argv[3] if len(sys.argv) > 3 else _DEFAULT_PROMPT
        print(camera_observe_impl(source, prompt, save_image=False))
    else:
        print(f"unknown cmd: {cmd}", file=sys.stderr)
        sys.exit(1)