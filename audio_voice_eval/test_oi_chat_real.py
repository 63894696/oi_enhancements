"""实测 OI 0.4.3 装上 audio 增强器后,LLM 能不能真在 code block 里调

跑 3 个真测试:
  T1: 用户说"把 C:/temp/sf_audio/real_guanjv_10s.wav 转成文字",看 LLM 会不会调 interpreter.audio_asr
  T2: 用户说"用中文女声把'今天天气真好'合成 wav 到 C:/temp/llm_tts.wav",看 LLM 会不会调 interpreter.audio_tts
  T3: 用户说"把 Hello 翻译成中文",看 LLM 会不会调 interpreter.audio_translate
  T4: 用户说"用 SILICONFLOW 平台翻译成日文 Hello",看 LLM 会不会按指令切平台
  T5: 用户说"用 edge-tts 把'你好'合成 mp3",看 LLM 会不会按指令切免费平台

LLM 用 STEPFUN step-3.7-flash(已知可跑,免费额度多)
auto_run = False → 我们手动看 LLM 生成的 code,确认它"想"调 audio 工具
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

# 2) 配置 LLM
import subprocess, re
key_out = subprocess.run(['reg','query','HKCU\\Environment','/v','STEPFUN_API_KEY'], capture_output=True, text=True).stdout
m = re.search(r'REG_SZ\s+(\S+)', key_out)
STEPFUN_KEY = m.group(1) if m else None
STEPFUN_BASE = "https://api.stepfun.com/v1"

interpreter.llm.model = "openai/step-3.7-flash"
interpreter.llm.api_base = STEPFUN_BASE
interpreter.llm.api_key = STEPFUN_KEY
interpreter.auto_run = False          # 关键!让 LLM 生成 code 但不执行
interpreter.verbose = False
interpreter.safe_mode = "off"         # 关 safe mode(避免 ask user)
interpreter.system_message = (interpreter.system_message or "") + """

# Audio Tools (额外说明,告诉 LLM 怎么用)

你可以用以下 audio 工具(已挂在 `interpreter` 全局变量上):

  interpreter.audio_asr(wav_path, platform='auto')
    - wav_path: 音频文件路径
    - platform: 'auto' | 'SILICONFLOW' | 'STEPFUN' | 'BAILIAN'
    - 例子: interpreter.audio_asr('C:/temp/sample.wav', platform='SILICONFLOW')

  interpreter.audio_tts(text, output_path, voice='sambert-zhichu-v1')
    - 默认走 BAILIAN 平台
    - 想用免费 edge-tts:interpreter.audio_tts(text, path, voice='zh-CN-XiaoxiaoNeural') 然后 audio.tts() 内部会 fallback
    - 或者直接 audio.tts(text, path, voice='zh-CN-XiaoxiaoNeural', platform='EDGE')

  interpreter.audio_translate(text, dest='zh-CN', src='auto')
    - 走 Google 免费翻译,100+ 语言

  interpreter.audio_platforms()
    - 返回 7 平台 × 5 能力 rating 矩阵

  interpreter.audio_chat(text)
    - LLM 决策 + TTS 输出 wav(单轮)

