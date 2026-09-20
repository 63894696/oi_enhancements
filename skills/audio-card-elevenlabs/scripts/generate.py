# -*- coding: utf-8 -*-
"""audio-card-elevenlabs — 调 ElevenLabs TTS API。"""
import argparse, json, os, sys, time, urllib.request, urllib.error


def _api(method, url, body=None, headers_extra=None, token=None, timeout=30, raw=False):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if headers_extra:
        headers.update(headers_extra)
    data = None
    if body is not None and not raw:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode("utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--voice", default="Rachel")  # default ElevenLabs voice
    ap.add_argument("--model", default="eleven_multilingual_v3")
    ap.add_argument("--output", default="")
    ap.add_argument("--api-key", default=os.environ.get("ELEVENLABS_API_KEY", ""))
    args = ap.parse_args()
    if not args.api_key:
        print("[FAIL] ELEVENLABS_API_KEY 未设", file=sys.stderr)
        return 1
    # 先列 voice 把名字 → voice_id
    code, body = _api("GET", "https://api.elevenlabs.io/v1/voices",
                      token=args.api_key, timeout=10)
    if code != 200:
        print(f"[FAIL] list voices HTTP {code}: {body[:200]}", file=sys.stderr)
        return 2
    try:
        j = json.loads(body)
        voices = {v["name"]: v["voice_id"] for v in j.get("voices", [])}
    except json.JSONDecodeError:
        voices = {}
    voice_id = voices.get(args.voice, args.voice)  # fallback 当作 voice_id 直接用
    if voice_id not in voices.values():
        # 看是否就是 voice_id
        if not any(v["voice_id"] == args.voice for v in j.get("voices", [])):
            print(f"[WARN] voice '{args.voice}' 不在 ElevenLabs 列表,当 voice_id 直接发", file=sys.stderr)
    # 调 TTS
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = {"text": args.text, "model_id": args.model}
    code, data = _api("POST", url, body=body, token=args.api_key, timeout=60)
    if code != 200:
        print(f"[FAIL] TTS HTTP {code}: {data[:200]}", file=sys.stderr)
        return 3
    if not args.output:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = os.path.join("generated", f"elevenlabs_{ts}.mp3")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "wb") as f:
        f.write(data)
    print(f"[ok] saved → {args.output}  ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())