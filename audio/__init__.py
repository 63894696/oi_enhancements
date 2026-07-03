"""OI audio 增强器 — 跨平台 ASR/TTS 统一入口(2026-07-02 ship)

为 OI agent 提供统一 audio 接口,屏蔽 5 个平台的差异:
  - STEPFUN(ASR 强 + TTS voice_id 白名单私有)
  - BAILIAN(ASR+TTS 都强 + voice 公开:Cherry/Ethan 等)
  - SILICONFLOW(ASR 强 + TTS 中文弱)
  - ARK 豆包(可探)
  - MiniMax(TTS 强,字段名易错)

给 OI 提供的 4 个统一方法:
  - asr(wav_path, platform="auto")         → str
  - tts(text, output_path, voice="auto")  → bool
  - chat_with_voice(text)                 → wav_path  (LLM 决策 + TTS 合成)
  - list_platforms()                      → dict      (各平台能力矩阵)

源码:`audio_voice_eval/oi_audio_bench.py` 的评测结果决定默认平台优先级
"""
from __future__ import annotations
import os
import sys
import json
import base64
import urllib.request
import urllib.error
import urllib.parse
import http.client
import mimetypes
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

# ============================================================
# Windows 注册表读 env(与 voice_agent/daemon.py 一致)
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
    "MINIMAX_BASE_URL": "https://api.MiniMax.chat",
    "ZAI_API_KEY": _read_env("ZAI_API_KEY"),
}

