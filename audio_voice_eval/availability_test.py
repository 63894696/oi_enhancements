"""OI audio 增强器 8 场景可用性测试(2026-07-02)

场景:
  1. list_platforms — 平台能力矩阵
  2. asr 真中文 wav — SILICONFLOW + STEPFUN + BAILIAN(待探) 三平台对比
  3. tts 中文 — BAILIAN 4 voice 跑 + SILICONFLOW 备份
  4. tts 错误处理 — 不存在的 voice
  5. chat_with_voice 端到端 — LLM + TTS 闭环
  6. asr 错误处理 — 不存在的 wav
  7. 平台能力查询 — get_best_platform
  8. 完整 OI 集成验证 — interpreter.audio_asr/.tts/.chat/.platforms 全可用

跑法:
  python availability_test.py
"""
import sys, os, json, time
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 1) 装 audio 增强器到 OI(同一进程)
print("=" * 60)
print("STAGE 0: install audio 增强器到 OI")
print("=" * 60)
import importlib.util
_audio_spec = importlib.util.spec_from_file_location("audio_enhancer",
    Path(__file__).resolve().parent.parent / "audio" / "__init__.py")
audio = importlib.util.module_from_spec(_audio_spec)
_audio_spec.loader.exec_module(audio)

from interpreter import interpreter
_ = interpreter.computer
OpenInterpreter = type(interpreter)

def _asr(self, wav_path, platform="auto"): return audio.asr(wav_path, platform=platform)
def _tts(self, text, output_path, voice="sambert-zhichu-v1"): return audio.tts(text, output_path, voice=voice, platform="BAILIAN")
def _chat(self, text): return audio.chat_with_voice(text)
def _translate(self, text, dest="zh-CN", src="auto"): return audio.translate(text, dest=dest, src=src)
def _platforms(self): return audio.list_platforms()

OpenInterpreter.audio_asr = _asr
OpenInterpreter.audio_tts = _tts
OpenInterpreter.audio_chat = _chat
OpenInterpreter.audio_translate = _translate
OpenInterpreter.audio_platforms = _platforms
print("✓ 4 tools mounted: interpreter.audio_asr/.audio_tts/.audio_chat/.audio_platforms\n")

# 收集结果
results = {"timestamp": datetime.now().isoformat(), "scenarios": []}
def record(name, status, detail):
    results["scenarios"].append({"name": name, "status": status, "detail": detail})
    icon = "✓" if status == "pass" else "✗"
    print(f"  {icon} {name}: {status}  ({detail if isinstance(detail, str) else json.dumps(detail)[:200]})")


# ============================================================
# 场景 1: list_platforms
# ============================================================
print("=" * 60)
print("场景 1: list_platforms")
print("=" * 60)
try:
    plats = interpreter.audio_platforms()
    n_plat = len(plats)
    caps_per_plat = [len(p) for p in plats.values()]
    # 6 平台(原 5) + 1 translate platform = 7
    # 每平台 5 能力,GOOGLE_TRANSLATE 多 1 个 translate = 6
    has_translate_cap = any("translate" in p for p in plats.values())
    record("1.list_platforms",
           "pass" if n_plat == 7 and has_translate_cap else "fail",
           f"{n_plat} platforms, caps per plat: {caps_per_plat}, has_translate_cap: {has_translate_cap}")
except Exception as e:
    record("1.list_platforms", "fail", str(e)[:200])


# ============================================================
# 场景 2: asr 真中文 wav
# ============================================================
print("\n" + "=" * 60)
print("场景 2: asr 真中文 wav(关雎原声 10s)× 3 平台")
print("=" * 60)
wav_path = "C:/temp/sf_audio/real_guanjv_10s.wav"
for plat in ["auto", "SILICONFLOW", "STEPFUN"]:
    try:
        r = interpreter.audio_asr(wav_path, platform=plat)
        text = r.get("text", "")[:60]
        ms = r.get("latency_ms", "?")
        status = "pass" if r.get("status") == "ok" and "雎" in text else "partial"
        record(f"2.asr.{plat}", status, f"text='{text}' latency={ms}ms")
    except Exception as e:
        record(f"2.asr.{plat}", "fail", str(e)[:200])


