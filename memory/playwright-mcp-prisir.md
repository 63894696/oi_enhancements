---
name: playwright-mcp-prisir
description: P3j T25 — Microsoft Playwright MCP(~18.5k stars)stdin 子进程桥接入;真浏览器交互/点击/填表单;threading.Lock 非可重入坑
metadata:
  type: project
---

P3j T25(2026-09-26 ship)— 把 Microsoft Playwright MCP(~18.5k stars)通过 stdio JSON-RPC 子进程桥接入 PrisirAI 主对话。

## 关键决策

- 走 stdio JSON-RPC 子进程(跟 agent_reach_bridge 一致,**不**走 HTTP/SSE server)
- 只接 7 个核心 tool:navigate / snapshot / click / type / evaluate / screenshot / close(不接 RCE / vision / devtools)
- 默认 headless(`--headless`),chromium only
- 不装 `playwright` Python 包(Node.js + npx 就够)
- click/type = L1(前端确认卡),余 L0
- 超时 30s

## 关键坑:threading.Lock 非可重入导致死锁

`_initialize` 末尾要发 `notifications/initialized` 通知。原本写:

```python
with self._send_lock:        # 主线程持锁
    self._send({...})        # _send 内部又 with self._send_lock: ← 死锁!
```

`threading.Lock()` 是**非可重入**的(对比 `RLock`),主线程在自己持锁的状态下等自己释放 → 永久阻塞。

**修法**:`_initialize` 不再额外加锁,只在 `_send` 内部持锁。注释里写明"`_send` 自己持 `_send_lock`,这里别再套一层"。

详见 [docs/playwright-mcp-prisir-2026-09-26.md](../docs/playwright-mcp-prisir-2026-09-26.md) 「关键 bug 与修复」节。

## 跨能力边界

PrisirAI 现有 8 类 fetcher/provider:

| 能力 | 用例 |
|------|------|
| web_fetch(urllib) | 静态 / 已渲染 HTML |
| web_fetch_jina(r.jina.ai) | 防反爬 + 渲染 HTML |
| web_fetch_ytdlp | 200+ 视频 / 音频 metadata |
| web_fetch_feedparser | RSS / Atom |
| gh_api(gh CLI) | GitHub repo / issue / PR |
| web_reach(agent-reach) | 14 平台通用 reader |
| hn_algolia | HN 搜索 |
| exa_mcp | 语义搜索 |
| **playwright_mcp(T25)** | **真浏览器交互 / 点击 / 填表单** |

playwright_mcp 跟 fetcher 类**不重叠**:fetcher 拿「已渲染内容」,playwright 拿「需交互才拿到的数据」(SPA / 登录后内容 / JS 渲染 / 表单提交后内容)。

## Why

T24 调研 50+ GitHub agent 能力层项目,唯一推荐推进的 P2 候选。

## How to apply

- 新能力判断标准:**这工具能不能补现有 fetcher 类的盲区**?
  - 静态/已渲染 → web_fetch 已覆盖
  - 反爬/JS 渲染 → jina / yt-dlp 已覆盖
  - GitHub / HN / Exa 特定场景 → 已有
  - **真浏览器交互(点击/填表单/登录态)→ playwright_mcp 是唯一选项**
- 子进程桥模型在 PrisirAI 已成模板(agent_reach_bridge / gh_bridge / hn_bridge / exa_bridge / playwright_bridge),复用 _JSONRPCClient + read thread + queue.Queue 模式。
- threading.Lock 必须只在一处加(不能让 caller 持锁又调 callee 持锁),否则死锁;或改用 RLock。

## 验证状态

- 23/23 tests(test_playwright_bridge 14 + test_playwright_endpoints 10)pass
- 60/60 verify checks pass(60 = 56 既有 + 4 new playwright check)

## 链

[[agent-tool-survey]] / [[agent-reach-upstream-decisions]] / [[prisIr-extension-roadmap-2026-09-20]]