# 屏蔽代理
for k in ("HTTPS_PROXY", "HTTP_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"


# ============================================================
# 平台能力矩阵(从 oi_audio_bench.py 评测结果来)
#   ★★★ = 真能用;★★ = 端点通但质量待定;★ = 探活;✗ = 不支持
# ============================================================
PLATFORM_MATRIX = {
    "STEPFUN": {
        "asr": {"models": ["step-asr-1.1", "step-asr", "stepaudio-2.5-asr"],
                 "endpoint": "/v1/audio/transcriptions", "rating": "★★★"},
        "tts": {"models": ["step-tts-2", "step-tts-vivid", "step-tts-mini", "stepaudio-2.5-tts"],
                 "endpoint": "/v1/audio/speech", "rating": "✗(voice_id 白名单私有)"},
        "chat_with_audio": {"models": ["stepaudio-2.5-chat"], "endpoint": "/v1/chat/completions", "rating": "★★(chat 模式 400 audio_url,需走 WS)"},
        "voice_agent": {"models": ["step-gui"], "endpoint": "/v1/chat/completions (WS /v1/realtime)", "rating": "★★(WS 未实装)"},
        "realtime": {"models": ["stepaudio-2.5-realtime"], "endpoint": "/v1/realtime (WS)", "rating": "★(WS 未实装)"},
    },
    "BAILIAN": {
        "asr": {"models": ["qwen3-asr-flash", "qwen3-asr-flash-realtime", "fun-asr-flash"],
                 "endpoint": "/compatible-mode/v1/chat/completions", "rating": "★★★(中文强)"},
        "tts": {"models": ["sambert-zhichu-v1", "sambert-zhixiao-v1", "sambert-zhiting-v1", "sambert-zhimiao-emo-v1"],
                 "endpoint": "dashscope SDK (WS) → qwen3-tts-flash HTTP 走不通,降级 sambert-v1",
                 "rating": "★★★(中文稳,4 个 voice 公开)"},
        "chat_with_audio": {"models": ["qwen3.5-omni-flash", "qwen3-omni-flash"],
                 "endpoint": "/compatible-mode/v1/chat/completions", "rating": "★★★"},
        "voice_agent": {"models": ["qwen3-omni-flash-realtime", "qwen3-s2s-flash-realtime"],
                 "endpoint": "/compatible-mode/v1/realtime (WS)", "rating": "★★"},
        "realtime": {"models": ["qwen3-omni-flash-realtime"], "endpoint": "/compatible-mode/v1/realtime (WS)", "rating": "★"},
    },
    "SILICONFLOW": {
        "asr": {"models": ["FunAudioLLM/SenseVoiceSmall", "TeleAI/TeleSpeechASR"],
                 "endpoint": "/v1/audio/transcriptions", "rating": "★★★(中文佳)"},
        "tts": {"models": ["FunAudioLLM/CosyVoice2-0.5B", "fnlp/MOSS-TTSD-v0.5"],
                 "endpoint": "/v1/audio/speech", "rating": "★(中文 TTS 输出英文/日文,平台模型限制)"},
        "chat_with_audio": {"models": [], "rating": "✗"},
        "voice_agent": {"models": [], "rating": "✗"},
        "realtime": {"models": [], "rating": "✗"},
    },
    "ARK": {
        "asr": {"models": ["doubao-asr(待探)"], "rating": "★(未见官方 ASR 端点)"},
        "tts": {"models": ["doubao-voice(待探)"], "rating": "★(doubao-seed 系 chat 强)"},
        "chat_with_audio": {"models": ["doubao-seed-1-6-flash-250715", "doubao-1-5-vision-pro-32k-250115"],
                 "endpoint": "/v3/chat/completions", "rating": "★★(vision 系支持图像)"},
        "voice_agent": {"models": [], "rating": "✗"},
        "realtime": {"models": [], "rating": "✗"},
    },
    "MINIMAX": {
        "asr": {"models": [], "rating": "✗(无 ASR 端点)"},
        "tts": {"models": ["speech-2.6-hd", "speech-01-turbo"],
                 "endpoint": "/v1/t2a_v2", "rating": "★★(字段易错,字段名 hex 编码)"},
        "chat_with_audio": {"models": [], "rating": "✗"},
        "voice_agent": {"models": [], "rating": "✗"},
        "realtime": {"models": [], "rating": "✗"},
    },
    # 新增 2026-07-02:免费 fallback
    "EDGE_TTS": {
        "asr": {"models": [], "rating": "✗(edge-tts 只做 TTS)"},
        "tts": {"models": ["zh-CN-XiaoxiaoNeural(女)", "zh-CN-YunxiNeural(男)", "zh-CN-YunjianNeural(男)",
                            "zh-CN-YunxiaNeural(男)", "zh-CN-XiaoyiNeural(女)",
                            "zh-HK-HiuGaaiNeural(女)", "zh-HK-HiuMaanNeural(女)", "zh-HK-WanLungNeural(男)",
                            "zh-TW-HsiaoChenNeural(女)", "zh-TW-HsiaoYuNeural(女)", "zh-TW-YunJheNeural(男)"],
                 "endpoint": "Microsoft speech endpoint (WebSocket)",
                 "rating": "★★★★(免费,11 个中文 voice,无需 API key,fallback 第一选择)"},
        "chat_with_audio": {"models": [], "rating": "✗"},
        "voice_agent": {"models": [], "rating": "✗"},
        "realtime": {"models": [], "rating": "✗"},
    },
    "GOOGLE_TRANSLATE": {
        "asr": {"models": [], "rating": "✗"},
        "tts": {"models": [], "rating": "✗"},
        "chat_with_audio": {"models": [], "rating": "✗"},
        "voice_agent": {"models": [], "rating": "✗"},
        "realtime": {"models": [], "rating": "✗"},
        "translate": {"models": ["googletrans 库(免费 100+ 语言)"],
                       "endpoint": "translate.google.com (非官方 API)",
                       "rating": "★★★★(免费,无需 API key,fallback 翻译)"},
    },
}


# ============================================================
# 底层:multipart POST
# ============================================================
def _post_multipart(url: str, api_key: str, file_path: str, model: str, timeout: int = 60) -> tuple:
    boundary = "----OI-AUDIO-BOUNDARY-7f3a"
    fp = Path(file_path)
    file_data = fp.read_bytes()
    ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{fp.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    parsed = urllib.parse.urlparse(url)
    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
    try:
        conn.request("POST", parsed.path, body=body, headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        })
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", errors="ignore")
    finally:
        conn.close()


def _post_json(url: str, api_key: str, body: dict, timeout: int = 60) -> tuple:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return 0, str(e)


# ============================================================
# 底层:Microsoft edge-tts(免费,无需 API key)
# ============================================================
async def _edge_tts_async(text: str, voice: str, out_mp3: Path) -> bool:
    try:
        import edge_tts
    except ImportError:
        return False
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(out_mp3))
        return out_mp3.exists() and out_mp3.stat().st_size > 100
    except Exception:
        return False


