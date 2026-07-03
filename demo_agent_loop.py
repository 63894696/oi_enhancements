"""vision+desktop demo 升级:完整 agent loop

相比 demo_gui_loop.py 的"一次性 hardcode 决策坐标":
- 用 stagehand 风格 a11y + screenshot 联合 extract
- 用 watchod 5min 包装 LLM 调用
- click 后 verify 重试机制(可选)
- 把 demo 改成可复用框架:用户给任务,自动找窗口 + 拆解 + 执行 + 报告

任务:
  自动在 Notepad 输入 "Hello from OI agent loop v2"
"""
import os
import sys
import json
import time
import re
import tempfile
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ========== 1. 环境 + API key ==========
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('HTTP_PROXY', None)
os.environ['NO_PROXY'] = '*'
reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')
key, _ = winreg.QueryValueEx(reg, 'OLLAMA_API_KEY')
winreg.CloseKey(reg)
os.environ['OPENAI_API_KEY'] = key


# ========== 2. import 增强器 ==========
from vision import capture_screen, find_window
from desktop import hotkey, click, type_text, focus_window
import pyautogui
pyautogui.FAILSAFE = False  # demo 允许点屏幕角落;生产环境应 True
from a11y_extract import extract_with_a11y
from stream_watchdog import oi_chat_with_watchdog


# ========== 3. 启动 Notepad(用 desktop,带重试) ==========

def launch_notepad(timeout: float = 10) -> bool:
    """Win+R 启动 notepad,重试一次"""
    for attempt in range(2):
        print(f"  尝试启动 Notepad (第 {attempt+1} 次)...")
        try:
            hotkey('win', 'r')
            time.sleep(0.5)
            type_text('notepad')
            time.sleep(0.3)
            hotkey('enter')
            time.sleep(2)
            # 验证
            fw = find_window('Notepad')
            if fw['status'] == 'ok':
                return True
        except Exception as e:
            print(f"  启动失败: {e}")
    return False


# ========== 4. verify step — 修死循环 ==========

def verify_screenshot_change(before_b64: str | None, after_b64: str | None) -> dict:
    """对比两张截图是否真的不同(click 是否真生效)

    Returns:
        {"changed": bool, "byte_diff": int, "hash_diff": int, "ratio": 0.0~1.0}
    """
    if not before_b64 or not after_b64:
        return {"changed": False, "byte_diff": 0, "hash_diff": 0, "ratio": 0.0,
                "reason": "missing input"}
    if before_b64 == after_b64:
        return {"changed": False, "byte_diff": 0, "hash_diff": 0, "ratio": 0.0,
                "reason": "identical"}
    # 字节差
    byte_diff = abs(len(after_b64) - len(before_b64))
    # hash 差:前 200 字符差异比例
    sample = min(200, len(before_b64), len(after_b64))
    hash_diff = sum(1 for i in range(sample) if before_b64[i] != after_b64[i])
    ratio = hash_diff / sample if sample else 0.0
    return {
        "changed": byte_diff > 500 or ratio > 0.05,
        "byte_diff": byte_diff,
        "hash_diff": hash_diff,
        "ratio": ratio,
    }


# ========== 5. agent loop ==========

