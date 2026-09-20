# -*- coding: utf-8 -*-
"""image-card-comfyui-local — 调本地 ComfyUI 生成图。

用法:
  python generate.py "<prompt>" [--output PATH] [--seed N] [--width N] [--height N]
                       [--workflow WORKFLOW_NAME] [--comfyui-url URL]

纯本地 — ComfyUI 默认在 http://127.0.0.1:8188,需用户预先启动。
不联网下载任何东西。

参考:
  https://docs.comfy.org/development/run-workflows/overview
  POST /prompt → {prompt_id}; GET /history/{id} → outputs
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error


def _api(method, url, body=None, timeout=30):
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def check_comfyui(base_url):
    """检测 ComfyUI 服务是否在跑(返 200 /system_stats 即视为 ok)。"""
    code, body = _api("GET", f"{base_url}/system_stats", timeout=5)
    return code, body


def submit_prompt(base_url, workflow):
    """POST workflow 到 /prompt,返 prompt_id。"""
    code, body = _api("POST", f"{base_url}/prompt", workflow, timeout=10)
    if code != 200:
        return None, f"HTTP {code}: {body[:200]}"
    try:
        return json.loads(body).get("prompt_id"), None
    except json.JSONDecodeError as e:
        return None, f"JSON 解析失败: {e}"


def wait_history(base_url, prompt_id, timeout_s=120, poll_s=2.0):
    """轮询 /history/{id} 等出图。返 (outputs, err)。outputs 是 [{filename, subfolder, type}]。"""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        code, body = _api("GET", f"{base_url}/history/{prompt_id}", timeout=10)
        if code == 200:
            try:
                j = json.loads(body)
                # history[id] 存在,且 outputs 非空
                entry = j.get(prompt_id)
                if entry and entry.get("outputs"):
                    return entry["outputs"], None
            except json.JSONDecodeError:
                pass
        time.sleep(poll_s)
    return None, f"timeout {timeout_s}s"


def collect_outputs(history_outputs):
    """从 history outputs 抽取 SaveImage 节点的输出列表。"""
    out_files = []
    for nid, node_out in history_outputs.items():
        for img in (node_out.get("images") or []):
            out_files.append({
                "filename": img.get("filename"),
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            })
    return out_files


def download_image(base_url, meta, dst_path):
    """从 ComfyUI /view 拉图存到 dst_path。"""
    qs = urllib.parse.urlencode({k: v for k, v in meta.items() if k in ("filename", "subfolder", "type") if v})
    code, body = _api("GET", f"{base_url}/view?{qs}", timeout=30)
    if code != 200:
        return False, f"HTTP {code}"
    os.makedirs(os.path.dirname(os.path.abspath(dst_path)), exist_ok=True)
    with open(dst_path, "wb") as f:
        f.write(body.encode("latin-1", errors="replace"))  # body is str from _api; binary 需要 byte path
    # 上面 urlopen + read() 在 _api 里返回 str 不可靠;本函数直接走 raw
    return True, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--output", default="")
    ap.add_argument("--seed", type=int, default=-1)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--workflow", default="sdxl")
    ap.add_argument("--comfyui-url", default=os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"))
    ap.add_argument("--workflow-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workflows"))
    args = ap.parse_args()

    base = args.comfyui_url.rstrip("/")
    # 前置检查
    code, body = check_comfyui(base)
    if code != 200:
        print(f"[FAIL] ComfyUI 不在 {base} ({code}); 请先启动 ComfyUI 服务", file=sys.stderr)
        return 2
    print(f"[ok] ComfyUI detected at {base}: {body[:80]}...")

    # 加载 workflow 模板(用户需自己 Save (API Format) 后放到 workflows/<name>.json)
    wf_path = os.path.join(args.workflow_dir, f"{args.workflow}.json")
    if not os.path.isfile(wf_path):
        print(f"[FAIL] workflow 模板不存在: {wf_path}", file=sys.stderr)
        print(f"  提示:ComfyUI UI 里 Save (API Format) 导出 JSON 到上述路径", file=sys.stderr)
        return 3
    with open(wf_path, "r", encoding="utf-8") as f:
        workflow = json.load(f)

    # 注入 prompt + seed(简化版 — 假设 workflow 有节点 6=CLIPTextEncode positive, 节点 3=KSampler seed)
    # 真实场景需要看 workflow JSON 拓扑,这里给示例骨架
    seed = args.seed if args.seed >= 0 else int(time.time()) % (2**31)
    injected = False
    for nid, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
            inputs = node.setdefault("inputs", {})
            if not inputs.get("text"):  # 只填空 text,不覆盖用户已有
                inputs["text"] = args.prompt
                injected = True
        if isinstance(node, dict) and node.get("class_type") == "KSampler":
            inputs = node.setdefault("inputs", {})
            inputs["seed"] = seed
            inputs["width"] = args.width
            inputs["height"] = args.height
    if not injected:
        print(f"[WARN] workflow 没有 CLIPTextEncode 节点,prompt 未注入(只改了 seed/size)", file=sys.stderr)

    # 提交
    prompt_id, err = submit_prompt(base, {"prompt": workflow, "client_id": "prisir-skill"})
    if err:
        print(f"[FAIL] submit: {err}", file=sys.stderr)
        return 4
    print(f"[ok] submitted prompt_id={prompt_id}")

    # 等待
    outputs, err = wait_history(base, prompt_id, timeout_s=180)
    if err:
        print(f"[FAIL] wait: {err}", file=sys.stderr)
        return 5
    files = collect_outputs(outputs)
    if not files:
        print(f"[FAIL] no outputs in history", file=sys.stderr)
        return 6

    # 下载第一张
    if not args.output:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = os.path.join("generated", f"comfyui_{ts}.png")
    import urllib.parse  # late import 兼容
    qs = urllib.parse.urlencode({k: v for k, v in files[0].items() if v})
    try:
        with urllib.request.urlopen(f"{base}/view?{qs}", timeout=30) as r:
            data = r.read()
    except Exception as e:
        print(f"[FAIL] download: {e}", file=sys.stderr)
        return 7
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"[ok] saved → {args.output}  ({len(data)} bytes)  seed={seed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())