def _edge_tts(text: str, out_path: Path, voice: str = "zh-CN-XiaoxiaoNeural") -> dict:
    """Microsoft edge-tts 免费 TTS(同步 wrapper)

    14 个中文 voice(2026-07-02 实测):
      - zh-CN-XiaoxiaoNeural(女)/ XiaoyiNeural(女)/ YunjianNeural(男)/ YunxiNeural(男)/ YunxiaNeural(男)
      - zh-HK-HiuGaaiNeural(女)/ HiuMaanNeural(女)/ WanLungNeural(男)
      - zh-TW-HsiaoChenNeural(女)/ HsiaoYuNeural(女)/ YunJheNeural(男)
      - 还有 4 个其他中文 voice

    限制:
      - 输出 mp3 不是 wav(用 ffmpeg 转 wav 16k mono 给 ASR 友好)
      - 需要网络连接到 Microsoft speech endpoint
      - 免费但有 rate limit(实测无明显)
    """
    try:
        import asyncio
        out_mp3 = out_path.with_suffix(".mp3") if out_path.suffix != ".mp3" else out_path
        # 路径里的 C:/temp/edge_xxx.mp3 → 用 C:/temp/edge_xxx.wav
        if out_path.suffix == ".wav":
            out_mp3 = out_path.with_suffix(".mp3")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ok = loop.run_until_complete(_edge_tts_async(text, voice, out_mp3))
        loop.close()
        if not ok:
            return {"status": "error", "platform": "EDGE", "reason": "edge-tts 调用失败"}
        # 如果用户要 wav,转 wav 16k
        if out_path.suffix == ".wav" and out_mp3.exists():
            import subprocess
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(out_mp3), "-ar", "16000", "-ac", "1", str(out_path)],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0 and out_path.exists():
                out_mp3.unlink(missing_ok=True)
                return {"status": "ok", "platform": "EDGE", "voice": voice,
                        "output": str(out_path), "size_bytes": out_path.stat().st_size,
                        "latency_ms": None}
        return {"status": "ok", "platform": "EDGE", "voice": voice,
                "output": str(out_mp3), "size_bytes": out_mp3.stat().st_size,
                "latency_ms": None}
    except Exception as e:
        return {"status": "error", "platform": "EDGE", "reason": f"{type(e).__name__}: {str(e)[:200]}"}


# ============================================================
# 底层:googletrans 免费翻译(无需 API key)
# ============================================================
def _googletrans_sync(text: str, dest: str = "zh-CN", src: str = "auto") -> dict:
    """Google Translate 免费版(googletrans 库)

    支持:en↔zh↔ja↔ko↔fr↔de↔es↔ru 等 100+ 语言
    限制:有 rate limit(免费),偶尔会 429
    """
    try:
        from googletrans import Translator
    except ImportError:
        return {"status": "unavailable", "reason": "googletrans 未装,pip install googletrans"}
    try:
        import asyncio
        t = Translator()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        r = loop.run_until_complete(t.translate(text, dest=dest, src=src))
        loop.close()
        return {"status": "ok", "platform": "GOOGLE_TRANSLATE",
                "src": r.src, "dest": r.dest, "text": r.text}
    except Exception as e:
        return {"status": "error", "platform": "GOOGLE_TRANSLATE",
                "reason": f"{type(e).__name__}: {str(e)[:200]}"}


# ============================================================
# 统一 ASR 接口
# ============================================================
ASR_AUTO_ORDER = [
    ("BAILIAN", "qwen3-asr-flash", "/compatible-mode/v1/chat/completions", "BAILIAN_API_KEY"),
    ("SILICONFLOW", "FunAudioLLM/SenseVoiceSmall", "https://api.siliconflow.cn/v1/audio/transcriptions", "SILICONFLOW_API_KEY"),
    ("STEPFUN", "step-asr-1.1", "https://api.stepfun.com/v1/audio/transcriptions", "STEPFUN_API_KEY"),
]


def asr(wav_path: str, platform: str = "auto") -> dict:
    """ASR 统一入口

    platform="auto" → 按 ASR_AUTO_ORDER 试到 ok
    platform="STEPFUN"|"BAILIAN"|"SILICONFLOW" → 锁平台(用平台默认模型)
    """
    if platform == "auto":
        for plat, model, url, key_env in ASR_AUTO_ORDER:
            r = asr(wav_path, platform=plat)
            if r.get("status") == "ok":
                return r
        return {"status": "fail", "reason": "all platforms failed"}

    # 锁平台
    if platform == "STEPFUN":
        return _asr_stepfun(wav_path)
    elif platform == "BAILIAN":
        return _asr_bailian(wav_path)
    elif platform == "SILICONFLOW":
        return _asr_siliconflow(wav_path, model="FunAudioLLM/SenseVoiceSmall")
    return {"status": "error", "reason": f"unknown platform {platform}"}


def _asr_siliconflow(wav_path: str, model: str = "FunAudioLLM/SenseVoiceSmall") -> dict:
    api_key = ENV["SILICONFLOW_API_KEY"]
    if not api_key:
        return {"status": "unavailable", "reason": "SILICONFLOW_API_KEY not in env"}
    t0 = time.time()
    code, body = _post_multipart("https://api.siliconflow.cn/v1/audio/transcriptions", api_key, wav_path, model, timeout=60)
    dt = int((time.time() - t0) * 1000)
    if code != 200:
        return {"status": "error", "platform": "SILICONFLOW", "model": model, "http_code": code, "latency_ms": dt, "raw": body[:200]}
    try:
        text = json.loads(body).get("text", "")
    except Exception:
        text = body[:100]
    return {"status": "ok", "platform": "SILICONFLOW", "model": model, "text": text, "latency_ms": dt}


