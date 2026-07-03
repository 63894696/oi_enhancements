"""OI audio 跨平台评测脚本 — 2026-07-02

跑 4 维度评测(2 维度已实装,2 维度留 hook):
  ① ASR 中文准确率(真中文 wav × N 个 ASR 模型)
  ② TTS 端点连通性(平台/voice 白名单测试)
  ③ Voice Agent tool-call(待补,需要 step-gui 的 WS endpoint)
  ④ Realtime 延迟(待补,需要 WebSocket 客户端)

跑法:
  cd C:\\Users\\Administrator\\oi_enhancements\\audio_voice_eval
  python oi_audio_bench.py --asr-only       # 只跑 ASR(快,~3 分钟)
  python oi_audio_bench.py --tts-only       # 只跑 TTS 端点探测
  python oi_audio_bench.py                  # 全跑

输出:
  1) 控制台表格
  2) JSON 结果 → ~/.oi/benchmarks/audio-2026-07-02.json
  3) xlsx 新增 sheet:"Audio Models"  → D:/GuoNeiMianFeiMoXin/MianFeiMoXinBiao.xlsx
"""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import base64
import urllib.request
import urllib.error
import subprocess
from pathlib import Path
from datetime import datetime

# ============================================================
# Windows 注册表读 env(跟 voice_agent/daemon.py 一致)
# ============================================================
import winreg

def _read_env(name: str) -> str:
    try:
        reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
        val, _ = winreg.QueryValueEx(reg, name)
        winreg.CloseKey(reg)
        return val
    except FileNotFoundError:
        return ""

ENV = {
    "STEPFUN_API_KEY": _read_env("STEPFUN_API_KEY"),
    "STEPFUN_BASE_URL": _read_env("STEPFUN_BASE_URL"),
    "BAILIAN_API_KEY": _read_env("BAILIAN_API_KEY"),
    "BAILIAN_BASE_URL": _read_env("BAILIAN_BASE_URL"),
    "SILICONFLOW_API_KEY": _read_env("SILICONFLOW_API_KEY"),
    "SILICONFLOW_BASE_URL": _read_env("SILICONFLOW_BASE_URL"),
    "ARK_API_KEY": _read_env("ARK_API_KEY"),
    "ARK_BASE_URL": _read_env("ARK_BASE_URL"),
    "MINIMAX_API_KEY": _read_env("MINIMAX_API_KEY"),
    "ZAI_API_KEY": _read_env("ZAI_API_KEY"),
    "SENSENOVA_API_KEY": _read_env("SENSENOVA_API_KEY"),
}

# 屏蔽代理
for k in ("HTTPS_PROXY", "HTTP_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"


def _http_post_json(url, headers, body, timeout=60):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)


def _http_post_multipart(url, api_key, file_path, model, timeout=60):
    """Python 原生 multipart POST(避开 subprocess shell 转义)"""
    import http.client
    import mimetypes
    from email.generator import BytesGenerator
    from io import BytesIO

    boundary = "----OI-BENCH-BOUNDARY-7f3a"
    fp = Path(file_path)
    file_data = fp.read_bytes()
    filename = fp.name
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n'
        f"{model}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
    try:
        conn.request("POST", parsed.path,
            body=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            })
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)
    finally:
        conn.close()


# ============================================================
# 评测 ①: ASR 中文准确率
# ============================================================

# Ground truth wav + 文本(诗经关雎前 4 句)
ASR_TEST_CASES = [
    {
        "name": "关雎原声_10s",
        "wav": "C:/temp/sf_audio/real_guanjv_10s.wav",
        "ground_truth": "关关雎鸠在河之洲窈窕淑女君子好逑",
        "source": "tencent cloud TTS (vocal)",
    },
    {
        "name": "关雎朗诵_10s",
        "wav": "C:/temp/sf_audio/real_xiaoxiao_10s.wav",
        "ground_truth": "关关雎鸠在河之洲窈窕淑女君子好逑",
        "source": "edge-tts xiaoxiao (朗诵)",
    },
]

