# -*- coding: utf-8 -*-
"""image-card-openai — 调 OpenAI Images API。"""
import argparse, base64, json, os, sys, time, urllib.request, urllib.error


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
    ap.add_argument("--model", default="gpt-image-2")
    ap.add_argument("--size", default="1024x1024")
    ap.add_argument("--quality", default="auto")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--output", default="")
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""))
    args = ap.parse_args()

    if not args.api_key:
        print("[FAIL] OPENAI_API_KEY 未设", file=sys.stderr)
        return 1
    body = {
        "model": args.model,
        "prompt": args.prompt,
        "size": args.size,
        "quality": args.quality,
        "n": args.n,
        "response_format": "b64_json",
    }
    code, txt = _api("POST", "https://api.openai.com/v1/images/generations",
                     body=body, token=args.api_key, timeout=120)
    if code != 200:
        print(f"[FAIL] HTTP {code}: {txt[:200]}", file=sys.stderr)
        return 2
    try:
        j = json.loads(txt)
        images = j.get("data", [])
    except json.JSONDecodeError as e:
        print(f"[FAIL] JSON: {e}", file=sys.stderr)
        return 3
    if not images:
        print(f"[FAIL] no image in response: {txt[:200]}", file=sys.stderr)
        return 4
    if not args.output:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = os.path.join("generated", f"openai_{ts}.png")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    # 取第一张
    b64 = images[0].get("b64_json", "")
    if not b64:
        print("[FAIL] no b64_json in first image", file=sys.stderr)
        return 5
    data = base64.b64decode(b64)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"[ok] saved → {args.output}  ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())