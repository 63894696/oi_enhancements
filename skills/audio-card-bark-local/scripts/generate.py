# -*- coding: utf-8 -*-
"""audio-card-bark-local — 调 bark 生成 wav。bark 未装时走 stub(3 秒白噪音)。"""
import argparse, os, sys, struct, math, time, wave

# bark 可选:本机没 GPU/没装 bark 时走 stub
try:
    import bark  # noqa: F401
    from bark import generate_audio, SAMPLE_RATE, preload_models  # noqa: F401
    _BARK_OK = True
except ImportError:
    _BARK_OK = False

try:
    import scipy.io.wavfile  # noqa: F401
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False


def _stub_wav(path: str, text: str, seconds: float = 3.0):
    """离线 stub:写一段 22050Hz 单声道白噪音(代替 bark 真生成)。
    仅供 #67 e2e 验证「落盘 → doc-panel 预览」闭环;不是真音频。"""
    sr = 22050
    n = int(sr * seconds)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for i in range(n):
            # 微弱 tone 偏移,别太吵耳朵
            v = int(8000 * math.sin(2 * math.pi * 220 * i / sr)) + int(2000 * (((i * 1103515245 + 12345) >> 8) & 0xFFFF) / 65535 - 1000)
            w.writeframes(struct.pack("<h", max(-32767, min(32767, v))))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("--output", default="")
    ap.add_argument("--voice", default="v2/en_speaker_6")
    args = ap.parse_args()

    if not args.output:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = os.path.join("generated", f"bark_{ts}.wav")
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)

    if _BARK_OK and _SCIPY_OK:
        # 真路径
        print("[info] preload_models (首次会下载 ~10GB)", file=sys.stderr)
        preload_models()
        print(f"[info] generate voice={args.voice}", file=sys.stderr)
        audio = generate_audio(args.text, history_prompt=args.voice)
        scipy.io.wavfile.write(args.output, SAMPLE_RATE, audio)
        print(f"[ok] saved → {args.output}  ({len(audio)} samples @ {SAMPLE_RATE}Hz) 真 bark 输出")
        return 0
    # stub 路径
    n = _stub_wav(args.output, args.text)
    print(f"[ok-stub] saved → {args.output}  ({n} samples @ 22050Hz) "
          f"(bark 未装,白噪音占位;真生成需 pip install git+https://github.com/suno-ai/bark.git)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())