# ============================================================
# 场景 3: tts 中文 × 4 voice
# ============================================================
print("\n" + "=" * 60)
print("场景 3: tts 中文 × 4 voice(BAILIAN sambert)")
print("=" * 60)
out_dir = Path("C:/temp/avail_test")
out_dir.mkdir(parents=True, exist_ok=True)
text = "今天天气真好,适合出去散步"
for voice in ["sambert-zhichu-v1", "sambert-zhixiao-v1", "sambert-zhiting-v1", "sambert-zhimiao-emo-v1"]:
    out = out_dir / f"tts_{voice}.wav"
    try:
        r = interpreter.audio_tts(text, str(out), voice=voice)
        size = out.stat().st_size if out.exists() else 0
        status = "pass" if r.get("status") == "ok" and size > 1000 else "fail"
        record(f"3.tts.{voice}", status, f"size={size}b")
    except Exception as e:
        record(f"3.tts.{voice}", "fail", str(e)[:200])


# ============================================================
# 场景 4: tts 错误处理 — 不存在的 voice
# ============================================================
print("\n" + "=" * 60)
print("场景 4: tts 错误处理")
print("=" * 60)
out = out_dir / "err_voice.wav"
try:
    r = interpreter.audio_tts("test", str(out), voice="non-existent-voice")
    status = r.get("status", "fail")
    record("4.tts_error.invalid_voice", "pass" if status != "ok" else "fail",
           f"status={status} (期望 fail/unavailable)")
except Exception as e:
    record("4.tts_error.invalid_voice", "pass", f"raised (good): {str(e)[:80]}")


# ============================================================
# 场景 5: chat_with_voice 端到端
# ============================================================
print("\n" + "=" * 60)
print("场景 5: chat_with_voice 端到端(STEPFUN LLM + BAILIAN TTS)")
print("=" * 60)
try:
    r = interpreter.audio_chat("今天星期几?")
    reply = r.get("llm_reply", "")[:80]
    out_wav = r.get("output_wav", "")
    tts_status = r.get("tts", {}).get("status", "?")
    tts_size = Path(out_wav).stat().st_size if Path(out_wav).exists() else 0
    status = "pass" if r.get("status") in ("ok", "partial") and tts_size > 1000 else "fail"
    record("5.chat_with_voice", status,
           f"reply='{reply}' tts={tts_status} size={tts_size}b")
except Exception as e:
    record("5.chat_with_voice", "fail", str(e)[:200])


# ============================================================
# 场景 6: asr 错误处理 — 不存在的 wav
# ============================================================
print("\n" + "=" * 60)
print("场景 6: asr 错误处理")
print("=" * 60)
try:
    r = interpreter.audio_asr("C:/temp/does_not_exist.wav", platform="auto")
    status = r.get("status", "fail")
    record("6.asr_error.missing_file", "pass" if status != "ok" else "fail",
           f"status={status} (期望 error/fail/unavailable)")
except Exception as e:
    record("6.asr_error.missing_file", "pass", f"raised (good): {str(e)[:80]}")


# ============================================================
# 场景 7: get_best_platform 能力查询
# ============================================================
print("\n" + "=" * 60)
print("场景 7: get_best_platform")
print("=" * 60)
for cap in ["asr", "tts", "chat_with_audio", "voice_agent"]:
    try:
        p = audio.get_best_platform(cap)
        record(f"7.get_best.{cap}", "pass" if p in audio.PLATFORM_MATRIX else "fail", f"→ {p}")
    except Exception as e:
        record(f"7.get_best.{cap}", "fail", str(e)[:200])


