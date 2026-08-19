"""Prisir Browser 内置 ASR — P1 轨 A 离线预生成字幕原型

蓝图依据:prisir-browser-builtin-asr-video-subtitle-v01-2026-08-19 §3.2
  轨 A 主线:WASAPI loopback 听系统音频 → SenseVoice → 带时间轴 SRT 存下载目录。
  DRM 管视频流提取,管不到用户听到的声音;零破解、合法、通用(有声即可)。

架构(P1 原型,Python 编排 + Rust 引擎):
  soundcard loopback(48k stereo)→ 混单声道 + 重采样 16k
    → silero-vad 切句(带起止时间)
    → prisir_asr.dll transcribe_pcm(Rust 引擎,ctypes)
    → 按加速比回标时间轴 → SRT + sidecar 元数据

P5 终态会把 loopback/VAD 也下沉 Rust;P1 先用 Python 复用现成 VAD 快速验证正确性。

用法:
  python -m prisir_asr.subtitle_gen --dur 65 --speed 2.0 \
      --title 关雎 --url local://guanju
  # 另一进程同时播放音频(经声卡),本程序静默听写
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
import warnings
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

# loopback 丢帧是常态(采集跟不上播放),时间轴按"实际读到的样本数"累加,
# 不让 discontinuity 刷屏告警。
warnings.filterwarnings("ignore", message="data discontinuity")

# ---- 路径 ----
_HERE = Path(__file__).resolve().parent          # prisir_asr/
ORT_DLL = _HERE / "ort_bin" / "onnxruntime.dll"
ASR_DLL = _HERE / "target" / "release" / "prisir_asr.dll"
MODEL_DIR = r"C:\Users\Administrator\AppData\Roaming\Shandianshuo\models\sensevoice-small"
OUT_DIR = Path.home() / "Downloads" / "PrisirSubtitles"

SAMPLE_RATE = 16000        # ASR 采样率
LOOPBACK_RATE = 48000      # 声卡 loopback 原生采样率(Windows 默认)
VAD_CHUNK = 512            # silero @16k 每块 32ms

# CTC 帧与真实时间的关系:LFR_N=6 帧移 10ms → 一个 CTC 输出步 ≈ 60ms(见 config lfr_n=6)
# 但 VAD 已给起止样本时间,直接用 VAD 时间,不依赖 CTC 帧回推。


# ============================================================
# Rust ASR 引擎封装(ctypes → prisir_asr.dll)
# ============================================================
class RustASR:
    """调 P0 编译的 prisir_asr.dll(transcribe_pcm)"""

    def __init__(self, model_dir: str = MODEL_DIR):
        import os
        os.environ.setdefault("ORT_DYLIB_PATH", str(ORT_DLL))  # load-dynamic 自动找
        # 先预载 ORT 1.29(避免 cdylib 内部走默认路径撞到 1.26)
        ctypes.CDLL(str(ORT_DLL))
        self._lib = ctypes.CDLL(str(ASR_DLL))
        self._lib.prisir_asr_load.restype = ctypes.c_void_p
        self._lib.prisir_asr_load.argtypes = [ctypes.c_char_p]
        self._lib.prisir_asr_transcribe_pcm.restype = ctypes.c_void_p  # 手动管理内存
        self._lib.prisir_asr_transcribe_pcm.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_size_t,
            ctypes.c_int, ctypes.c_int,
        ]
        self._lib.prisir_asr_free_string.argtypes = [ctypes.c_void_p]
        self._lib.prisir_asr_free.argtypes = [ctypes.c_void_p]
        self._h = self._lib.prisir_asr_load(model_dir.encode("utf-8"))
        if not self._h:
            raise RuntimeError("prisir_asr_load 失败(检查 model.onnx/tokens.json/ort dll)")

    def transcribe(self, pcm_f32: np.ndarray, language: int = 1, textnorm: int = 14) -> str:
        """pcm_f32: 16kHz mono float32 [-1,1]"""
        buf = np.ascontiguousarray(pcm_f32, dtype=np.float32)
        ptr = buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        res_ptr = self._lib.prisir_asr_transcribe_pcm(
            self._h, ptr, len(buf), language, textnorm)
        if not res_ptr:
            return ""
        try:
            return ctypes.cast(res_ptr, ctypes.c_char_p).value.decode("utf-8")
        finally:
            self._lib.prisir_asr_free_string(res_ptr)

    def close(self):
        if self._h:
            self._lib.prisir_asr_free(self._h)
            self._h = None

    def __del__(self):
        self.close()


# ============================================================
# VAD 切句(复用 voice_input 的 silero 包装)
# ============================================================
def _make_vad():
    """加载 silero-vad,返回 VADIterator(独立实例)"""
    from silero_vad import load_silero_vad, VADIterator
    model = load_silero_vad(onnx=False)
    return VADIterator(model, threshold=0.4, sampling_rate=SAMPLE_RATE,
                       min_silence_duration_ms=500)


# ============================================================
# 采集 + 切句 + 识别
# ============================================================
@dataclass
class Cue:
    index: int
    start: float   # 秒,已按加速比回标到原始时间轴
    end: float
    text: str


def _to_mono_16k(block: np.ndarray, src_rate: int) -> np.ndarray:
    """loopback 块 → mono 16k float32。block: (n, ch)"""
    if block.ndim == 2:
        block = block.mean(axis=1)
    block = block.astype(np.float32)
    if src_rate != SAMPLE_RATE:
        # 用整数比例重采样(48k→16k 即 /3)
        from math import gcd
        g = gcd(src_rate, SAMPLE_RATE)
        block = resample_poly(block, SAMPLE_RATE // g, src_rate // g)
    return block


def capture_and_transcribe(
    duration: float,
    speed: float = 1.0,
    language: int = 1,
    textnorm: int = 14,
    speaker_substr: str | None = None,
    progress: bool = True,
) -> list[Cue]:
    """听系统音频 duration 秒,VAD 切句逐段识别,返回按原始时间轴回标的 cues。

    speed: 播放加速比(加速静音生成)。识别块时间 = VAD 样本时间 × speed?——
      不对。加速播放时,声卡听到的是压缩后的音频;VAD 给出的时间是"压缩后时间轴",
      真实(原始)时间轴 = VAD 时间 × speed。故 start/end 乘 speed 回标。
    """
    import soundcard as sc

    # 选 loopback 扬声器
    speaker = None
    for s in sc.all_speakers():
        if speaker_substr is None or speaker_substr in s.name:
            speaker = s
            break
    if speaker is None:
        raise RuntimeError("未找到扬声器(loopback)")
    print(f"[capture] loopback 扬声器: {speaker.name}", file=sys.stderr)

    asr = RustASR()
    vad = _make_vad()
    print("[capture] 引擎就绪(模型+VAD 已加载),开始采集", file=sys.stderr)

    cues: list[Cue] = []
    seg_buf: list[np.ndarray] = []   # 当前语音段的 16k 样本
    seg_start_sample: int | None = None
    total_samples = 0                # 已处理的 16k 样本数(实际读到的,压缩后时间轴)
    target_samples = int(duration * SAMPLE_RATE)

    with sc.get_microphone(speaker.name, include_loopback=True).recorder(
        samplerate=LOOPBACK_RATE, channels=2
    ) as rec:
        blk = int(LOOPBACK_RATE * 0.5)  # 0.5s 一抓
        while total_samples < target_samples:
            data = rec.record(numframes=blk)         # (n, 2) float32 @48k
            if data.size == 0:
                continue
            chunk16k = _to_mono_16k(data, LOOPBACK_RATE)

            # 按 512 样本喂 VAD
            pos = 0
            while pos + VAD_CHUNK <= len(chunk16k):
                sub = chunk16k[pos:pos + VAD_CHUNK]
                ev = vad(sub, return_seconds=False)
                if ev:
                    if "start" in ev:
                        # VADIterator 的 start 是绝对样本数,含 speech_pad 回退(30ms),
                        # 指到当前 chunk 之前。钳制在 [当前块起点, 当前块内]。
                        abs_start = ev["start"]
                        seg_start_sample = max(total_samples + pos,
                                               min(abs_start, total_samples + pos + VAD_CHUNK))
                        seg_buf = []
                    elif "end" in ev and seg_start_sample is not None:
                        # 同理,end = temp_end + pad - window,也是绝对且需钳制
                        abs_end = ev["end"]
                        seg_end_sample = max(total_samples + pos,
                                             min(abs_end, total_samples + pos + VAD_CHUNK))
                        seg = np.concatenate(seg_buf) if seg_buf else np.array([], np.float32)
                        _flush_segment(seg, seg_start_sample, seg_end_sample,
                                       speed, asr, cues, language, textnorm)
                        seg_start_sample = None
                        seg_buf = []
                if seg_start_sample is not None:
                    seg_buf.append(sub)
                pos += VAD_CHUNK
            total_samples += len(chunk16k)   # 用实际读到的样本数累加,消除丢帧漂移
            if progress:
                done = total_samples / SAMPLE_RATE
                print(f"\r[capture] {done:.1f}/{duration:.0f}s  cues={len(cues)}",
                      end="", file=sys.stderr)

    # 收尾:还有未闭合的段
    if seg_start_sample is not None and seg_buf:
        seg = np.concatenate(seg_buf)
        _flush_segment(seg, seg_start_sample, total_samples, speed, asr, cues,
                       language, textnorm)
    print(file=sys.stderr)
    asr.close()
    return cues


def _flush_segment(seg: np.ndarray, start_s: int, end_s: int, speed: float,
                   asr: RustASR, cues: list[Cue], language: int, textnorm: int):
    """识别一段并追加 cue(时间乘 speed 回标原始时间轴)"""
    if len(seg) < SAMPLE_RATE * 0.25:   # 丢弃 <250ms 的过短段
        return
    # 能量门控:静音/底噪段不过 ASR(消除 "Yeah."/"没." 这类幻听)
    if float(np.sqrt(np.mean(seg ** 2))) < 0.01:
        return
    text = asr.transcribe(seg, language=language, textnorm=textnorm).strip()
    if not text:
        return
    # 丢弃纯英文幻听碎片(中文视频里 VAD 误触发常出 "Yeah."/"OK." 之类)
    if not any('一' <= ch <= '鿿' for ch in text) and len(text) <= 12:
        return
    start = start_s / SAMPLE_RATE * speed
    end = end_s / SAMPLE_RATE * speed
    cues.append(Cue(len(cues) + 1, start, end, text))
    print(f"  [cue {len(cues)}] {start:6.2f}-{end:6.2f}  {text}", file=sys.stderr)


# ============================================================
# SRT + 元数据输出
# ============================================================
def _fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[Cue], path: Path):
    lines = []
    for c in cues:
        lines.append(str(c.index))
        lines.append(f"{_fmt_ts(c.start)} --> {_fmt_ts(c.end)}")
        lines.append(c.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_meta(path: Path, *, title, url, duration, speed, language, textnorm, n_cues):
    meta = {
        "title": title,
        "url": url,
        "duration_sec": duration,
        "speed": speed,
        "language": language,
        "textnorm": textnorm,
        "n_cues": n_cues,
        "engine": "prisir_asr (SenseVoice Small, local)",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="P1 轨 A 离线预生成字幕(WASAPI loopback)")
    ap.add_argument("--dur", type=float, required=True, help="听写时长(秒,压缩后时间轴)")
    ap.add_argument("--speed", type=float, default=1.0, help="播放加速比(时间轴回标)")
    ap.add_argument("--title", default="untitled")
    ap.add_argument("--url", default="")
    ap.add_argument("--language", type=int, default=1)
    ap.add_argument("--textnorm", type=int, default=14)
    ap.add_argument("--speaker", default=None, help="扬声器名子串(默认第一个)")
    ap.add_argument("--out", default=None, help="输出目录(默认 ~/Downloads/PrisirSubtitles)")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "".join(c if c.isalnum() or c in "._-一-鿿" else "_" for c in args.title)[:60]
    srt_path = out_dir / f"{stem}.srt"
    meta_path = out_dir / f"{stem}.json"

    t0 = time.time()
    cues = capture_and_transcribe(
        duration=args.dur, speed=args.speed, language=args.language,
        textnorm=args.textnorm, speaker_substr=args.speaker)
    dt = time.time() - t0

    write_srt(cues, srt_path)
    write_meta(meta_path, title=args.title, url=args.url, duration=args.dur,
               speed=args.speed, language=args.language, textnorm=args.textnorm,
               n_cues=len(cues))
    print(f"\n✓ {len(cues)} 条字幕,用时 {dt:.1f}s", file=sys.stderr)
    print(f"✓ SRT:  {srt_path}", file=sys.stderr)
    print(f"✓ meta: {meta_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
