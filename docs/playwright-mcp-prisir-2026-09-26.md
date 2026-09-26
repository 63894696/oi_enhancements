# P3j T25 — Playwright MCP 接入 (PrisirAI · 2026-09-26)

## 背景

P3j T24 调研 50+ GitHub agent 能力层项目后,唯一推荐推进的 P2 候选是 **Microsoft Playwright MCP**(~18.5k stars)。T25 把它接入到 PrisirAI 主对话,作为「真浏览器交互 / 点击 / 填表单」能力层。

## 设计决策(用户已拍板)

| 决策 | 选择 | 原因 |
|------|------|------|
| 协议 | stdio JSON-RPC 子进程桥 | 跟 agent_reach_bridge 范式一致;避免启 HTTP/SSE server |
| tool 范围 | 7 个核心(navigate / snapshot / click / type / evaluate / screenshot / close) | 不接 PDF / vision / devtools / network / storage / RCE |
| 浏览器模式 | headless 默认(`--headless`) | 避免 popup 干扰 |
| Python 包 | **不装 `playwright`** | Node.js + npx 已足够 |
| 超时 | 30s | 浏览器操作可能慢 |
| 风险分级 | click/type = L1(确认卡),余 L0 | 副作用操作需前端确认 |

## 实现概览

### 模块结构

```
prisir_work/playwright_bridge.py       ~450 行
  ├─ _JSONRPCClient                    JSON-RPC 2.0 over stdio
  │   ├─ start()                       Popen + 启 read thread + initialize handshake
  │   ├─ _read_loop()                  daemon thread 按 id 路由响应到 queue.Queue
  │   ├─ _send() / _request()          写 stdin + 阻塞等 stdout 响应
  │   ├─ _initialize()                 initialize + notifications/initialized
  │   ├─ call_tool()                   封装 tools/call MCP 方法
  │   └─ close()                       best-effort shutdown + terminate + 清理 pending
  ├─ pw_health()                       3 档 mode:missing_node / not_installed / ready
  ├─ pw_navigate / pw_snapshot / pw_click / pw_type
  ├─ pw_evaluate / pw_screenshot / pw_close
```

### 关键代码模式

#### 1. 常驻 stdio 子进程 + JSON-RPC

```python
class _JSONRPCClient:
    def start(self):
        self._proc = subprocess.Popen(self._cmd, stdin=PIPE, stdout=PIPE,
                                      text=True, bufsize=1)
        self._read_thread = Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()
        return self._initialize()  # initialize + notifications/initialized
```

#### 2. 请求/响应通过 queue.Queue 按 id 路由

```python
def _request(self, method, params=None, *, timeout=30.0):
    with self._id_lock:
        self._next_id += 1
        rid = self._next_id
        q = queue.Queue(maxsize=1)
        self._pending[rid] = q
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None: msg["params"] = params
    self._send(msg)  # 写 stdin
    try:
        resp = q.get(timeout=timeout)
    except Empty:
        return {"ok": False, "error": "playwright_call_timeout"}
    # ...
```

#### 3. read thread 按 id 派发

```python
def _read_loop(self):
    for line in self._proc.stdout:
        msg = json.loads(line.strip())
        msg_id = msg.get("id")
        if msg_id is None: continue  # notification, ignore
        q = self._pending.pop(int(msg_id), None)
        if q is None: continue  # 未知 id, warn
        q.put({"raw": msg, "error": msg.get("error"),
               "result": msg.get("result")})
```

#### 4. health 3 档

```python
def pw_health():
    if not shutil.which("node"):
        return {"ok": False, "mode": "missing_node",
                "hint": "装 Node.js ≥ 18"}
    if not shutil.which("npx"):
        return {"ok": False, "mode": "missing_npx"}
    proc = subprocess.run(["npx", "-y", "@playwright/mcp", "--help"],
                          capture_output=True, text=True, timeout=20.0)
    if proc.returncode != 0:
        return {"ok": False, "mode": "not_installed"}
    # 真启 + initialize handshake
    c = _JSONRPCClient([...])
    init_r = c.start()
    if not init_r["ok"]:
        return {"ok": False, "mode": "start_failed"}
    c.close()
    return {"ok": True, "mode": "ready"}
```

### 关键 bug 与修复

