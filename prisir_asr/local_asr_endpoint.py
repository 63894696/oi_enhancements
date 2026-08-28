"""Prisir 本地 ASR 引擎端点(127.0.0.1:12308)— engines.js 总线的 local_sensevoice 后端

承接 custom-hover-translate/.../engines.js 的 callLocalAsr:
  POST /asr { audio: base64(wav), format, language, textnorm, want_cues }
    → { ok, text, cues: [{start,end,text}] }
  GET  /health → { ok: true }

引擎:P0 编译的 prisir_asr.dll(Rust + ONNX,SenseVoice Small,纯本地零外发)。
切句:silero-vad(want_cues 时给时间轴)。
隐私红线:本端点只绑 127.0.0.1,纯本地识别,音频不出本机。

P5 终态:此 Python 守护被浏览器本体内置 ASR(Rust FFI)取代;P2 先让它跑通总线闭环。

启动:python local_asr_endpoint.py
"""
from __future__ import annotations

import base64
import io
import sys
import wave
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))  # 复用 subtitle_gen 的 RustASR / VAD

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

from subtitle_gen import RustASR, _make_vad, SAMPLE_RATE, VAD_CHUNK, MODEL_DIR

HOST, PORT = "127.0.0.1", 12308

app = FastAPI(title="Prisir Local ASR")
_asr: RustASR | None = None


def get_asr() -> RustASR:
    global _asr
    if _asr is None:
        _asr = RustASR(MODEL_DIR)
    return _asr


def _decode_wav(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    with wave.open(io.BytesIO(raw), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        data = wf.readframes(n)
    if sw == 2:
        a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        a = np.frombuffer(data, dtype=np.float32)
    if ch == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    if sr != SAMPLE_RATE:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(sr, SAMPLE_RATE)
        a = resample_poly(a, SAMPLE_RATE // g, sr // g)
    return a.astype(np.float32)


def _segment(pcm: np.ndarray, language: int, textnorm: int):
    """VAD 切句 + 逐段识别 → (整段文本, cues)"""
    asr = get_asr()
    vad = _make_vad()
    cues = []
    seg, start = [], None
    pos = 0
    while pos + VAD_CHUNK <= len(pcm):
        sub = pcm[pos:pos + VAD_CHUNK]
        ev = vad(sub, return_seconds=False)
        if ev:
            if "start" in ev:
                # 钳制 speech_pad 回退(P1 时间轴漂移根因)
                start = max(pos, min(ev["start"], pos + VAD_CHUNK))
                seg = []
            elif "end" in ev and start is not None:
                end = max(pos, min(ev["end"], pos + VAD_CHUNK))
                s = np.concatenate(seg) if seg else np.array([], np.float32)
                _emit(s, start, end, asr, cues, language, textnorm)
                start = None
        if start is not None:
            seg.append(sub)
        pos += VAD_CHUNK
    if start is not None and seg:
        s = np.concatenate(seg)
        _emit(s, start, pos, asr, cues, language, textnorm)
    text = " ".join(c["text"] for c in cues).strip()
    return text, cues


def _emit(seg, start_s, end_s, asr, cues, language, textnorm):
    if len(seg) < SAMPLE_RATE * 0.25:
        return
    if float(np.sqrt(np.mean(seg ** 2))) < 0.01:
        return
    t = asr.transcribe(seg, language=language, textnorm=textnorm).strip()
    if not t:
        return
    cues.append({"start": round(start_s / SAMPLE_RATE, 3),
                 "end": round(end_s / SAMPLE_RATE, 3), "text": t})


class AsrReq(BaseModel):
    audio: str
    format: str = "wav"
    language: int = 1
    textnorm: int = 14
    want_cues: bool = True


@app.get("/health")
def health():
    return {"ok": True, "engine": "prisir_asr(local SenseVoice)", "model": MODEL_DIR}


@app.post("/asr")
def asr(req: AsrReq):
    try:
        pcm = _decode_wav(req.audio)
    except Exception as e:
        return {"ok": False, "error": f"decode: {e}", "text": ""}
    engine = get_asr()
    if req.want_cues:
        text, cues = _segment(pcm, req.language, req.textnorm)
        return {"ok": True, "text": text, "cues": cues}
    text = engine.transcribe(pcm, language=req.language, textnorm=req.textnorm)
    return {"ok": True, "text": text, "cues": None}


if __name__ == "__main__":
    print(f"[Prisir ASR] 本地引擎端点 http://{HOST}:{PORT}  模型={MODEL_DIR}")
    print("[Prisir ASR] 纯本地零外发。POST /asr, GET /health")
    get_asr()  # 预热加载模型
    print("[Prisir ASR] 模型已加载,就绪")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
