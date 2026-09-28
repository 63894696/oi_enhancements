"""vision_tools.py — v0.23.4 Prisiragent 视觉观察 MCP tool 后端

走"工具不重复"原则:
- 不重写截图,走 PowerShell + System.Drawing(本机 Windows 通用)
- 不重写视觉 LLM,走 cc-switch 15721 + 已有的 model_providers
- ✅ 新:camera_observe 单帧/连续观察
- ✅ 新:source 支持 windows_desktop / mumu_screencap(adb)
- ✅ 新:base64 编码 → 视觉 LLM(走 cc-switch claude-opus-4-8 vision 或 qwen-vl-max)
- ✅ M3.77:vision_query 加 laya_guard fast-path 拦截 prompt injection
         (复用 projects/laya_guard/src/laya_guard.py)

环境变量:
- AUREON_VISION_DEFAULT_SOURCE: 默认 source,默认 "windows_desktop"
- AUREON_VISION_DEFAULT_PROMPT: 默认 prompt,默认 "描述这张图"
- AUREON_VISION_MODEL: 视觉 LLM,默认走 cc-switch qwen-vl-max(便宜 + 中文好)
- AUREON_VISION_NO_LAYA_GUARD: 设 "1" 禁用 laya_guard(默认启用)
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

log = logging.getLogger("prisiragent.vision_tools")

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
# 1.5 laya_guard 懒加载(M3.77) — vision_query 入口拦截 prompt injection
# ─────────────────────────────────────────────────
# 设计:
#   - 单进程单 Router 单例 cache(与其它程序共享 laya 模型)
#   - laya 未加载 → fail-closed 返 risk="medium"(不拦截,只 warn)
#   - 失败 prompt → risk=high/critical → 短路 vision_query 不调 LLM
#   - AUREON_VISION_NO_LAYA_GUARD=1 → 完全跳过(测试 / 离线用)

_PROJECTS_DIR = Path("C:/Users/Administrator/oi_enhancements/projects")
_LAYA_GUARD_SRC = _PROJECTS_DIR / "laya_guard" / "src"
if str(_LAYA_GUARD_SRC) not in sys.path:
    sys.path.insert(0, str(_LAYA_GUARD_SRC))

# lazy import state
_GUARD_OK = False           # laya 模块导入成功
_GUARD_LOADED = False       # 尝试加载过(laya 未安装则不再重试)
_GUARD_IMPORT_ERROR: str | None = None


def _probe_laya_guard() -> None:
    """启动时探测 laya_guard 是否可用。结果存到模块级单例。"""
    global _GUARD_OK, _GUARD_LOADED, _GUARD_IMPORT_ERROR
    if _GUARD_LOADED:
        return
    _GUARD_LOADED = True
    try:
        from laya_guard import guard, decide  # noqa: F401
        _GUARD_OK = True
    except Exception as e:  # noqa: BLE001
        _GUARD_OK = False
        _GUARD_IMPORT_ERROR = f"{type(e).__name__}: {e}"
        log.warning(f"laya_guard 不可用: {_GUARD_IMPORT_ERROR} → vision_guard 走 fail-closed")


def vision_guard(prompt: str) -> dict:
    """vision LLM prompt 预检 — 拦截 jailbreak / injection / sensitive。

    Args:
        prompt: 用户给视觉 LLM 的指令文本

    Returns:
        dict:
          - risk: "safe" / "low" / "medium" / "high" / "critical"
          - jailbreak / injection / sensitive / harm / topic(同 laya_guard)
          - decision: "allow" / "ask" / "deny"
          - decision_reason: 简要原因
          - latency_ms
          - backend: "laya" / "fail-closed"
          - parse_fail: bool
          - enabled: bool(AUREON_VISION_NO_LAYA_GUARD=1 时=False,跳过全部)
    """
    no_laya = os.environ.get("AUREON_VISION_NO_LAYA_GUARD", "") == "1"
    if no_laya:
        return {
            "risk": "unknown",
            "jailbreak": 0.0,
            "injection": 0.0,
            "sensitive": 0.0,
            "harm": 0.0,
            "topic": None,
            "decision": "allow",
            "decision_reason": "laya_guard disabled by env",
            "latency_ms": 0.0,
            "backend": "disabled",
            "parse_fail": False,
            "enabled": False,
        }

    _probe_laya_guard()
    if not _GUARD_OK:
        # fail-closed:不拦截,只是不预警
        return {
            "risk": "medium",
            "jailbreak": 0.0,
            "injection": 0.0,
            "sensitive": 0.0,
            "harm": 0.0,
            "topic": None,
            "decision": "allow",
            "decision_reason": f"laya_guard unavailable: {_GUARD_IMPORT_ERROR}",
            "latency_ms": 0.0,
            "backend": "fail-closed",
            "parse_fail": True,
            "enabled": True,
        }

    try:
        from laya_guard import guard, decide
        result = guard(prompt)
        decision = decide(result)
        return {
            "risk": result.get("risk"),
            "jailbreak": result.get("jailbreak", 0.0),
            "injection": result.get("injection", 0.0),
            "sensitive": result.get("sensitive", 0.0),
            "harm": result.get("harm", 0.0),
            "topic": result.get("topic"),
            "decision": decision.get("decision", "allow"),
            "decision_reason": decision.get("reason", ""),
            "latency_ms": result.get("latency_ms", 0.0),
            "backend": result.get("backend", "laya"),
            "parse_fail": result.get("parse_fail", False),
            "enabled": True,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("vision_guard 调用失败")
        return {
            "risk": "medium",
            "jailbreak": 0.0,
            "injection": 0.0,
            "sensitive": 0.0,
            "harm": 0.0,
            "topic": None,
            "decision": "allow",
            "decision_reason": f"vision_guard error: {type(e).__name__}: {e}",
            "latency_ms": 0.0,
            "backend": "fail-closed",
            "parse_fail": True,
            "enabled": True,
        }


# ─────────────────────────────────────────────────
# 1.6 laya captioner 懒加载(M3.81) — vision_query race_impl 4 分类 fast-path
# ─────────────────────────────────────────────────
# 设计:
#   - 单进程单 Router 单例 cache(复用 laya_guard 同模型)
#   - laya 不可用 → fail-open (走原 urllib qwen-vl-max)
#   - 4 分类:app / focused / code / dialog(描述用户期望看到的屏幕内容类型)
#   - race_impl 逻辑:
#       * laya 预测 "code" 且 conf >= 0.55 → fast-path (max_tokens=30,~1s)
#       * laya 预测 "dialog" 且 conf >= 0.30 → fast-path
#       * 其它 → 走完整 qwen-vl-max (max_tokens=1024,~2-4s)
#   - AUREON_VISION_NO_CAPTIONER=1 → 完全跳过

_CAPTIONER_OK = False
_CAPTIONER_LOADED = False
_CAPTIONER_IMPORT_ERROR: str | None = None
_CAPTIONER_ROUTER = None
_CAPTIONER_QS = {
    "screen_type": {
        "type": "choice",
        "instructions": "Predict what type of screen content the user is asking about in `prompt`.",
        "criteria": {
            "app": "a desktop application window (browser, file manager, IDE overall framework with menus/toolbars)",
            "focused": "a focused text input or terminal command prompt in progress",
            "code": "source code or technical text content (programming, scripts, markup, configs)",
            "dialog": "a modal dialog, alert, popup, or system message overlay",
        },
    }
}
# fast-path 阈值 (per-class,基于 M3.81 bench 500 条:code 83% / dialog 28% / app 57% / focused 60%)
_CAPTIONER_THRESHOLDS = {
    "code": 0.55,    # code 类 83% ACC,高阈值即真用
    "dialog": 0.30,  # dialog 类 28% ACC,只高置信度才信
    "app": 0.65,     # app 57% ACC,稍高阈值
    "focused": 0.60, # focused 60% ACC
}


def _probe_captioner() -> None:
    """启动时探测 laya captioner Router 是否可用。"""
    global _CAPTIONER_OK, _CAPTIONER_LOADED, _CAPTIONER_IMPORT_ERROR, _CAPTIONER_ROUTER
    if _CAPTIONER_LOADED:
        return
    _CAPTIONER_LOADED = True
    try:
        from laya import Router  # noqa: F401
        # 用 multilingual 中文 + English 混合 prompt
        _CAPTIONER_ROUTER = Router(default="multilingual", preload=True)
        _CAPTIONER_OK = True
    except Exception as e:  # noqa: BLE001
        _CAPTIONER_OK = False
        _CAPTIONER_IMPORT_ERROR = f"{type(e).__name__}: {e}"
        log.warning(f"laya captioner 不可用: {_CAPTIONER_IMPORT_ERROR} → race_impl fail-open")


def vision_captioner(prompt: str) -> dict:
    """vision captioner — 用 laya zero-shot 4 分类预测 prompt 期望的屏幕类型。

    Returns:
        dict:
          - choice: "app" / "focused" / "code" / "dialog" / None
          - conf: 0-1 概率
          - probabilities: 4 类概率 dict
          - threshold: 该类的 fast-path 阈值
          - fast_path: bool(是否建议走 fast-path)
          - backend: "laya" / "fail-open"
          - latency_ms
          - parse_fail: bool
          - enabled: bool(AUREON_VISION_NO_CAPTIONER=1 时=False)
    """
    no_captioner = os.environ.get("AUREON_VISION_NO_CAPTIONER", "") == "1"
    if no_captioner:
        return {
            "choice": None,
            "conf": 0.0,
            "probabilities": {},
            "threshold": 0.0,
            "fast_path": False,
            "backend": "disabled",
            "latency_ms": 0.0,
            "parse_fail": False,
            "enabled": False,
            "reason": "AUREON_VISION_NO_CAPTIONER=1",
        }

    _probe_captioner()
    if not _CAPTIONER_OK:
        return {
            "choice": None,
            "conf": 0.0,
            "probabilities": {},
            "threshold": 0.0,
            "fast_path": False,
            "backend": "fail-open",
            "latency_ms": 0.0,
            "parse_fail": True,
            "enabled": True,
            "reason": f"laya unavailable: {_CAPTIONER_IMPORT_ERROR}",
        }

    t0 = time.time()
    try:
        res = _CAPTIONER_ROUTER.predict({"prompt": prompt}, _CAPTIONER_QS)
        elapsed_ms = (time.time() - t0) * 1000
        answers = res.get("answers", {})
        st = answers.get("screen_type", {})
        choice = st.get("choice")
        probs = st.get("probabilities", {})
        conf = st.get("answer_confidence") or st.get("confidence") or 0.0

        if not choice or choice not in _CAPTIONER_THRESHOLDS:
            return {
                "choice": None,
                "conf": 0.0,
                "probabilities": probs,
                "threshold": 0.0,
                "fast_path": False,
                "backend": "laya",
                "latency_ms": elapsed_ms,
                "parse_fail": True,
                "enabled": True,
                "reason": "no valid choice",
            }

        threshold = _CAPTIONER_THRESHOLDS[choice]
        fast_path = conf >= threshold
        return {
            "choice": choice,
            "conf": round(conf, 4),
            "probabilities": {k: round(v, 4) for k, v in probs.items()},
            "threshold": threshold,
            "fast_path": fast_path,
            "backend": "laya",
            "latency_ms": round(elapsed_ms, 1),
            "parse_fail": False,
            "enabled": True,
            "reason": f"{choice} conf={conf:.3f} >= {threshold}",
        }
    except Exception as e:  # noqa: BLE001
        log.exception("vision_captioner 调用失败")
        return {
            "choice": None,
            "conf": 0.0,
            "probabilities": {},
            "threshold": 0.0,
            "fast_path": False,
            "backend": "fail-open",
            "latency_ms": (time.time() - t0) * 1000,
            "parse_fail": True,
            "enabled": True,
            "reason": f"{type(e).__name__}: {e}",
        }


# ─────────────────────────────────────────────────
# 2. 视觉 LLM(走 cc-switch OpenAI 兼容)
# ─────────────────────────────────────────────────

def _qwen_vl_call(png_bytes: bytes, prompt: str, model: str, max_tokens: int = 1024,
                  temperature: float = 0.3) -> dict:
    """真实调百炼 qwen-vl-max (内部用)。"""
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
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

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
        "max_tokens": max_tokens,
    }


def vision_query(png_bytes: bytes, prompt: str, model: str = _VISION_MODEL) -> dict:
    """调百炼 qwen-vl-max 视觉模型,返回文字描述

    走 OpenAI 兼容 chat/completions + image_url(data:image/png;base64,...)
    直连百炼(不走 cc-switch,cc-switch 路由不一定支持 vision 模态)

    M3.77:入口先调 vision_guard(prompt) 拦截 prompt injection。
      - decision=deny  → 短路,不调 LLM,返 ok=False
      - decision=ask   → 放行(在 result 里标记 laya_guard 字段)
      - decision=allow → 正常路径

    M3.81:race_impl — captioner fast-path:
      - laya captioner 预测 prompt 期望的 4 类(基于 prompt 文本,无需图像)
      - conf >= per-class 阈值 → fast-path (max_tokens=30,~1s 节省 ¥)
      - conf < 阈值 → 完整 qwen-vl-max (max_tokens=1024)
      - laya 不可用 / 失败 → fail-open 走完整 qwen-vl-max
      - 结果中含 captioner 字段(backend/conf/fast_path)
    """
    if not png_bytes:
        return {"ok": False, "error": "空图像"}
    if not _BAILIAN_KEY:
        return {"ok": False, "error": "BAILIAN_API_KEY 未设,无法调百炼视觉"}

    # M3.77:laya_guard 拦截
    guard_result = vision_guard(prompt)
    guard_decision = guard_result.get("decision", "allow")
    if guard_decision == "deny":
        log.warning(
            f"vision_guard 拦截 prompt (risk={guard_result.get('risk')}): "
            f"{guard_result.get('decision_reason')} | prompt={prompt[:100]!r}"
        )
        return {
            "ok": False,
            "error": "laya_guard_denied",
            "error_detail": guard_result.get("decision_reason"),
            "laya_guard": guard_result,
            "model": model,
        }

    # M3.81:laya captioner race decision(基于 prompt 文本预测期望屏幕类型)
    captioner_result = vision_captioner(prompt)
    fast_path = bool(captioner_result.get("fast_path"))
    choice = captioner_result.get("choice")

    # fast-path 选 max_tokens
    if fast_path and choice == "code":
        # code 类:用户期望代码片段,fast-path 返回代码分类即可
        max_tokens = 30
        mode = "fast_code"
    elif fast_path and choice == "dialog":
        # dialog 类:用户期望 dialog 文本,max 短
        max_tokens = 40
        mode = "fast_dialog"
    elif fast_path and choice == "app":
        # app 类:用户期望应用窗口概述
        max_tokens = 60
        mode = "fast_app"
    elif fast_path and choice == "focused":
        # focused 类:用户期望输入框文本
        max_tokens = 50
        mode = "fast_focused"
    else:
        max_tokens = 1024
        mode = "full"

    try:
        result = _qwen_vl_call(png_bytes, prompt, model, max_tokens=max_tokens)
        if not result.get("ok"):
            return {**result, "laya_guard": guard_result, "captioner": captioner_result}
        return {
            **result,
            "laya_guard": guard_result,
            "captioner": captioner_result,
            "race_mode": mode,
        }
    except Exception as e:
        log.exception("vision_query failed")
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {str(e)[:300]}",
            "laya_guard": guard_result,
            "captioner": captioner_result,
            "race_mode": mode,
        }


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
            "laya_guard": vision_result.get("laya_guard"),
            "captioner": vision_result.get("captioner"),
            "race_mode": vision_result.get("race_mode"),
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
        "laya_guard": vision_result.get("laya_guard"),
        "captioner": vision_result.get("captioner"),
        "race_mode": vision_result.get("race_mode"),
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
    # M3.81: captioner 状态
    health["captioner"] = {
        "available": _CAPTIONER_OK,
        "error": _CAPTIONER_IMPORT_ERROR,
        "thresholds": _CAPTIONER_THRESHOLDS,
    }
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