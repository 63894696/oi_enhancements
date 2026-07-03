"""OI 0.4.3 chat 模式实测 LLM 真用 audio 工具(auto_run=True)

关键改动:
  1. system_message 明确"工具已挂 interpreter,不要重做 pip install"
  2. auto_run=True → LLM 生成 code 立即执行
  3. 5 个真测试,看 wav/mp3 文件是不是真生成

跑法:
  python test_oi_chat_auto.py
"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 1) 装 audio 增强器到 OI
import importlib.util
_audio_spec = importlib.util.spec_from_file_location("audio_e",
    Path(__file__).resolve().parent.parent / "audio" / "__init__.py")
audio = importlib.util.module_from_spec(_audio_spec)
_audio_spec.loader.exec_module(audio)

from interpreter import interpreter
_ = interpreter.computer
OpenInterpreter = type(interpreter)

def _asr(self, wav, platform="auto"): return audio.asr(wav, platform=platform)
def _tts(self, text, path, voice="sambert-zhichu-v1"): return audio.tts(text, path, voice=voice, platform="BAILIAN")
def _chat(self, text): return audio.chat_with_voice(text)
def _translate(self, text, dest="zh-CN", src="auto"): return audio.translate(text, dest=dest, src=src)
def _platforms(self): return audio.list_platforms()

OpenInterpreter.audio_asr = _asr
OpenInterpreter.audio_tts = _tts
OpenInterpreter.audio_chat = _chat
OpenInterpreter.audio_translate = _translate
OpenInterpreter.audio_platforms = _platforms
print("✓ audio 增强器装上 OI:5 工具\n")

# 2) LLM 配置
import subprocess, re
key_out = subprocess.run(['reg','query','HKCU\\Environment','/v','STEPFUN_API_KEY'], capture_output=True, text=True).stdout
m = re.search(r'REG_SZ\s+(\S+)', key_out)
STEPFUN_KEY = m.group(1) if m else None

interpreter.llm.model = "openai/step-3.7-flash"
interpreter.llm.api_base = "https://api.stepfun.com/v1"
interpreter.llm.api_key = STEPFUN_KEY
interpreter.auto_run = True          # 关键!让 LLM 生成的 code 立即执行
interpreter.verbose = False
interpreter.safe_mode = "off"

# 关键 system_message:明确"工具已装,不要重做"
interpreter.system_message = (interpreter.system_message or "") + """

# Audio Tools(已挂在 interpreter 上,直接调用,不要重做)

下面 5 个 audio 工具已经挂在 Python 全局 `interpreter` 上,**直接调用即可,不要 pip install、不要 import edge_tts / deep_translator / google.cloud 之类的库**。

