"""OI memory 增强器端到端 demo —— 装 hooks 到 OI,做一次真 chat,看 recall 是否生效"""
import os
import sys
import winreg

# 1. 注入 OLLAMA key(沿用其他 OI 脚本的模式)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('HTTP_PROXY', None)
os.environ['NO_PROXY'] = '*'
reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')
key, _ = winreg.QueryValueEx(reg, 'OLLAMA_API_KEY')
winreg.CloseKey(reg)
os.environ['OPENAI_API_KEY'] = key

HERE = Path = __import__('pathlib').Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from oi_memory_hooks import store, install, get_memory, recall

# 2. 预先 store 一些 L0/L1(身份 + 关键事实)
store('L0', 'user:zrkwedii9', '用户偏好中文,偏好写笔记入 Obsidian,沟通风格直接不啰嗦', ['preference'])
store('L1', 'project:team-web', 'C:/Users/Administrator/demos/team-web — FastAPI+11 panel 多 agent 协作面板', ['project'])
store('L1', 'project:peckaboo', 'C:/Users/Administrator/.gemini/tmp/gemini-temp-1778570286463/Peekaboo-W — 用户的旧版看屏 agent', ['project'])
store('L2', 'task:panel-bug', 'app.js 第 64 行 COLUMN_LAYOUT 硬编码 software-dev role,切到 customer-service 9 个 panel 只剩 2 个', ['bug', 'team-web'])

print('=== pre-seeded memories ===')
print(f'  stats: {get_memory().stats()["total"]} 条')
print()

# 3. 配 OI + 装 hooks
from interpreter import interpreter
interpreter.llm.model = 'openai/devstral-small-2:24b'  # 已知可用 + 便宜
interpreter.llm.api_base = 'https://ollama.com/v1'
interpreter.llm.api_key = key
interpreter.auto_run = False   # 不用 Jupyter 内核,避免 sandbox 隔离问题
interpreter.verbose = False

install(interpreter, agent_name='oi-memory-demo')
print('>>> OI configured + memory hooks installed')

# 4. 做一次 chat,内容应该匹配预存记忆
TASK = '你能简单介绍一下 team-web 这个项目吗?它有什么 panel?'
print(f'\n>>> 启动 OI chat() task="{TASK[:50]}..."')
print('-' * 60)

import time
start = time.time()
response_chunks = []
for chunk in interpreter.chat(TASK):
    response_chunks.append(chunk)
    if isinstance(chunk, dict):
        if chunk.get('type') == 'message' and isinstance(chunk.get('content'), str):
            print(chunk['content'], end='', flush=True)
    elif isinstance(chunk, str):
        print(chunk, end='', flush=True)
print()
print('-' * 60)
print(f'>>> done in {time.time()-start:.1f}s')

# 5. 验证 post_chat 自动 store 了对话快照
print('\n=== post-chat memory state ===')
stats = get_memory().stats()
print(f'  total now: {stats["total"]} 条')
print(f'  by layer: {stats["by_layer"]}')
print(f'  top accessed:')
for h in stats['top_accessed'][:3]:
    print(f'    [{h["layer"]}] {h["title"]} (accessed {h["access_count"]}x)')

# 6. 验证 recall 能召回刚才的对话(按"team-web panel" query)
print('\n=== 验证 recall ===')
hits = recall('team-web panel 项目介绍', n=5)
for h in hits:
    print(f'  [{h["layer"]}] {h["title"]}')