调用模式:在 ```python 代码块里直接 `interpreter.audio_xxx(...)`。
"""
print("✓ LLM 配置:STEPFUN step-3.7-flash (auto_run=False 看 LLM 生成的 code)")
print()

# 3) 跑 5 个测试
test_cases = [
    {
        "name": "T1.ASR(用户要求转录音频)",
        "msg": "请把 C:/temp/sf_audio/real_guanjv_10s.wav 这个音频文件转成文字。用 SILICONFLOW 平台。",
        "expect_tools": ["audio_asr"],
        "expect_args": ["SILICONFLOW"],
    },
    {
        "name": "T2.TTS(用户要求中文女声合成)",
        "msg": "请用中文女声把'今天天气真好,适合出去散步'合成 wav,保存到 C:/temp/llm_tts_test.wav。",
        "expect_tools": ["audio_tts"],
        "expect_args": ["wav"],
    },
    {
        "name": "T3.Translate(用户要求中英翻译)",
        "msg": "把 'Hello, how are you today?' 翻译成中文。",
        "expect_tools": ["audio_translate"],
        "expect_args": ["zh-CN"],
    },
    {
        "name": "T4.切平台(用户要求 SILICONFLOW 翻译)",
        "msg": "用 SILICONFLOW 平台把 'Good morning' 翻译成日文。",
        "expect_tools": ["audio_translate"],
        "expect_args": ["ja"],
    },
    {
        "name": "T5.切免费平台(用户要求 edge-tts)",
        "msg": "用微软免费的 edge-tts 把'你好'合成 mp3,保存到 C:/temp/llm_edge_test.mp3。",
        "expect_tools": ["audio_tts"],
        "expect_args": ["EDGE", "edge"],
    },
]

results = []
for tc in test_cases:
    print("=" * 60)
    print(tc["name"])
    print("=" * 60)
    print(f"  USER: {tc['msg']}")
    print()
    # 收集 LLM 生成的 messages
    interpreter.messages = []  # 重置对话历史
    try:
        # 用 chat 模式(不 auto_run)
        interpreter.chat(tc["msg"], display=False)
    except Exception as e:
        print(f"  chat() EXC: {e}")
        results.append({**tc, "status": "fail", "reason": str(e)[:200]})
        continue

    # 检查 LLM 生成的最后一条 assistant message 是否包含 audio 工具调用
    last_assistant = None
    for m in reversed(interpreter.messages):
        if m.get("role") == "assistant":
            last_assistant = m
            break
    if not last_assistant:
        results.append({**tc, "status": "fail", "reason": "no assistant msg"})
        continue
    content = last_assistant.get("content", "")
    if isinstance(content, list):
        content = " ".join([str(c) for c in content])
    print(f"  LLM assistant: {content[:300]}")

    # 检查 code block (OI 0.4.3 用 <tool_call><python>...</python> 或 <tool_call>\npython\n... 格式)
    import re
    # 抓所有可能的 code block(OI 0.4.3 实际格式很杂)
    all_codes = []
    # 1) ```python ... ```
    for m in re.finditer(r"```python\s*(.*?)```", content, re.DOTALL):
        all_codes.append(m.group(1))
    # 2) <tool_call><python>...</python>
    for m in re.finditer(r"<tool_call>\s*<python>(.*?)</python>", content, re.DOTALL):
        all_codes.append(m.group(1))
    # 3) <tool_call>\npython\n...\n</tool_call>
    for m in re.finditer(r"<tool_call>\s*\n?\s*python\s*\n(.*?)</tool_call>", content, re.DOTALL):
        all_codes.append(m.group(1))
    # 4) <tool_call>python\n...<tool_call>(裸的)
    for m in re.finditer(r"<tool_call>\s*python\s*\n(.*?)(?:<tool_call>|$)", content, re.DOTALL):
        all_codes.append(m.group(1))
    # 5) ``` ... ``` (无 python 标签)
    for m in re.finditer(r"```\s*(.*?)```", content, re.DOTALL):
        all_codes.append(m.group(1))

    code = "\n---\n".join(all_codes) if all_codes else None
    if code:
        # 取最长 code
        code = max(all_codes, key=len)
        print(f"  LLM code block ({len(code)} chars): {code[:300]}")
        called_tools = [t for t in tc["expect_tools"] if t in code]
        all_called = called_tools == tc["expect_tools"]
        all_args = all(arg in code for arg in tc["expect_args"])
        status = "pass" if (all_called and all_args) else "partial"
        reason = f"tools_called={called_tools} args_match={all_args} code={code[:80]}"
    else:
        status = "fail"
        reason = f"no python code block found in: {content[:200]}"
    results.append({**tc, "status": status, "reason": reason})
    print(f"  → {status.upper()}: {reason}\n")

# 4) 总结
n_pass = sum(1 for r in results if r["status"] == "pass")
print("=" * 60)
print(f"总结: {n_pass}/{len(results)} pass")
print("=" * 60)
for r in results:
    icon = "✓" if r["status"] == "pass" else "✗"
    print(f"  {icon} {r['name']}: {r['reason'][:80]}")