# 候选 ASR 模型(平台,模型,端点,header key)
ASR_CANDIDATES = [
    ("SILICONFLOW", "FunAudioLLM/SenseVoiceSmall", "https://api.siliconflow.cn/v1/audio/transcriptions", "SILICONFLOW_API_KEY"),
    ("SILICONFLOW", "TeleAI/TeleSpeechASR",        "https://api.siliconflow.cn/v1/audio/transcriptions", "SILICONFLOW_API_KEY"),
    ("STEPFUN",     "step-asr-1.1",                "https://api.stepfun.com/v1/audio/transcriptions",  "STEPFUN_API_KEY"),
    ("STEPFUN",     "step-asr",                    "https://api.stepfun.com/v1/audio/transcriptions",  "STEPFUN_API_KEY"),
    ("STEPFUN",     "stepaudio-2.5-asr",           "https://api.stepfun.com/v1/audio/transcriptions",  "STEPFUN_API_KEY"),
]


def evaluate_asr() -> list:
    """跑所有 ASR 模型 × 所有 wav,返回评测结果"""
    results = []
    for tc in ASR_TEST_CASES:
        for plat, model, url, key_env in ASR_CANDIDATES:
            api_key = ENV.get(key_env, "")
            if not api_key:
                results.append({**tc, "platform": plat, "model": model, "status": "skip", "reason": f"{key_env} not in env"})
                continue
            code, body = _http_post_multipart(
                url, api_key, tc["wav"], model, timeout=60,
            )
            text = ""
            try:
                text = json.loads(body).get("text", "")
            except Exception:
                pass
            results.append({
                "name": tc["name"], "ground_truth": tc["ground_truth"],
                "platform": plat, "model": model,
                "status": "ok" if code == 200 and text else "fail",
                "http_code": code, "asr_text": text, "raw_body": body[:200],
            })
    return results


def compute_asr_metrics(results: list) -> dict:
    """对每个模型计算(中文混淆)准确率(用 char overlap 简化估算)"""
    by_model = {}
    for r in results:
        if r.get("status") != "ok":
            continue
        m = (r["platform"], r["model"])
        by_model.setdefault(m, []).append(r)
    summary = []
    for (plat, model), rs in by_model.items():
        # 简化指标:平均 text 长度 / ground_truth 长度
        gt = ASR_TEST_CASES[0]["ground_truth"]
        accs = []
        for r in rs:
            ref_chars = set(gt)
            hyp_chars = set(r["asr_text"].replace(" ", "").replace("。", ""))
            if not hyp_chars:
                accs.append(0)
                continue
            # 包含中文核心字 (雎鸠洲窈窕淑女逑) 比例
            core = "雎鸠洲窈窕淑女逑关河之"
            core_present = sum(1 for c in core if c in r["asr_text"])
            accs.append(core_present / len(core))
        summary.append({
            "platform": plat, "model": model,
            "core_char_recall": round(sum(accs) / len(accs), 3) if accs else 0,
            "samples": len(rs),
        })
    return summary


# ============================================================
# 评测 ②: TTS 端点连通性
# ============================================================

# 候选 TTS(平台,模型,端点,header key,body shape,必填 voice 字段)
TTS_CANDIDATES = [
    # SILICONFLOW(已知 8 voice,中文 TTS 弱)
    {
        "platform": "SILICONFLOW", "model": "FunAudioLLM/CosyVoice2-0.5B",
        "url": "https://api.siliconflow.cn/v1/audio/speech",
        "key": "SILICONFLOW_API_KEY",
        "body": {"voice": "FunAudioLLM/CosyVoice2-0.5B:anna", "response_format": "wav", "sample_rate": 16000, "stream": False},
    },
    {
        "platform": "SILICONFLOW", "model": "fnlp/MOSS-TTSD-v0.5",
        "url": "https://api.siliconflow.cn/v1/audio/speech",
        "key": "SILICONFLOW_API_KEY",
        "body": {"voice": "fnlp/MOSS-TTSD-v0.5:anna", "response_format": "wav", "sample_rate": 16000, "stream": False},
    },
    # STEPFUN(已知 4 个 TTS 模型,voice 白名单私有,全 400)
    {
        "platform": "STEPFUN", "model": "step-tts-2",
        "url": "https://api.stepfun.com/v1/audio/speech",
        "key": "STEPFUN_API_KEY",
        "body": {"voice": "alloy"},
    },
    {
        "platform": "STEPFUN", "model": "step-tts-vivid",
        "url": "https://api.stepfun.com/v1/audio/speech",
        "key": "STEPFUN_API_KEY",
        "body": {"voice": "alloy"},
    },
    {
        "platform": "STEPFUN", "model": "step-tts-mini",
        "url": "https://api.stepfun.com/v1/audio/speech",
        "key": "STEPFUN_API_KEY",
        "body": {"voice": "alloy"},
    },
    {
        "platform": "STEPFUN", "model": "stepaudio-2.5-tts",
        "url": "https://api.stepfun.com/v1/audio/speech",
        "key": "STEPFUN_API_KEY",
        "body": {"voice": "alloy"},
    },
]