**Bug 1:threading.Lock 非可重入,导致 _initialize deadlock**

`_initialize` 末尾调 `self._send(...)` 通知 `notifications/initialized`。原本写的是:

```python
with self._send_lock:        # ← 持锁
    self._send({...})        # ← _send 内部又 with self._send_lock: ← 死锁!
```

`threading.Lock()` 是**非可重入**的,主线程在自己持锁的状态下等自己释放 → 永久阻塞。修复:**只在 `_send` 内部持锁,`_initialize` 不再额外加锁**(注释里写明)。

### 8 endpoints + 8 capabilities

| endpoint | method | risk | capability |
|---------|--------|------|-----------|
| `/web/playwright/health` | POST | L0 | `web.playwright.health` |
| `/web/playwright/navigate` | POST | L0 | `web.playwright.navigate` |
| `/web/playwright/snapshot` | POST | L0 | `web.playwright.snapshot` |
| `/web/playwright/click` | POST | L1 | `web.playwright.click` |
| `/web/playwright/type` | POST | L1 | `web.playwright.type` |
| `/web/playwright/evaluate` | POST | L0 | `web.playwright.evaluate` |
| `/web/playwright/screenshot` | POST | L0 | `web.playwright.screenshot` |
| `/web/playwright/close` | POST | L0 | `web.playwright.close` |

click / type 因为有副作用(填数据 / 点按钮可能触发提交),L1 弹前端确认卡;其余 L0 直发。

### 错误翻译(agent_main_chat_hook)

```python
_TRANSLATIONS = [
    ("playwright_missing_node", "Node.js 未装(浏览器 MCP 需要 Node ≥ 18)", "https://nodejs.org"),
    ("playwright_npx_failed", "npx 调用失败", "/extensions"),
    ("playwright_not_initialized", "浏览器 MCP 握手失败", "/extensions"),
    ("playwright_call_timeout", "浏览器操作 30s 超时", "/extensions"),
    ("playwright_send_", "MCP stdin 写入失败", "/extensions"),
    ("playwright_rpc_error", "MCP 协议错误", "/extensions"),
    ("playwright_not_started", "MCP 子进程已退出", "/extensions"),
    ("playwright_npx_not_found", "找不到 npx 命令", "https://nodejs.org"),
    ("playwright_npx_timeout", "npx 拉包超时", "/extensions"),
    ("playwright_missing_npx", "npx 缺失", "https://nodejs.org"),
] + _TRANSLATIONS
```

## 端到端使用流程

### 主对话「打开 v2ex.com/t/123456 帮我看第 1 楼说什么」

1. LLM 解析 → `[[EXEC: web.playwright.navigate url="https://v2ex.com/t/123456"]]`
2. 前端 → `/web/playwright/navigate` → `pw_navigate(url)`
3. JSON-RPC → npx @playwright/mcp 子进程 → 启动 Chromium headless
4. 浏览器打开 URL → 返回 `{content: "Navigated to ..."}`
5. 前端显示 ✓ → LLM 继续 → `[[EXEC: web.playwright.snapshot]]`
6. 取 a11y 树 → LLM 解析 → 提取第 1 楼文本 → 反馈用户

### 主对话「帮我填这个表单 username=test」

1. `[[EXEC: web.playwright.navigate url="..."]]`
2. `[[EXEC: web.playwright.snapshot]]` → 拿 a11y 树 → 找 ref=eN
3. **前端弹 L1 确认卡**(填 username=test)
4. 用户点确认 → `[[EXEC: web.playwright.type text="test" ref="eN"]]`
5. 浏览器填入 → 返回 `{content: "Typed 'test'"}`
6. 前端显示 ✓

## 测试覆盖

`tests/test_playwright_bridge.py` 14 mock case:
- pw_health 3 档(missing_node / npx_failed / not_installed / ready)
- JSON-RPC 请求/响应(成功 / timeout / RPC error)
- pw_navigate / pw_click / pw_type / pw_evaluate 入参校验
- pw_close 终止子进程
- stdin write count 校验(initialize + notification + call)

`tests/test_playwright_endpoints.py` 10 case:
- 8 endpoints 在 _REGISTRY + risk 分级
- 8 capabilities 注册 + keywords + L1 confirm
- endpoint handler 透传(mock pw_bridge fn)
- 4 错误翻译在 _TRANSLATIONS
- endpoint 拿掉上游时不抛栈

