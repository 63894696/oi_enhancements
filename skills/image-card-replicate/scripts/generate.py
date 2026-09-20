# -*- coding: utf-8 -*-
"""image-card-replicate — 调 Replicate API 生成图。"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error


def _api(method, url, body=None, token=None, timeout=30):
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--model", default="")
    ap.add_argument("--seed", type=int, default=-1)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--output", default="")
    ap.add_argument("--api-token", default=os.environ.get("REPLICATE_API_TOKEN", ""))
    args = ap.parse_args()

    if not args.api_token:
        # M3.33 #67 stub:无 token 写 1024x1024 占位 PNG + prompt 进 metadata
        ts = time.strftime("%Y%m%d_%H%M%S")
        if not args.output:
            args.output = os.path.join("generated", f"replicate_{ts}.png")
        try:
            from PIL import Image, PngImagePlugin
        except ImportError:
            print("[FAIL] Pillow 未装:pip install Pillow", file=sys.stderr)
            return 11
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        img = Image.new("RGB", (args.width, args.height), color=(64, 96, 128))
        meta = PngImagePlugin.PngInfo()
        meta.add_text("prompt", args.prompt)
        meta.add_text("stub", "true (REPLICATE_API_TOKEN 未设,占位图)")
        img.save(args.output, pnginfo=meta)
        print(f"[ok-stub] saved → {args.output} ({args.width}x{args.height}) "
              f"(无 REPLICATE_API_TOKEN,占位 PNG;真生成需 export REPLICATE_API_TOKEN=...)")
        return 0
    if not args.model:
        # 按 prompt 长度猜
        if len(args.prompt) < 50:
            args.model = "black-forest-labs/flux-schnell"
        else:
            args.model = "black-forest-labs/flux-dev"
        print(f"[info] model auto-selected: {args.model}")

    # Replicate 用 owner/name:pinned_version 或 owner/name@version; 简化用 latest tag
    body = {
        "input": {
            "prompt": args.prompt,
            "width": args.width,
            "height": args.height,
        }
    }
    if args.seed >= 0:
        body["input"]["seed"] = args.seed
    code, txt = _api("POST", f"https://api.replicate.com/v1/models/{args.model}/predictions",
                     body=body, token=args.api_token, timeout=15)
    if code not in (200, 201):
        print(f"[FAIL] submit HTTP {code}: {txt[:200]}", file=sys.stderr)
        return 2
    try:
        j = json.loads(txt)
    except json.JSONDecodeError as e:
        print(f"[FAIL] JSON: {e}", file=sys.stderr)
        return 3
    pid = j.get("id")
    if not pid:
        print(f"[FAIL] no id in response: {txt[:200]}", file=sys.stderr)
        return 4
    print(f"[ok] submitted id={pid}, polling...")

    # poll
    t0 = time.time()
    while time.time() - t0 < 120:
        code, txt = _api("GET", f"https://api.replicate.com/v1/predictions/{pid}",
                         token=args.api_token, timeout=10)
        if code != 200:
            time.sleep(2)
            continue
        try:
            j = json.loads(txt)
        except json.JSONDecodeError:
            time.sleep(2)
            continue
        status = j.get("status")
        if status == "succeeded":
            outputs = j.get("output") or []
            if not outputs:
                print("[FAIL] no output in result", file=sys.stderr)
                return 5
            url = outputs[0]
            break
        elif status in ("failed", "canceled"):
            print(f"[FAIL] prediction {status}: {j.get('error','')}", file=sys.stderr)
            return 6
        time.sleep(2)
    else:
        print("[FAIL] poll timeout 120s", file=sys.stderr)
        return 7

    # 下载
    if not args.output:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = os.path.join("generated", f"replicate_{ts}.png")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read()
    except Exception as e:
        print(f"[FAIL] download: {e}", file=sys.stderr)
        return 8
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"[ok] saved → {args.output}  ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())