def _asr_stepfun(wav_path: str, model: str = "step-asr-1.1") -> dict:
    api_key = ENV["STEPFUN_API_KEY"]
    if not api_key:
        return {"status": "unavailable", "reason": "STEPFUN_API_KEY not in env"}
    t0 = time.time()
    code, body = _post_multipart("https://api.stepfun.com/v1/audio/transcriptions", api_key, wav_path, model, timeout=60)
    dt = int((time.time() - t0) * 1000)
    if code != 200:
        return {"status": "error", "platform": "STEPFUN", "model": model, "http_code": code, "latency_ms": dt, "raw": body[:200]}
    try:
        text = json.loads(body).get("text", "")
    except Exception:
        text = body[:100]
    return {"status": "ok", "platform": "STEPFUN", "model": model, "text": text, "latency_ms": dt}


def _asr_bailian(wav_path: str) -> dict:
    api_key = ENV["BAILIAN_API_KEY"]
    if not api_key:
        return {"status": "unavailable", "reason": "BAILIAN_API_KEY not in env"}
    # BAILIAN 走 chat completions,把 wav base64 当 audio_url
    audio_b64 = base64.b64encode(Path(wav_path).read_bytes()).decode()
    body = {
        "model": "qwen3-asr-flash",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "请把这段音频转成文字。"},
                {"type": "audio_url", "audio_url": {"url": f"data:audio/wav;base64,{audio_b64}"}},
            ],
        }],
    }
    t0 = time.time()
    code, resp = _post_json("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", api_key, body, timeout=60)
    dt = int((time.time() - t0) * 1000)
    if code != 200:
        return {"status": "error", "platform": "BAILIAN", "http_code": code, "latency_ms": dt, "raw": resp[:200]}
    try:
        text = json.loads(resp)["choices"][0]["message"]["content"]
    except Exception:
        text = resp[:100]
    return {"status": "ok", "platform": "BAILIAN", "model": "qwen3-asr-flash", "text": text, "latency_ms": dt}


# ============================================================
# 统一 TTS 接口
# ============================================================
TTS_AUTO_ORDER = [
    # BAILIAN 优先(voice 公开,中文 TTS 强)
    ("BAILIAN", "qwen3-tts-flash", "Cherry", "/compatible-mode/v1/audio/speech", "BAILIAN_API_KEY",
     lambda text, voice: {"model": "qwen3-tts-flash", "input": text, "voice": voice}),
    # SILICONFLOW 备选(但中文输出英文,别用)
    ("SILICONFLOW", "FunAudioLLM/CosyVoice2-0.5B", "anna", "https://api.siliconflow.cn/v1/audio/speech", "SILICONFLOW_API_KEY",
     lambda text, voice: {"model": "FunAudioLLM/CosyVoice2-0.5B", "input": text, "voice": f"FunAudioLLM/CosyVoice2-0.5B:{voice}", "response_format": "wav", "sample_rate": 16000, "stream": False}),
    # 免费 fallback: Microsoft edge-tts(无需 API key)
    ("EDGE", "zh-CN-XiaoxiaoNeural", "xiaoxiao", None, None, None),
]