调用格式(在 ```python 代码块里):
```python
# ASR:音频转文字
interpreter.audio_asr('C:/path/to.wav')                          # platform='auto'
interpreter.audio_asr('C:/path/to.wav', platform='SILICONFLOW')  # 锁平台

# TTS:文字转语音(默认 BAILIAN sambert,失败 fallback edge-tts)
interpreter.audio_tts('你好', 'C:/out.wav')                      # 默认 voice
interpreter.audio_tts('你好', 'C:/out.wav', voice='sambert-zhixiao-v1')  # 男声
# 想用免费 edge-tts:用 audio.tts(..., platform='EDGE', voice='zh-CN-XiaoxiaoNeural')
import sys
sys.path.insert(0, r'C:/Users/Administrator/oi_enhancements')
from audio import tts as audio_tts
audio_tts('你好', 'C:/out.mp3', voice='zh-CN-XiaoxiaoNeural', platform='EDGE')

# 翻译:Google 免费,100+ 语言
interpreter.audio_translate('Hello, how are you?', dest='zh-CN')
interpreter.audio_translate('今天的天气真好', dest='en')

# 平台能力查询
interpreter.audio_platforms()  # 返回 7 平台 × 6 能力 rating 矩阵

# 完整语音对话(LLM 决策 + TTS 输出)
interpreter.audio_chat('今天星期几?')
```

重要原则:
1. **audio 工具已可用,直接调 interpreter.audio_xxx(...)**
2. **不要 pip install** 任何新库
3. **不要 import edge_tts / deep_translator / whisper / silero** 之类的库
4. 想换平台就在 audio_tts 调 platform 参数,不要自己造
5. audio 工具已自动 fallback(BAILIAN→SILICONFLOW→EDGE),失败时它自己处理
"""
print("✓ LLM 配置:STEPFUN step-3.7-flash (auto_run=True, system_message 已强化)")

# 3) 跑 5 个真测试
test_cases = [
    {
        "name": "T1.ASR(用 SILICONFLOW 转录音频)",
        "msg": "请把 C:/temp/sf_audio/real_guanjv_10s.wav 这个音频文件转成文字,只输出识别结果。",
        "expect_artifacts": [],  # 不期待新文件
    },
    {
        "name": "T2.TTS(中文女声合成 wav)",
        "msg": "请用中文女声把'今天天气真好,适合出去散步'合成 wav,保存到 C:/temp/oi_chat_tts.wav。",
        "expect_artifacts": ["C:/temp/oi_chat_tts.wav"],
    },
    {
        "name": "T3.Translate(中英翻译)",
        "msg": "把 'Hello, how are you today?' 翻译成中文,只输出翻译结果。",
        "expect_artifacts": [],
    },
    {
        "name": "T4.切平台(用 SILICONFLOW 翻译)",
        "msg": "把 'Good morning, world!' 翻译成日文。",
        "expect_artifacts": [],
    },
    {
        "name": "T5.切免费平台(edge-tts 合成 mp3)",
        "msg": "用微软免费的 edge-tts 把'今天天气真好'合成 mp3,保存到 C:/temp/oi_chat_edge.mp3。",
        "expect_artifacts": ["C:/temp/oi_chat_edge.mp3"],
    },
]

results = []
for tc in test_cases:
    print()
    print("=" * 60)
    print(tc["name"])
    print("=" * 60)
    print(f"  USER: {tc['msg']}")
    print()
    interpreter.messages = []  # 重置

    # 跑 chat(auto_run=True,LLM 生成 code 自动执行)
    t0 = time.time()
    try:
        interpreter.chat(tc["msg"], display=False)
        dt = time.time() - t0
        # 收集输出
        last = interpreter.messages[-1] if interpreter.messages else {}
        content = last.get("content", "")
        if isinstance(content, list):
            content = " ".join([str(c) for c in content])
        # 检查 code 执行结果(从 messages 里抓 tool_result)
        tool_results = [m for m in interpreter.messages if m.get("role") == "function" or m.get("type") == "function"]
        # 检查期待文件
        files_ok = []
        for f in tc["expect_artifacts"]:
            if Path(f).exists():
                files_ok.append(f"{f} ({Path(f).stat().st_size}b)")
            else:
                files_ok.append(f"{f} (MISSING)")
        # LLM 调对工具的判定
        all_msgs_str = json.dumps(interpreter.messages, ensure_ascii=False, default=str)
        called_audio = "audio_asr" in all_msgs_str or "audio_tts" in all_msgs_str or "audio_translate" in all_msgs_str
        all_files_exist = all(Path(f).exists() for f in tc["expect_artifacts"]) if tc["expect_artifacts"] else True
        status = "pass" if (called_audio and all_files_exist) else "partial" if called_audio else "fail"
        reason = f"called_audio={called_audio} files={files_ok} dt={dt:.1f}s content='{content[:200]}'"
    except Exception as e:
        status = "fail"
        reason = f"chat() EXC: {e}"
    results.append({**tc, "status": status, "reason": reason})
    print(f"  → {status.upper()}: {reason[:200]}")

# 4) 总结
n_pass = sum(1 for r in results if r["status"] == "pass")
n_partial = sum(1 for r in results if r["status"] == "partial")
print()
print("=" * 60)
print(f"总结:{n_pass} pass / {n_partial} partial / {len(results)} total")
print("=" * 60)
for r in results:
    icon = "✓" if r["status"] == "pass" else ("~" if r["status"] == "partial" else "✗")
    print(f"  {icon} {r['name']}: {r['reason'][:120]}")

# JSON 落盘
out_json = Path.home() / ".oi" / "benchmarks" / f"oi_chat_auto_{int(time.time())}.json"
out_json.write_text(json.dumps([{"name": r["name"], "status": r["status"], "reason": r["reason"]} for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n→ JSON: {out_json}")
