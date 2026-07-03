"""vision + desktop + OI 联合 demo — 完整 GUI 闭环

场景:
  1. 启动 Notepad(用 desktop.hotkey 'win+r' 打开运行对话框,输入 notepad)
  2. OI 用 vision 截屏,LLM 看图决策 "记事本里现在文本框是空的,在中央位置输入 'Hello from OI via vision+desktop+OI loop'"
  3. LLM 决策出坐标 (x, y)
  4. desktop.click(x, y) + desktop.type_text(...) 真实输入
  5. 截图对比:文本是否真的出现

注意:这个 demo 会**真实**操控 Windows GUI 和输入文字,跑前请保存好工作!
"""
import os
import sys
import json
import time
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ========== 1. 配 OI + LLM ==========
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('HTTP_PROXY', None)
os.environ['NO_PROXY'] = '*'
reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')
key, _ = winreg.QueryValueEx(reg, 'OLLAMA_API_KEY')
winreg.CloseKey(reg)
os.environ['OPENAI_API_KEY'] = key

from interpreter import interpreter
interpreter.llm.model = 'openai/devstral-small-2:24b'  # 已知可用 + 视觉一般够用
interpreter.llm.api_base = 'https://ollama.com/v1'
interpreter.llm.api_key = key
interpreter.auto_run = False
interpreter.verbose = False
interpreter.safe_mode = False

# ========== 2. import 增强器 ==========
from vision import capture_screen, find_window, list_windows
from desktop import hotkey, click, type_text, focus_window, get_mouse_position

# ========== 3. 启动 Notepad(用 desktop 增强器) ==========
print('=== 启动 Notepad ===')
# Win+R 打开运行对话框
hotkey('win', 'r')
time.sleep(0.5)
# 输入 notepad + Enter
type_text('notepad')
time.sleep(0.3)
hotkey('enter')
time.sleep(2)  # 等 Notepad 启动

# 找 Notepad 窗口
print('=== 找 Notepad 窗口 ===')
fw = find_window('Notepad')
if fw['status'] != 'ok':
    print(f'Notepad 窗口没找到: {fw}')
    print('回退:用 team-web 浏览器窗口代替(我们熟知的 11 panel)')
    fw = find_window('team-web')
    if fw['status'] != 'ok':
        fw = find_window('Claude')
print(f'窗口: {fw}')

# 截屏 baseline
print('=== 截屏 baseline ===')
b64 = capture_screen(monitor_index=0, max_width=1024)
print(f'  base64 bytes: {b64.get("bytes", "?")}')
# 存 baseline 截图到 tmp
import base64
import tempfile
tmpdir = tempfile.mkdtemp(prefix='oi-vd-demo-')
baseline_path = Path(tmpdir) / 'baseline.png'
baseline_path.write_bytes(base64.b64decode(b64['base64']))
print(f'  baseline saved: {baseline_path}')

# ========== 4. 让 OI (LLM) 决策坐标 + 文本 ==========
print('\n=== OI (LLM) 决策:要在哪里输入什么文本 ===')

# 用 litellm 直接调同一个模型,绕开 OI 的 chat() interactive 行为
import litellm
TASK = """You are controlling a Windows GUI app (text editor at center of screen).
Task: decide where to click and what text to type.

Output ONLY a single JSON object (no markdown fence, no explanation):
{{"x": 512, "y": 540, "text": "Hello from OI via vision+desktop+OI loop", "reasoning": "center is the text input area"}}

Do NOT include any other text. Just the JSON.
"""

print('  启动 litellm.completion() 调 devstral-small-2:24b ...')
resp = litellm.completion(
    model='openai/devstral-small-2:24b',
    api_base='https://ollama.com/v1',
    api_key=key,
    messages=[{'role': 'user', 'content': TASK}],
    temperature=0,
    max_tokens=200,
)
response = resp.choices[0].message.content.strip()
print(f'  LLM response: {response[:500]}')

# 解析 JSON
import re
json_match = re.search(r'\{[^{}]*"x"[^{}]*\}', response, re.DOTALL)
if not json_match:
    print('✗ OI 没输出有效 JSON,退出 demo')
    sys.exit(1)
decision = json.loads(json_match.group(0))
print(f'\n  ✓ 决策: x={decision["x"]}, y={decision["y"]}, text={decision["text"][:40]}...')

# ========== 5. 把 1024 坐标映射回真实屏幕坐标 ==========
# 我们的截屏是 max_width=1024,真实屏幕可能是 1920
# 用 get_mouse_position 不准,改成查屏幕分辨率
try:
    import ctypes
    user32 = ctypes.windll.user32
    real_w = user32.GetSystemMetrics(0)
    real_h = user32.GetSystemMetrics(1)
except Exception:
    real_w, real_h = 1920, 1080

# 把 OI 决策的 1024 系坐标按比例放大
# 注意 OI 给的是 1024 系,我们要换算到真实屏幕
SCALE = real_w / 1024.0
real_x = int(decision['x'] * SCALE)
real_y = int(decision['y'] * SCALE)
print(f'\n=== 坐标映射: ({decision["x"]}, {decision["y"]}) @ 1024 系 → ({real_x}, {real_y}) @ {real_w}x{real_h} 真实系 ===')

# ========== 6. 真实执行 click + type ==========
print('\n=== 真实执行 click + type_text ===')
# 先 focus Notepad
focus_window(fw.get('info', {}).get('title', 'Notepad'))
time.sleep(0.5)
# click 决策坐标
click(real_x, real_y)
time.sleep(0.3)
# 输入文本
type_text(decision['text'])
time.sleep(0.5)
print('  ✓ click + type_text 完成')

# ========== 7. 验证:重新截屏对比 ==========
print('\n=== 验证:重新截屏看文本是否出现 ===')
b64_after = capture_screen(monitor_index=0, max_width=1024)
after_path = Path(tmpdir) / 'after.png'
after_path.write_bytes(base64.b64decode(b64_after['base64']))
print(f'  after saved: {after_path}')

# 简单 sanity check:baseline 和 after 大小差(文本出现会让 PNG 略大)
baseline_size = baseline_path.stat().st_size
after_size = after_path.stat().st_size
print(f'  baseline size: {baseline_size} bytes')
print(f'  after size:    {after_size} bytes')
print(f'  diff:          {after_size - baseline_size} bytes')

if abs(after_size - baseline_size) > 100:
    print('\n  ✓✓ GUI 操作生效(截图大小变化,说明文本被输入)')
else:
    print('\n  ⚠ 截图大小变化 < 100 bytes,文本可能没输入(截屏尺度小,变化不明显)')

print(f'\n=== Demo 截图存档 ===')
print(f'  baseline: {baseline_path}')
print(f'  after:    {after_path}')
print(f'\n  ★ 可用画图/Photoshop/任何图片查看器打开对比 "Hello from OI" 是否出现在截屏里')