def tts(text: str, output_path: str, voice: str = "auto", platform: str = "auto") -> dict:
    """TTS 统一入口
    platform="auto" → 按 TTS_AUTO_ORDER 试(BAILIAN → SILICONFLOW → EDGE 免费 fallback)
    platform="EDGE" → 强制走 edge-tts(免费,11 个中文 voice)
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if platform == "auto":
        for plat, mdl, def_voice, url, key_env, body_fn in TTS_AUTO_ORDER:
            r = tts(text, output_path, voice=voice, platform=plat)
            if r.get("status") == "ok":
                return r
        return {"status": "fail", "reason": "all TTS platforms failed"}

    if platform == "BAILIAN":
        return _tts_bailian(text, out, voice if voice != "auto" else "Cherry")
    elif platform == "SILICONFLOW":
        return _tts_siliconflow(text, out, voice if voice != "auto" else "anna")
    elif platform == "STEPFUN":
        return {"status": "fail", "platform": "STEPFUN", "reason": "voice_id 白名单私有,需要工单拿白名单"}
    elif platform == "EDGE":
        return _edge_tts(text, out, voice if voice not in ("auto", "Cherry", "alloy") else "zh-CN-XiaoxiaoNeural")
    return {"status": "error", "reason": f"unknown platform {platform}"}


def _tts_bailian(text: str, out: Path, voice: str = "Cherry") -> dict:
    """BAILIAN TTS — 2026-07-02 ship

    实测结论:
      - qwen3-tts-flash HTTP 端点全 400,SDK 1.25.24 ModelNotFound(WebSocket 路由 SDK 没实装)
      - sambert-v1 系列(老 API)用 dashscope SDK 走 WS 实测能出 wav
      - 默认 voice 改 sambert 系列(用 Cherry 也回退到 sambert-zhichu-v1)

    Available voices(2026-07-02 本机实测):
      - sambert-zhichu-v1        女声基础
      - sambert-zhixiao-v1       男声
      - sambert-zhiting-v1       女声
      - sambert-zhimiao-emo-v1   情感女声

    Returns {"status":"ok"|"error", "platform":"BAILIAN", "model":voice, "output":..., "size_bytes":..., "latency_ms":...}
    """
    api_key = ENV["BAILIAN_API_KEY"]
    if not api_key:
        return {"status": "unavailable", "reason": "BAILIAN_API_KEY not in env"}
    # Cherry 等 voice 名称不在 SDK 白名单 → 强制映射到 sambert 系列
    if voice in ("Cherry", "Ethan", "Mia", "alloy", "echo", "shimmer", "onyx", "fable", "nova", "default"):
        voice = "sambert-zhichu-v1"  # 默认女声
    try:
        import dashscope
        from dashscope.audio.tts import SpeechSynthesizer
    except ImportError:
        return {"status": "unavailable", "reason": "dashscope SDK not installed"}
    dashscope.api_key = api_key
    t0 = time.time()
    try:
        resp = SpeechSynthesizer.call(model=voice, text=text, format="wav")
        if resp._response.status_code != 200:
            return {"status": "error", "platform": "BAILIAN", "model": voice,
                    "code": resp._response.code, "message": resp._response.message[:200],
                    "latency_ms": int((time.time() - t0) * 1000)}
        audio_bytes = resp.get_audio_data()
        if not audio_bytes:
            return {"status": "error", "platform": "BAILIAN", "model": voice, "reason": "empty audio"}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio_bytes)
        return {"status": "ok", "platform": "BAILIAN", "model": voice, "voice": voice,
                "output": str(out), "size_bytes": len(audio_bytes),
                "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"status": "error", "platform": "BAILIAN", "model": voice,
                "reason": f"{type(e).__name__}: {str(e)[:200]}"}


def _tts_siliconflow(text: str, out: Path, voice: str = "anna") -> dict:
    api_key = ENV["SILICONFLOW_API_KEY"]
    if not api_key:
        return {"status": "unavailable", "reason": "SILICONFLOW_API_KEY not in env"}
    body = {"model": "FunAudioLLM/CosyVoice2-0.5B", "input": text,
            "voice": f"FunAudioLLM/CosyVoice2-0.5B:{voice}",
            "response_format": "wav", "sample_rate": 16000, "stream": False}
    t0 = time.time()
    import subprocess
    cmd = ["curl", "-sS", "-o", str(out), "-w", "%{http_code}",
           "-H", f"Authorization: Bearer {api_key}",
           "-H", "Content-Type: application/json",
           "-d", json.dumps(body),
           "https://api.siliconflow.cn/v1/audio/speech"]
    try:
        result = subprocess.run(cmd, timeout=30, capture_output=True, text=True)
        code = int(result.stdout.strip()) if result.stdout.strip().isdigit() else 0
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    dt = int((time.time() - t0) * 1000)
    if code == 200 and out.exists() and out.stat().st_size > 100:
        return {"status": "ok", "platform": "SILICONFLOW", "model": "FunAudioLLM/CosyVoice2-0.5B", "voice": voice, "output": str(out), "size_bytes": out.stat().st_size, "latency_ms": dt}
    return {"status": "error", "platform": "SILICONFLOW", "http_code": code, "latency_ms": dt}


# ============================================================
# 完整语音对话链(ASR→LLM→TTS)
# ============================================================
def chat_with_voice(text: str, llm_model_platform: str = "STEPFUN", llm_model: str = "step-3.7-flash",
                    output_wav: str = None) -> dict:
    """单轮语音对话(LLM 决策 + TTS 输出)

    输入:用户文本(可来自 ASR 输出)
    输出:LLM 回复 + TTS wav
    """
    if output_wav is None:
        output_wav = str(Path.home() / ".voice_agent" / f"oi_chat_{int(time.time())}.wav")
    # 调 STEPFUN LLM(已知 max_tokens>=4000)
    api_key = ENV["STEPFUN_API_KEY"]
    llm_body = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": "你是用户的语音助手 OI,1-2 句中文回复,不要 markdown。"},
            {"role": "user", "content": text},
        ],
        "max_tokens": 4000,
        "temperature": 0.6,
    }
    t0 = time.time()
    code, resp = _post_json("https://api.stepfun.com/v1/chat/completions", api_key, llm_body, timeout=60)
    if code != 200:
        return {"status": "error", "stage": "llm", "http_code": code, "raw": str(resp)[:200]}
    try:
        m = json.loads(resp)["choices"][0]["message"]
        reply = m.get("content") or m.get("reasoning_content") or ""
    except Exception as e:
        return {"status": "error", "stage": "llm", "reason": str(e)}
    llm_dt = int((time.time() - t0) * 1000)

    # TTS
    tts_r = tts(reply, output_wav)
    return {
        "status": "ok" if tts_r.get("status") == "ok" else "partial",
        "llm_reply": reply,
        "llm_latency_ms": llm_dt,
        "tts": tts_r,
        "output_wav": output_wav,
    }


# ============================================================
# 平台能力查询
# ============================================================
def list_platforms() -> dict:
    """返回所有平台 × 能力的 rating 矩阵"""
    return PLATFORM_MATRIX


def get_best_platform(capability: str) -> str:
    """按 capability 返回最佳平台"""
    order = {
        "asr": ["BAILIAN", "SILICONFLOW", "STEPFUN"],
        "tts": ["BAILIAN", "SILICONFLOW"],  # STEPFUN TTS voice 白名单未解锁
        "chat_with_audio": ["BAILIAN", "STEPFUN"],
        "voice_agent": ["BAILIAN", "STEPFUN"],
    }.get(capability, [])
    for p in order:
        if ENV.get(f"{p}_API_KEY"):
            return p
    return "STEPFUN"  # fallback


# ============================================================
# OI installer(2026-07-02 ship) — 永久写进 system_message
# ============================================================
AUDIO_TOOLS_SYSTEM_MESSAGE = """

