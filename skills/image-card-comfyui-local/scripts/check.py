# -*- coding: utf-8 -*-
"""image-card-comfyui-local 前置检查 — 跑 generate 前先验证环境。"""
import os
import sys
import urllib.request
import urllib.error

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
WF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workflows")


def check():
    msgs = []
    # 1. ComfyUI 服务
    try:
        with urllib.request.urlopen(f"{COMFYUI_URL}/system_stats", timeout=5) as r:
            ok = r.status == 200
            body = r.read().decode("utf-8", errors="replace")[:120]
            msgs.append(("ok" if ok else "fail",
                         f"ComfyUI at {COMFYUI_URL} → HTTP {r.status} {body}"))
    except urllib.error.URLError as e:
        msgs.append(("fail", f"ComfyUI at {COMFYUI_URL} 不可达: {e}"))
    except Exception as e:
        msgs.append(("fail", f"ComfyUI 检测异常: {e}"))
    # 2. workflow 目录 + 默认模板
    if not os.path.isdir(WF_DIR):
        msgs.append(("warn", f"workflows/ 目录不存在: {WF_DIR};需要 ComfyUI UI 里 Save (API Format) 后放进来"))
    else:
        wfs = [f for f in os.listdir(WF_DIR) if f.endswith(".json")]
        if not wfs:
            msgs.append(("warn", "workflows/ 目录无 .json 模板"))
        else:
            msgs.append(("ok", f"workflow 模板: {wfs}"))
    # 3. NVIDIA GPU(可选,nvidia-smi)
    import shutil, subprocess
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.free", "--format=csv,noheader"],
                                          text=True, timeout=5)
            msgs.append(("ok", f"GPU: {out.strip().splitlines()[0]}"))
        except Exception:
            pass
    else:
        msgs.append(("info", "nvidia-smi 不在 PATH(不影响,但生成时可能 OOM 难诊断)"))
    return msgs


if __name__ == "__main__":
    fail = False
    for level, msg in check():
        print(f"[{level}] {msg}")
        if level == "fail":
            fail = True
    sys.exit(1 if fail else 0)