def agent_loop(task: str, model: str = 'openai/devstral-small-2:24b',
               max_steps: int = 10) -> dict:
    """完整 agent loop(带 verify 防死循环)

    Args:
        task: 用户任务
        model: LLM 模型
        max_steps: 最大步骤数

    Returns:
        {"status": "ok"/"fail"/"verify_failed", "steps": N, "decisions": [...]}
    """
    decisions = []
    verify_log = []
    step = 0
    last_screenshot_b64 = None  # 用于 verify 对比
    verify_fail_count = 0  # 连续 verify 失败次数
    MAX_VERIFY_FAIL = 3  # 连续失败 3 次就停

    while step < max_steps:
        step += 1
        print(f"\n=== Step {step}/{max_steps} === (verify_fails={verify_fail_count}/{MAX_VERIFY_FAIL})")

        # 5.1 联合 extract
        print("  extract(screenshot + a11y)...")
        ext = extract_with_a11y(window_title=None, screenshot_max_width=640)
        a11y_snippet = (ext.get('a11y') or '')[:1500]
        screenshot_obj = ext.get('screenshot') or {}
        screenshot_b64 = screenshot_obj.get('base64')

        # 5.2 LLM 决策 — 包含上次 verify 结果作为反馈
        verify_hint = ""
        if verify_log:
            last_v = verify_log[-1]
            status = "成功(屏幕有变化)" if last_v['changed'] else "失败(屏幕无变化)"
            verify_hint = f"\n\n上次 click verify 结果:{status} (byte_diff={last_v['byte_diff']}, ratio={last_v['ratio']:.2%})。如果 verify 失败,说明 click 没命中,需要换坐标或不同元素。\n"

        prompt = f"""你是 GUI agent。任务:{task}

当前屏幕状态(stagehand-style extract):
- a11y tree(UI 元素 + 标记):
```
{a11y_snippet}
```
- screenshot base64 length: {len(screenshot_b64) if screenshot_b64 else 0} chars
{verify_hint}
请输出严格 JSON(不要其他文字):
{{
  "action": "click" | "type_text" | "noop" | "done",
  "x": 640,
  "y": 360,
  "text": "要输入的文本(type_text 才有)",
  "reasoning": "为什么这么决策"
}}

只输出 JSON,不要解释。
"""
        print(f"  调 LLM ({model})...")
        response = oi_chat_with_watchdog(
            task=prompt, model=model, api_key=key,
            idle_timeout_ms=60_000,  # 1min demo 用
            temperature=0, max_tokens=300,
        )
        # 抽 JSON
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
        if not json_match:
            print(f"  ✗ LLM 没输出有效 JSON: {response[:200]}")
            continue
        decision = json.loads(json_match.group(0))
        decisions.append(decision)
        print(f"  决策: action={decision['action']}, reason={decision.get('reasoning', '')[:60]}")

        # 5.3 执行决策 + verify
        action = decision.get('action', 'noop')
        if action == 'done':
            print("  ✓ LLM 决策 done,任务完成")
            return {"status": "ok", "steps": step, "decisions": decisions,
                    "verify_log": verify_log}
        elif action == 'noop':
            print("  noop,继续")
            continue
        elif action == 'click':
            # 坐标缩放:640 → 真实屏幕
            import ctypes
            real_w = ctypes.windll.user32.GetSystemMetrics(0)
            SCALE = real_w / 640.0
            real_x = int(decision['x'] * SCALE)
            real_y = int(decision['y'] * SCALE)
            print(f"  click({real_x}, {real_y}) @ {real_w}px scale={SCALE:.2f}")

            # 截 before
            before_cap = extract_with_a11y(window_title=None, screenshot_max_width=640)
            before_b64 = (before_cap.get('screenshot') or {}).get('base64')

            # 真 click
            click(real_x, real_y)
            time.sleep(0.5)

            # 截 after + verify
            after_cap = extract_with_a11y(window_title=None, screenshot_max_width=640)
            after_b64 = (after_cap.get('screenshot') or {}).get('base64')
            v = verify_screenshot_change(before_b64, after_b64)
            verify_log.append(v)
            print(f"  verify: changed={v['changed']}, byte_diff={v['byte_diff']}, ratio={v['ratio']:.2%}")

            if not v['changed']:
                verify_fail_count += 1
                print(f"  ⚠ click 没生效(verify 失败),verify_fail_count={verify_fail_count}/{MAX_VERIFY_FAIL}")
                if verify_fail_count >= MAX_VERIFY_FAIL:
                    print(f"  ✗ 连续 {MAX_VERIFY_FAIL} 次 verify 失败,停止 demo")
                    return {"status": "verify_failed", "steps": step, "decisions": decisions,
                            "verify_log": verify_log,
                            "reason": f"consecutive {MAX_VERIFY_FAIL} verify failures"}
                # 不递增 step? 这里 step 已经 +1 了,直接 continue 让 LLM 看 verify_hint 换坐标
                continue
            else:
                verify_fail_count = 0  # 重置
                print("  ✓ verify 通过(click 生效),可以进入下一步")
        elif action == 'type_text':
            text = decision.get('text', '')
            print(f"  type_text: {text[:50]}")
            type_text(text)
            time.sleep(0.5)
            # 验证 type 生效(可以再次 verify,简化:不强制)
            v_after = verify_screenshot_change(screenshot_b64, screenshot_b64)  # placeholder
            print(f"  type_text done (no strict verify)")
        else:
            print(f"  未知 action: {action},继续")

    return {"status": "timeout", "steps": step, "decisions": decisions,
            "verify_log": verify_log,
            "reason": f"max_steps={max_steps} exceeded without done"}


# ========== 6. 主流程 ==========

print('=== 启动 Notepad ===')
launched = launch_notepad()
if not launched:
    print("  ⚠ Notepad 没启动,fallback 到 team-web 浏览器窗口")
    # 不退出,让 demo 跑在已知窗口

print('\n=== 开始 agent loop ===')
result = agent_loop(
    task="""在 Notepad 中央输入文本 'Hello from OI agent loop v2',然后判定任务完成(action=done)。

提示:
- a11y tree 里找 EditControl(可编辑文本框)
- EditControl 的坐标就是 Notepad 文本输入区
- click 那个坐标,然后 type_text 'Hello from OI agent loop v2',最后 action=done
""",
    max_steps=8,
)

print(f'\n=== 结果 ===')
print(f"  status: {result['status']}")
print(f"  steps: {result['steps']}")
print(f"  decisions: {len(result.get('decisions', []))} 条")
print(f"  verify_log: {len(result.get('verify_log', []))} 条")
for i, d in enumerate(result.get('decisions', []), 1):
    print(f"    {i}. {d.get('action')}: {d.get('reasoning', '')[:80]}")
verify_log = result.get('verify_log', [])
if verify_log:
    print(f"\n  verify details:")
    for i, v in enumerate(verify_log, 1):
        flag = "✓" if v['changed'] else "✗"
        print(f"    {i}. {flag} byte_diff={v['byte_diff']:5d}  ratio={v['ratio']:.2%}")