# ============================================================
# 场景 8: OI 集成验证 — 4 个方法都挂在 interpreter 上
# ============================================================
print("\n" + "=" * 60)
print("场景 8: OI 集成验证")
print("=" * 60)
for method in ["audio_asr", "audio_tts", "audio_chat", "audio_platforms", "audio_translate"]:
    has = hasattr(interpreter, method)
    callable_ok = callable(getattr(interpreter, method, None))
    record(f"8.oi_integration.{method}", "pass" if has and callable_ok else "fail",
           f"has={has} callable={callable_ok}")


# ============================================================
# 场景 9: edge-tts 免费 fallback(4 voice)
# ============================================================
print("\n" + "=" * 60)
print("场景 9: edge-tts 免费 TTS fallback(4 voice)")
print("=" * 60)
out_dir = Path("C:/temp/avail_test")
for voice in ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-HK-WanLungNeural"]:
    out = out_dir / f"edge_{voice}.wav"
    try:
        r = interpreter.audio_tts("免费 edge-tts 测试", str(out), voice=voice)
        # 强制走 EDGE 平台
        r = audio.tts("免费 edge-tts 测试", str(out), voice=voice, platform="EDGE")
        size = out.stat().st_size if out.exists() else 0
        status = "pass" if r.get("status") == "ok" and size > 1000 else "fail"
        record(f"9.edge_tts.{voice}", status, f"size={size}b")
    except Exception as e:
        record(f"9.edge_tts.{voice}", "fail", str(e)[:200])


# ============================================================
# 场景 10: googletrans 翻译
# ============================================================
print("\n" + "=" * 60)
print("场景 10: googletrans 免费翻译(3 方向)")
print("=" * 60)
for text, dest in [("Hello, how are you today?", "zh-CN"),
                    ("今天的天气真好,适合出去散步。", "en"),
                    ("おはようございます", "zh-CN")]:
    try:
        r = interpreter.audio_translate(text, dest=dest)
        out_text = r.get("text", "")[:60]
        status = "pass" if r.get("status") == "ok" and out_text else "fail"
        record(f"10.translate.{dest}", status, f"'{text[:30]}' → '{out_text}'")
    except Exception as e:
        record(f"10.translate.{dest}", "fail", str(e)[:200])


# ============================================================
# 场景 11: 平台能力矩阵(7 平台)
# ============================================================
print("\n" + "=" * 60)
print("场景 11: 平台能力矩阵(7 平台)")
print("=" * 60)
try:
    plats = interpreter.audio_platforms()
    n_plat = len(plats)
    has_edge = "EDGE_TTS" in plats
    has_translate = "GOOGLE_TRANSLATE" in plats
    record("11.platforms.7_total", "pass" if n_plat == 7 else "fail", f"7 平台,实际 {n_plat}")
    record("11.platforms.edge_in", "pass" if has_edge else "fail", f"EDGE_TTS in matrix: {has_edge}")
    record("11.platforms.translate_in", "pass" if has_translate else "fail", f"GOOGLE_TRANSLATE in matrix: {has_translate}")
except Exception as e:
    record("11.platforms.7_total", "fail", str(e)[:200])


# ============================================================
# 输出 JSON + 总结
# ============================================================
print("\n" + "=" * 60)
print("总结")
print("=" * 60)
n_pass = sum(1 for s in results["scenarios"] if s["status"] == "pass")
n_fail = sum(1 for s in results["scenarios"] if s["status"] == "fail")
n_partial = sum(1 for s in results["scenarios"] if s["status"] == "partial")
print(f"  pass={n_pass}  partial={n_partial}  fail={n_fail}  total={len(results['scenarios'])}")

out_json = Path.home() / ".oi" / "benchmarks" / f"oi_audio_availability_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
out_json.parent.mkdir(parents=True, exist_ok=True)
out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n→ JSON: {out_json}")