**23/23 tests pass · 60/60 verify checks pass**

## 已知限制

| 项 | 当前 | 未来 |
|----|------|------|
| 工具范围 | 7 个核心 | PDF / vision / devtools 等 opt-in caps |
| 浏览器 | chromium only | firefox / webkit |
| 会话模式 | 单实例(每次 `start()` 起新进程) | 多实例 / 持久会话 |
| Playwright Python 包 | **不装**(避免重复依赖) | 如需 Python API 可加 |
| RCE 风险工具 | `browser_run_code_unsafe` **白名单不接** | 不接 |
| 网络访问 | 默认 npx 拉 `@playwright/mcp@latest` | 可 pin 版本 |

## 安装

主对话走「浏览器 MCP」相关能力时,如本机未装 Node.js,前端弹卡提示:

```
Node.js 未装(浏览器 MCP 需要 Node ≥ 18)
→ https://nodejs.org
```

装 Node.js ≥ 18 后,首次调用 `pw_navigate` 会触发 npx 自动拉 `@playwright/mcp@latest`(首次约 30s+,下载 Chromium ~200MB)。

## 跟既有能力的边界

| 能力 | 用例 | 何时用 |
|------|------|--------|
| **web_fetch**(urllib) | 静态 / 已渲染 HTML | 优先走,极快 |
| **web_fetch_jina**(r.jina.ai) | 防反爬 + 渲染 HTML | web_fetch 拿不到时 |
| **web_fetch_ytdlp** | 200+ 视频 / 音频 metadata | 视频 / 音频类 |
| **web_fetch_feedparser** | RSS / Atom | 订阅源 |
| **gh_api**(gh CLI) | GitHub repo / issue / PR | GitHub 特定场景 |
| **web_reach**(agent-reach) | 14 平台通用 reader | 第三方服务 |
| **hn_algolia** | HackerNews 搜索 | HN 特定场景 |
| **exa_mcp** | 语义搜索 | 研究类搜索 |
| **🎉 playwright_mcp**(T25) | **真浏览器交互 / 点击 / 填表单** | **SPA / 登录后内容 / 需交互才拿到的数据** |

## E2E 真跑步骤

```bash
# 1. 装 Node.js(若未装)
winget install OpenJS.NodeJS.LTS

# 2. 验证
python -c "from prisir_work import playwright_bridge as pw; print(pw.pw_health())"
# 首次会下载 Chromium(~200MB),需要 30s+

# 3. 主对话 E2E:
# 「打开 https://news.ycombinator.com,告诉我前 3 条标题」
# → LLM 走 [[EXEC: web.playwright.navigate]] + [[EXEC: web.playwright.snapshot]]
# → 提取标题反馈

# 4. 真填表单 E2E:
# 「打开 https://example.com/login,填 username=test,password=x」
# → LLM 走 navigate → snapshot → click/type(L1 确认卡)→ 提交
```

## Rollback

- `prisir_work/playwright_bridge.py` 文件删除
- `prisir_work/endpoints.py` 删 8 行 `@register` 装饰器
- `prisir_work/capability.py` 删 8 行 `register_capability`
- `prisir_work/agent_main_chat_hook.py` 删 4 行翻译
- `tests/test_playwright_*.py` 文件删除
- `verify_wechat_publisher.py` 删 4 check + 4 CHECKS 行

## 数据流

```
主对话「打开 v2ex.com/t/123456 帮我看第 1 楼说什么」
  ↓ LLM 解析出 [[EXEC: web.playwright.navigate url="..."]]
  ↓ endpoints._web_pw_navigate
  ↓ pw_bridge.pw_navigate
  ↓ _JSONRPCClient.call_tool("browser_navigate", {url})
  ↓ JSON-RPC over stdio → npx @playwright/mcp 子进程
  ↓ 启动 Chromium headless → 打开 URL → 返 {content: "Navigated to ..."}
  ↓ build_exec_result
  ↓ ws event: capability_exec_result
  ↓ 前端渲染 + LLM 后续 [[EXEC: web.playwright.snapshot]] 取 a11y 树
  ↓ a11y 树喂 LLM → 提取第 1 楼文本 → 反馈用户
```