# Audio Tools (已挂在 interpreter 上,2026-07-02 ship — 自动加载)

下面 5 个 audio 工具已挂在 Python 全局 `interpreter` 上,**直接调用,不要 pip install**。

调用格式(在 ```python 代码块里):
```python
# ASR:音频转文字
interpreter.audio_asr('C:/path/to.wav')                          # platform='auto'
interpreter.audio_asr('C:/path/to.wav', platform='SILICONFLOW')  # 锁平台

# TTS:文字转语音(默认 BAILIAN sambert,失败 fallback edge-tts)
interpreter.audio_tts('你好', 'C:/out.wav')
interpreter.audio_tts('你好', 'C:/out.wav', voice='sambert-zhixiao-v1')  # 男声
# 想用免费 edge-tts:
import sys
sys.path.insert(0, r'C:/Users/Administrator/oi_enhancements')
from audio import tts as audio_tts
audio_tts('你好', 'C:/out.mp3', voice='zh-CN-XiaoxiaoNeural', platform='EDGE')

# 翻译:Google 免费,100+ 语言
interpreter.audio_translate('Hello, how are you?', dest='zh-CN')
interpreter.audio_translate('今天的天气真好', dest='en')

# 平台能力查询(7 平台 × 6 能力 rating 矩阵)
interpreter.audio_platforms()

# 完整语音对话(LLM 决策 + TTS 输出 wav)
interpreter.audio_chat('今天星期几?')
```

**重要原则**:
1. audio 工具已可用,直接调 `interpreter.audio_xxx(...)`,**不要重做**
2. **不要 pip install** 任何新库
3. **不要 import** `edge_tts` / `deep_translator` / `whisper` / `silero` / `requests` 之类的库
4. 想换平台就在 audio_tts 调 `platform` 参数,不要自己造
5. audio 工具已自动 fallback(BAILIAN→SILICONFLOW→EDGE),失败时它自己处理

# Desktop 操作(2026-07-02 ship,通过 audio 增强器附带)

你还可以触发**系统操作**(voice_agent + OI desktop 增强器):
- 这些工具也在 `interpreter` 全局里(需先 `from audio import install; install(interpreter)` 已 ship 永久 system_message)
- 但为简化,你可以用 `[ACTION:json]` 协议让 LLM 输出 action,voice_agent 会替你执行:
  - click / double_click / right_click
  - type(自动切英文)
  - hotkey / press_key
  - open / screenshot / list_windows
  - memory_recall / translate

如需 OI 默认调 desktop 增强器,请调用 `interpreter.install_desktop_tools()`(待实装)。
"""

# 永久 system_message 补丁文件路径(OI 启动时自动拼接)
AUDIO_SYSTEM_MESSAGE_PATCH_FILE = Path.home() / ".oi" / "audio_tools_system_message.md"


def _write_system_message_patch():
    """把 audio 工具的 system_message 补丁写入永久文件(让 OI 默认都知道)"""
    AUDIO_SYSTEM_MESSAGE_PATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUDIO_SYSTEM_MESSAGE_PATCH_FILE.write_text(AUDIO_TOOLS_SYSTEM_MESSAGE, encoding="utf-8")


def _read_system_message_patch() -> str:
    """读 system_message 补丁(如果存在)"""
    if AUDIO_SYSTEM_MESSAGE_PATCH_FILE.exists():
        try:
            return AUDIO_SYSTEM_MESSAGE_PATCH_FILE.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


def install(interpreter) -> dict:
    """把 audio 增强器注入 OI agent(2026-07-02 升级:永久 system_message)

    两步:
      1. 把 system_message 补丁写到 ~/.oi/audio_tools_system_message.md(永久)
      2. 注入当前 interpreter 实例(运行时)
    """
    # 1) 永久化 system_message 补丁
    _write_system_message_patch()

    # 2) 注入当前 interpreter
    if "Audio Tools (已挂在 interpreter 上,2026-07-02 ship" not in (interpreter.system_message or ""):
        interpreter.system_message = (interpreter.system_message or "") + AUDIO_TOOLS_SYSTEM_MESSAGE

    # 3) 挂 5 个工具到 interpreter 类
    tools = {
        "audio_asr": asr,
        "audio_tts": tts,
        "audio_chat": chat_with_voice,
        "audio_translate": translate,
        "audio_platforms": list_platforms,
    }
    for name, method in tools.items():
        if hasattr(interpreter, "__class__"):
            setattr(type(interpreter), name, lambda self, *a, _m=method, **kw: _m(*a, **kw))

    return {
        "status": "ok",
        "installed_tools": list(tools.keys()),
        "system_message_patch_file": str(AUDIO_SYSTEM_MESSAGE_PATCH_FILE),
        "description": f"audio 增强器 ship 了 {len(tools)} 个工具,system_message 永久补丁已写到 {AUDIO_SYSTEM_MESSAGE_PATCH_FILE}",
    }


def get_permanent_system_message() -> str:
    """外部 OI 启动器可以调这个,把补丁拼接到 OI 默认 system_message"""
    return _read_system_message_patch()


# ============================================================
# install_desktop_tools (2026-07-02 ship)
# 把 desktop / vision / memory 增强器也永久挂到 interpreter + system_message
# ============================================================
DESKTOP_TOOLS_SYSTEM_MESSAGE = """

# Desktop Tools (已挂在 interpreter 上,2026-07-02 ship — install_desktop_tools)

下面这些桌面控制工具**已经挂在 interpreter 上**(由 oi_enhancements/desktop / vision / memory 提供),**直接调用即可**。

调用格式(在 ```python 代码块里):
```python
# 1) Desktop(键盘鼠标)
from oi_enhancements.desktop import click, type_text, hotkey, press_key
click(100, 200)                              # 点 (100, 200)
click(100, 200, double=True)                  # 双击
click(100, 200, button='right')               # 右键
type_text('hello world')                     # 输入文字(自动切英文 IME)
hotkey('ctrl+c')                              # 按键组合
press_key('enter')                            # 单键

# 2) Window 管理
from oi_enhancements.desktop import focus_window, maximize_window, move_window
focus_window('notepad')                      # 激活窗口
maximize_window('Claude')                    # 最大化

# 3) Vision(截图 + 窗口列表)
from oi_enhancements.vision import capture_screen, list_windows
img_b64 = capture_screen()                   # 返回 base64 PNG
windows = list_windows()                     # 返回窗口列表

# 4) Memory(持久记忆)
from oi_enhancements.memory import oi_memory
oi_memory.recall('team-web panel 数')        # 检索记忆
oi_memory.store('今天测试 audio 增强器')     # 存记忆
```

