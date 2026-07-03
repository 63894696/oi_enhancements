"""stagehand → OI agent E2E 测试

目标:让 OI interpreter 装上 stagehand 工具,然后跑一个真 web task。
测试两种调用方式:
  1. 直接调 interpreter.stagehand_run()(绕过 LLM,纯走 OI 增强器栈)
  2. 通过 OI chat() 让 LLM 自己决定调(更接近真实用法)
"""
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # oi_enhancements/

import importlib.util
spec = importlib.util.spec_from_file_location("oi_sh", HERE / "install.py")
oi_sh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oi_sh)


# ============================================================
# Test 1:直接调 interpreter.stagehand_run()(纯增强器栈)
# ============================================================
print("=" * 60)
print("Test 1: 直接 interpreter.stagehand_run() — 不经 LLM")
print("=" * 60)

# 配 OI interpreter
from interpreter import interpreter
interpreter.llm.model = "openai/devstral-small-2:24b"
interpreter.llm.api_base = "https://ollama.com/v1"
import winreg
reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment")
key, _ = winreg.QueryValueEx(reg, "OLLAMA_API_KEY")
winreg.CloseKey(reg)
interpreter.llm.api_key = key
interpreter.auto_run = False
interpreter.verbose = False

# 装 stagehand 工具
result = oi_sh.install_stagehand_tools(interpreter)
print(f"install result: {result}")
print(f"interpreter has stagehand_run: {hasattr(interpreter, 'stagehand_run')}")
print(f"interpreter.system_message length: {len(interpreter.system_message or '')}")
print()

# 跑真 stagehand(直接调,不经 LLM)
print("Test 1: 跑真 stagehand_run (wikipedia 搜索 Python)...")
steps = [
    {"action": "goto", "url": "https://www.wikipedia.org/"},
    {"action": "act", "instruction": "Click on the search input box at the top center of the page"},
    {"action": "act", "instruction": "Type the word 'Python' into the search input"},
    {"action": "act", "instruction": "Click the search button to submit the search"},
    {"action": "extract", "instruction": "main article title"},
]
t0 = time.time()
r = interpreter.stagehand_run(steps, timeout=300)
elapsed = time.time() - t0
print(f"  status={r['status']}, elapsed={elapsed:.1f}s")
for i, sr in enumerate(r.get("results", []), 1):
    print(f"  Step {i} ({sr.get('action')}): elapsed={sr.get('elapsed_ms')}ms")
    if sr.get("action") == "act":
        print(f"    success={sr.get('success')}, msg={sr.get('message', '')[:80]}")
    elif sr.get("action") == "extract":
        print(f"    result={str(sr.get('result', ''))[:120]}")

print()


# ============================================================
# Test 2:让 OI LLM 自己决定调 stagehand(更真实)
# ============================================================
print("=" * 60)
print("Test 2: OI LLM 自己决定调 stagehand — 自然语言任务")
print("=" * 60)

# 给 OI 一个 web task,看它能不能用 stagehand 工具
# 注意:OI 0.4.3 默认不让 LLM 直接调任意方法,所以这个测试可能需要
# 用 litellm 直调 + 在 prompt 里显式告诉它怎么调

import litellm

task_prompt = f"""你是一个 OI agent。你可以使用 stagehand 浏览器自动化工具做 web task。

## 工具可用

调用 `stagehand_run(steps)`,steps 是 list of dict:
- {{"action": "goto", "url": "..."}}  - 打开 URL
- {{"action": "act", "instruction": "..."}}  - 自然语言驱动浏览器(点击/输入)
- {{"action": "extract", "instruction": "..."}}  - 提取内容
- {{"action": "observe", "instruction": "..."}}  - 列出可操作元素

调用示例(伪代码):
```python
result = stagehand_run([
    {{"action": "goto", "url": "https://www.wikipedia.org/"}},
    {{"action": "act", "instruction": "Click search input then type Python"}},
    {{"action": "act", "instruction": "Click search button"}},
    {{"action": "extract", "instruction": "main article title"}},
])
print(result['results'][-1]['result'])
```

## 用户任务

"用浏览器帮我查一下 wikipedia 关于 Python 编程语言页面的标题是什么?"

## 你的回答

只输出 Python 代码(不要其他文字),写一个完整的 Python 脚本调用 stagehand_run
完成上述任务并 print 结果。
"""

print(f"  调 litellm 拿 LLM 决策代码...")
resp = litellm.completion(
    model="openai/devstral-small-2:24b",
    api_base="https://ollama.com/v1",
    api_key=key,
    messages=[{"role": "user", "content": task_prompt}],
    temperature=0,
    max_tokens=800,
)
llm_response = resp.choices[0].message.content.strip()
print(f"  LLM 生成的代码 (前 600 chars):")
print("  " + llm_response[:600].replace("\n", "\n  "))
print()

# 提取 python 代码块
import re
code_match = re.search(r"```python\n(.*?)```", llm_response, re.DOTALL)
if code_match:
    code = code_match.group(1).strip()
    print("  === 执行 LLM 生成的代码(用 exec,已注入 stagehand_run)===")
    # 给 LLM 一个 sandbox: 提供 stagehand_run + print
    sandbox = {
        "stagehand_run": interpreter.stagehand_run,
        "__import__": __import__,  # 让 LLM 也能 import
    }
    try:
        exec(code, sandbox)
    except Exception as e:
        print(f"  LLM 代码执行出错: {type(e).__name__}: {e}")
else:
    print("  LLM 没输出代码块")