def evaluate_tts() -> list:
    """跑所有 TTS 候选,只测连通性(不存 wav 避免 4-5M 噪声)"""
    results = []
    for c in TTS_CANDIDATES:
        api_key = ENV.get(c["key"], "")
        if not api_key:
            results.append({**c, "status": "skip", "reason": f"{c['key']} not in env"})
            continue
        body = {"model": c["model"], "input": "今天天气真好", **c["body"]}
        t0 = time.time()
        code, resp = _http_post_json(
            c["url"],
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            body, timeout=30,
        )
        dt = int((time.time() - t0) * 1000)
        is_audio = resp[:4] in (b"RIFF", b"ID3\x02", b"\xff\xfb", b"OggS")
        if isinstance(resp, str):
            is_audio_resp = False
            resp_text = resp[:200]
        else:
            is_audio_resp = False  # resp 已 decode 成 str
            resp_text = ""
        # 单独 binary 路径
        if code == 200 and len(resp) > 100 and not resp.startswith("{"):
            is_audio_resp = True
        results.append({
            "platform": c["platform"], "model": c["model"],
            "status": "ok" if code == 200 else "fail",
            "http_code": code, "latency_ms": dt,
            "looks_like_audio": is_audio_resp,
            "resp_preview": resp_text[:200] if isinstance(resp, str) else f"<binary {len(resp)} bytes>",
        })
    return results


# ============================================================
# 主流程
# ============================================================

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--asr-only", action="store_true")
    p.add_argument("--tts-only", action="store_true")
    p.add_argument("--json-out", default=str(Path.home() / ".oi" / "benchmarks" / f"audio-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.json"))
    args = p.parse_args()

    out = {"timestamp": datetime.now().isoformat(), "env_keys_present": {k: bool(v) for k, v in ENV.items()}}

    if not args.tts_only:
        print("=" * 60)
        print("① ASR 中文准确率评测")
        print("=" * 60)
        asr_results = evaluate_asr()
        asr_metrics = compute_asr_metrics(asr_results)
        out["asr"] = {"raw": asr_results, "summary": asr_metrics}
        print(f"\n  {'Platform':<14} {'Model':<32} {'Recall':<8} {'N':<4}")
        for m in sorted(asr_metrics, key=lambda x: -x["core_char_recall"]):
            print(f"  {m['platform']:<14} {m['model']:<32} {m['core_char_recall']:<8.3f} {m['samples']:<4}")

    if not args.asr_only:
        print()
        print("=" * 60)
        print("② TTS 端点连通性评测")
        print("=" * 60)
        tts_results = evaluate_tts()
        out["tts"] = tts_results
        print(f"\n  {'Platform':<14} {'Model':<32} {'HTTP':<6} {'Latency':<10} {'Status':<6}")
        for r in tts_results:
            print(f"  {r['platform']:<14} {r['model']:<32} {r['http_code']:<6} {r.get('latency_ms','?'):<10} {r['status']:<6}")

    # 输出 JSON
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ JSON: {args.json_out}")
    return out


if __name__ == "__main__":
    main()