**重要原则**:
1. **不要重做这些工具**(不要 import pyautogui / pynput / Pillow / sqlite3 之类)
2. **不要 pip install** 任何新库
3. desktop / vision / memory 增强器已 ship 在 `C:/Users/Administrator/oi_enhancements/{desktop,vision,memory}/`
4. 如果要 import,先确保 `sys.path` 含 `C:/Users/Administrator/oi_enhancements`
"""


def install_desktop_tools(interpreter) -> dict:
    """把 desktop / vision / memory 增强器永久挂到 OI(2026-07-02 ship)

    三个步骤:
      1. 把 desktop / vision / memory 增强器的 14 个核心方法挂到 interpreter 类
      2. 写 system_message 补丁到 ~/.oi/desktop_tools_system_message.md
      3. 在 interpreter.system_message 拼上 desktop tools 描述
    """
    import importlib.util
    sys_path = "C:/Users/Administrator/oi_enhancements"
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)

    enhancers = {}
    for name in ["desktop", "vision", "memory"]:
        path = Path(f"{sys_path}/{name}/__init__.py")
        if path.exists():
            spec = importlib.util.spec_from_file_location(f"oi_{name}", path)
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
                enhancers[name] = mod
            except Exception as e:
                enhancers[name] = None

    # 把 14 个核心方法挂到 interpreter 类
    cls = type(interpreter)

    mounted = []

    # desktop
    if enhancers.get("desktop"):
        d = enhancers["desktop"]
        cls.dt_click = lambda self, x, y, button="left", double=False: d.click(x, y, button=button, double=double)
        cls.dt_type_text = lambda self, text: d.type_text(text)
        cls.dt_hotkey = lambda self, keys: d.hotkey(keys)
        cls.dt_press_key = lambda self, key: d.press_key(key)
        cls.dt_focus_window = lambda self, title: d.focus_window(title) if hasattr(d, "focus_window") else {"status": "unavailable"}
        cls.dt_maximize_window = lambda self, title: d.maximize_window(title) if hasattr(d, "maximize_window") else {"status": "unavailable"}
        cls.dt_move_window = lambda self, title, x, y: d.move_window(title, x, y) if hasattr(d, "move_window") else {"status": "unavailable"}
        cls.dt_find_window = lambda self, title: d.find_window(title) if hasattr(d, "find_window") else {"status": "unavailable"}
        mounted.extend(["dt_click", "dt_type_text", "dt_hotkey", "dt_press_key",
                        "dt_focus_window", "dt_maximize_window", "dt_move_window", "dt_find_window"])

    # vision
    if enhancers.get("vision"):
        v = enhancers["vision"]
        cls.vi_capture_screen = lambda self: v.capture_screen()
        cls.vi_list_windows = lambda self: v.list_windows()
        cls.vi_capture_window = lambda self, title: v.capture_window(title) if hasattr(v, "capture_window") else {"status": "unavailable"}
        mounted.extend(["vi_capture_screen", "vi_list_windows", "vi_capture_window"])

    # memory(oi_memory 是独立文件,直接 import)
    try:
        # 模块名要匹配 __name__ 让 dataclass 在 sys.modules 找到
        mem_spec = importlib.util.spec_from_file_location(
            "oi_memory", f"{sys_path}/memory/oi_memory.py")
        mem_mod = importlib.util.module_from_spec(mem_spec)
        sys.modules["oi_memory"] = mem_mod  # 注册到 sys.modules 修 dataclass 错
        mem_spec.loader.exec_module(mem_mod)
        oi_mem_instance = mem_mod.OIMemory()
        cls.mm_recall = lambda self, query: oi_mem_instance.recall(query)
        cls.mm_store = lambda self, text: oi_mem_instance.store(text)
        mounted.extend(["mm_recall", "mm_store"])
    except Exception as e:
        log(f"memory 增强器挂载失败: {e}")

    # 永久 system_message 补丁
    DESKTOP_SYSTEM_MESSAGE_PATCH_FILE = Path.home() / ".oi" / "desktop_tools_system_message.md"
    DESKTOP_SYSTEM_MESSAGE_PATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_SYSTEM_MESSAGE_PATCH_FILE.write_text(DESKTOP_TOOLS_SYSTEM_MESSAGE, encoding="utf-8")

    # 拼到 interpreter.system_message
    if "Desktop Tools (已挂在 interpreter 上,2026-07-02 ship" not in (interpreter.system_message or ""):
        interpreter.system_message = (interpreter.system_message or "") + DESKTOP_TOOLS_SYSTEM_MESSAGE

    return {
        "status": "ok",
        "mounted_tools": mounted,
        "tools_count": len(mounted),
        "system_message_patch_file": str(DESKTOP_SYSTEM_MESSAGE_PATCH_FILE),
        "description": f"desktop/vision/memory ship 了 {len(mounted)} 个工具到 interpreter,永久补丁写好",
    }


# ============================================================
# 统一翻译接口
# ============================================================
def translate(text: str, dest: str = "zh-CN", src: str = "auto") -> dict:
    """统一翻译入口(2026-07-02 ship,基于 googletrans)

    platform="auto" → 试 GOOGLE_TRANSLATE(免费,需 pip install googletrans)
    以后可扩展:BING_TRANSLATE / DEEPL_FREE 等

    Returns {"status":"ok"|"error", "src":..., "dest":..., "text":...}
    """
    return _googletrans_sync(text, dest=dest, src=src)
