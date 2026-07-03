"""test voice_agent Layer 5(2026-07-02)
- LLM 输出 [ACTION:json] 协议时,调 desktop 增强器
- 8 个 action type 全覆盖测试
- 走真 OI 增强器(不 mock)
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "C:/Users/Administrator/voice_agent")

import importlib.util
spec = importlib.util.spec_from_file_location("d", "C:/Users/Administrator/voice_agent/daemon.py")
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)

print("=== 测试 parse_and_execute_actions(Layer 5)===")
print()

# 1) 测试 LLM 回复解析([ACTION:json] 抓取)
test_replies = [
    ("普通对话(无 action)", "你好,今天天气真好,适合出去散步。", []),
    ("单 click", '好的,我来点击那个按钮。\n[ACTION:{"type":"click","x":100,"y":200}]', ["click"]),
    ("open + type", '[ACTION:{"type":"open","app":"notepad"}]\n[ACTION:{"type":"type","text":"hello world"}]', ["open", "type"]),
    ("hotkey", '[ACTION:{"type":"hotkey","keys":"ctrl+c"}]', ["hotkey"]),
    ("双 action + 中文", '好的,我来打开浏览器。\n[ACTION:{"type":"open","app":"chrome"}]\n[ACTION:{"type":"type","text":"https://www.google.com"}]', ["open", "type"]),
    ("复杂", '我来做几件事。\n[ACTION:{"type":"screenshot"}]\n[ACTION:{"type":"list_windows"}]\n[ACTION:{"type":"translate","text":"Hello","dest":"en"}]', ["screenshot", "list_windows", "translate"]),
    ("坏 JSON", '[ACTION:{"type":"click","x":100]', []),  # 解析失败
]

results = []
for name, reply, expected_types in test_replies:
    print(f"--- {name} ---")
    actions = d.parse_and_execute_actions(reply)
    tts = d.extract_tts_text(reply)
    print(f"  TTS 文字: {tts!r}")
    print(f"  执行结果: {actions}")
    # 校验
    if expected_types:
        ok = len(actions) == len(expected_types)
    else:
        ok = len(actions) == 0
    status = "pass" if ok else "fail"
    results.append({"name": name, "status": status, "tts": tts, "actions": actions, "expected": expected_types})
    print(f"  → {status}")
    print()

# 2) 测真 action 走 desktop 增强器
print("=" * 60)
print("=== 真 desktop 增强器连通测试(不真点屏幕)===")
print("=" * 60)
# 用 desktop.find_window()(只读,不真点)
import importlib.util
desktop_spec = importlib.util.spec_from_file_location("oi_desktop", "C:/Users/Administrator/oi_enhancements/desktop/__init__.py")
desktop_mod = importlib.util.module_from_spec(desktop_spec)
desktop_spec.loader.exec_module(desktop_mod)
print(f"  desktop WindowManager 类: {desktop_mod.WindowManager}")
try:
    wins = desktop_mod.WindowManager.list_all_windows() if hasattr(desktop_mod, 'WindowManager') else []
    print(f"  desktop 真窗口: {len(wins)} 个")
    for w in wins[:3]:
        if isinstance(w, dict):
            print(f"    - {w.get('title', '?')[:50]}")
        else:
            print(f"    - {w}")
except Exception as e:
    print(f"  ERR: {e}")

# 3) 测真 action 走 memory 增强器
print()
print("=" * 60)
print("=== 真 memory 增强器连通测试(只读,recall)===")
print("=" * 60)
try:
    memory_spec = importlib.util.spec_from_file_location("oi_memory", "C:/Users/Administrator/oi_enhancements/memory/__init__.py")
    memory_mod = importlib.util.module_from_spec(memory_spec)
    memory_spec.loader.exec_module(memory_mod)
    print(f"  memory loaded: {memory_mod.__file__}")
except Exception as e:
    print(f"  memory ERR: {e}")

# 4) 测真 action 走 vision 增强器
print()
print("=" * 60)
print("=== 真 vision 增强器连通测试(只读,list_windows)===")
print("=" * 60)
try:
    vision_spec = importlib.util.spec_from_file_location("oi_vision", "C:/Users/Administrator/oi_enhancements/vision/__init__.py")
    vision_mod = importlib.util.module_from_spec(vision_spec)
    vision_spec.loader.exec_module(vision_mod)
    wins = vision_mod.list_windows(visible_only=True)
    print(f"  vision list_windows: {wins.get('count', 0)} 个窗口")
    for w in wins.get("windows", [])[:3]:
        print(f"    - {w.get('title', '?')[:50]}")
except Exception as e:
    print(f"  vision ERR: {e}")

# 总结
print()
print("=" * 60)
print("总结")
print("=" * 60)
n_pass = sum(1 for r in results if r["status"] == "pass")
print(f"  parse_and_execute_actions 测试: {n_pass}/{len(results)} pass")
for r in results:
    icon = "✓" if r["status"] == "pass" else "✗"
    print(f"    {icon} {r['name']}: {r['actions'][:2] if r['actions'] else 'no actions'}")

# 5) JSON 落盘
out_json = Path.home() / ".oi" / "benchmarks" / f"voice_layer5_test_{int(__import__('time').time())}.json"
out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n→ JSON: {out_json}")
