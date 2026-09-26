# agent-browser × PrisirAI 整合设计(2026-09-26 ship, P3j T26)

> 状态:**shipped**
> 标签:P3j T26 / vercel-labs/agent-browser / token 高效浏览器
> 跟 T25 Playwright MCP 互补:**T26 主线(token 高效)**;**T25 备用(长连常驻)**

---

## 1. 背景

T25 ship 了 Playwright MCP(子进程 JSON-RPC + Chromium,7 高层 API),但在批量自动化
场景下每次 `snapshot` 返 **全 a11y 树** + **ref 每次重新标号**,15 步跨页交互消耗
~114k tokens,且 LLM 拿 snapshot 后只能「本次有效」地引用 ref,跨步要重新对齐。

**vercel-labs/agent-browser**(43.2k stars,Apache-2.0,Vercel Labs 出品)解决了这两个痛点:

| 维度 | 优势 |
|------|------|
| **Refs 系统** | `@e1/@e2/...` 跨 snapshot **稳定**(不动 DOM 就一直有效)— LLM 可持久引用 |
| **Token 削减** | snapshot 只返 refs + 必要属性,**~93% token 削减** vs playwright-mcp |
| **Native Rust** | 预编译二进制 + npm 包,无需 Rust 工具链(`npm install -g agent-browser`) |
| **依赖** | Chrome for Testing(~150MB,`agent-browser install` 自动下载) |

---

## 2. 架构决策

### 2.1 协议选择(跟 T25 完全不同)

| | T25 Playwright MCP | T26 agent-browser |
|--|--|--|
| 协议 | **stdio JSON-RPC 长连**(Node.js MCP server) | **batch subprocess CLI** + Rust daemon IPC |
| 常驻 | 是(子进程常驻,daemon thread + queue.Queue) | **否**(每次 subprocess.run,Rust daemon 自动长驻) |
| Bridge 复杂度 | 高(`_JSONRPCClient` + `threading.Lock` + read loop) | **低**(跟 `gh_bridge._run` 同模式,单 subprocess.run) |
| 进程模型 | Python 主进程 ↔ Node 子进程(stdin/stdout) | Python 主进程 ↔ Rust daemon(bash CLI) |

**选 batch subprocess CLI 的原因**:agent-browser 自带 Rust daemon(首次调用时
启动),后续每个 subprocess.run 走 daemon IPC 不重 Chromium,启动开销 <100ms。

### 2.2 Refs 系统(@eN 跨 snapshot 稳定)

```bash
# Step 1: 取 snapshot
agent-browser snapshot -d 3 --json
# → {"snapshot": "@e1 [button] 登录\n@e2 [textbox] 邮箱\n@e3 [textbox] 密码"}

# Step 2: LLM 拿到 @e2,跨多步交互一直可用(只要 DOM 不变)
agent-browser click @e1     # 击登录(其实可以不要 — 只是示意)
agent-browser fill @e2 "test@example.com"
agent-browser fill @e3 "secret123"
agent-browser click @e1     # 真点登录按钮
```

**对比 T25**:playwright-mcp 的 `browser_snapshot` 每次都返新 ref 编号,LLM 跨步引用
要重新对齐,容易错。

### 2.3 风险等级(对齐 T25)

| 操作 | 风险 | 前端 UX |
|------|------|---------|
| `ab_open / ab_snapshot / ab_eval / ab_screenshot / ab_close` | **L0 只读** | 直通 |
| `ab_click / ab_fill` | **L1 副作用** | 前端弹确认卡 |

### 2.4 健康模式(3 档)

| mode | 触发条件 | 前端建议 |
|------|---------|---------|
| `missing_cli` | `agent-browser` 不在 PATH | 跳 npm 安装 |
| `not_installed` | bin 在但 `doctor` 失败(Chrome for Testing 未下载) | 跑 `agent-browser install` |
| `ready` | 全 OK | 可用 |

---

## 3. 实现概览

### 3.1 新建模块

- [`prisir_work/agent_browser_bridge.py`](../prisir_work/agent_browser_bridge.py) ~250 行
  - 8 公开 API:`ab_health / ab_open / ab_snapshot / ab_click / ab_fill / ab_eval / ab_screenshot / ab_close`
  - 内部 `_run(args, *, timeout, need_json)` 复用 `gh_bridge._run` 模式
  - `_extract_refs(snapshot)` 正则 `@e(\d+)` 抽 refs,去重保序
  - 验证 `@ref` 格式 `^@e\d+$`(不匹配直接 `bad_ref`,不走 subprocess)

### 3.2 端点(8 个)

| path | method | risk | 用途 |
|------|--------|------|------|
| `/web/agent-browser/health` | POST | L0 | 探活(3 档 mode) |
| `/web/agent-browser/open` | POST | L0 | 打开 URL |
| `/web/agent-browser/snapshot` | POST | L0 | 取 a11y 树 + refs |
| `/web/agent-browser/click` | POST | **L1** | 点 @ref |
| `/web/agent-browser/fill` | POST | **L1** | 填 @ref |
| `/web/agent-browser/eval` | POST | L0 | 跑 JS |
| `/web/agent-browser/screenshot` | POST | L0 | 截图 |
| `/web/agent-browser/close` | POST | L0 | 关浏览器 |

### 3.3 能力(8 个 `web.agent-browser.*`)

跟 T25 完全镜像的命名空间。LLM 主对话通过 [[EXEC: web.agent-browser.snapshot]]
等标记调用,前端按 risk 渲染确认卡(L1 弹卡,L0 直通)。

### 3.4 错误翻译(6 条 `ab_*`)

| 错误 | 翻译 | link |
|------|------|------|
| `ab_cli_not_found` | "agent-browser 未装(跑 `npm install -g agent-browser && agent-browser install`)" | npm |
| `ab_install_failed` | "Chrome for Testing 安装失败(检查 npm 镜像 / 网络;约 150MB)" | /extensions |
| `ab_daemon_failed` | "agent-browser Rust daemon 启动失败(可能端口占用或权限问题)" | /extensions |
| `ab_timeout` | "agent-browser 操作 30s 超时(网络慢或页面重,可重试)" | /extensions |
| `ab_invalid_ref` | "@ref 无效(DOM 已变或未 snapshot;重 snapshot 拿最新 refs)" | — |
| `ab_unsupported_engine` | "agent-browser 不支持该浏览器引擎(默认 chrome-for-testing)" | /extensions |

---

## 4. 跟 T25 Playwright MCP 互补关系

| 用例 | 推荐 |
|------|------|
| 多步交互 + 稳定 refs + token 敏感 | **T26 agent-browser**(默认) |
| 长连会话 / 实时 LLM 自拼步骤 / 跨页调试 | T25 Playwright MCP |
| 仅抓静态 HTML | web_fetch(urllib) |
| 防反爬 / JS 渲染抓取 | web_fetch_jina |
| GitHub 元数据 | gh_bridge |
| RSS 抓 | feedparser fetcher |
| YouTube 字幕 | yt-dlp fetcher |

**两个 ship 并存**:LLM 可在「细粒度控制 + 抗抖动」两条路径间选;前端按
capability 名(`web.agent-browser.*` vs `web.playwright.*`)清晰区分。

---

## 5. E2E 验证

### 5.1 单元测试

```bash
python -m pytest tests/test_agent_browser_bridge.py tests/test_agent_browser_endpoints.py -v
# 期望:18 + 16 = 34/34 绿
```

### 5.2 verify 全套

```bash
python verify_wechat_publisher.py -SkipHttp
# 期望:64/64(60 既有 + 4 new ab check)
```

### 5.3 真机 E2E(需 npm + agent-browser)

```bash
# 装 agent-browser
npm install -g agent-browser
agent-browser install  # 下载 Chrome for Testing

# 探活
python -c "from prisir_work import agent_browser_bridge as ab; print(ab.ab_health())"
# → {"mode": "ready", ...}

# 主对话 E2E:
# 「打开 https://news.ycombinator.com 告诉我前 3 条标题」
# → [[EXEC: web.agent-browser.open url="..."]]
# → [[EXEC: web.agent-browser.snapshot]]
# → a11y 树(@eN refs)
# → LLM 解析 + 反馈

# 真填表单:
# 「打开 example.com/login 填 username=test」
# → [[EXEC: web.agent-browser.open url="..."]]
# → [[EXEC: web.agent-browser.snapshot]]
# → 前端弹 L1 确认卡(填 username=test @e2)
# → 用户确认 → [[EXEC: web.agent-browser.fill ref="@e2" text="test"]]
```

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| agent-browser 未装 / npm 失败 | `ab_health mode=missing_cli` + 前端 npm 安装提示 |
| Chrome for Testing 下载失败(~150MB) | `mode=not_installed` + 跑 `agent-browser install` |
| Refs 失效(DOM 变) | `ab_invalid_ref` → 提示重 snapshot |
| LLM 不输 `web.agent-browser.*` 标记 | system prompt 已加;verify 检查 8 capability 注册 |
| Rust daemon 卡死 | `ab_close` 兜底;下次 call 自动重启 |
| 跟 playwright-mcp capability id 撞 | 命名空间分开:`web.agent-browser.*` vs `web.playwright.*` |

---

## 7. Rollback

- 删除 `prisir_work/agent_browser_bridge.py`
- `prisir_work/endpoints.py` 删 8 行 `@register`
- `prisir_work/capability.py` 删 8 行 `register_capability`
- `prisir_work/agent_main_chat_hook.py` 删 6 行翻译
- 删除 `tests/test_agent_browser_*.py`
- `verify_wechat_publisher.py` 删 4 check + 4 CHECKS 行

---

## 8. 何时重新评估

- agent-browser 出 `batch` 模式稳定 API → T26+1 加 `ab_batch(commands)` 跨步省 token
- Chrome for Testing 改 chromium-only → 调整 hint
- agent-browser 出 v2 协议(Rust → Go 等)→ 评估迁移成本
- LLM 开始吃视觉(多模态成熟)→ ab_screenshot 转 vision path

---

## 9. 跨能力边界一览(决策表)

| 信息源 | 何时用 |
|--------|-------|
| web_fetch | 静态 HTML,免 key |
| web_fetch_jina | 防反爬 / 全文搜索 |
| gh_bridge | GitHub 元数据/issue/PR/搜索 |
| feedparser | RSS/Atom |
| yt-dlp | 200+ 网站 metadata + 字幕 |
| exa | 语义搜索(需 EXA_API_KEY) |
| hackernews | HN 帖子 |
| agent-reach | 14 平台读/搜 |
| **agent-browser(T26)** | **多步交互 + 稳定 refs** |
| playwright-mcp(T25) | 长连会话 |
| video creators | 一键出片 / 视频处理 |
| music | 音乐能力 |
| publisher | 多平台发布 |

---

## 10. 数据流(主对话「帮我打开 V2EX 看第 1 楼」)

```
用户输入
  ↓ LLM 解析
  [[EXEC: web.agent-browser.open url="https://v2ex.com"]]
  ↓ endpoints._web_ab_open
  ↓ ab_bridge.ab_open
  ↓ subprocess.run(["agent-browser", "open", "https://v2ex.com", "--json"])
  ↓ Rust daemon 接 IPC → 启 Chrome → 打开 → 返 {"ok": true}
  ↓ build_exec_result → ws 事件 capability_exec_result
  ↓ 前端显示 ✓
  ↓ LLM 继续
  [[EXEC: web.agent-browser.snapshot]]
  ↓ ab_bridge.ab_snapshot
  ↓ subprocess.run(["agent-browser", "snapshot", "-d", "3", "--json"])
  ↓ 返 {tree: "@e1 ...", refs: ["@e1", "@e2", ...]}
  ↓ LLM 解析 + 提取第 1 楼 → 反馈用户

[变体] 用户说「帮我填这个表单 username=test」
  ↓ [[EXEC: web.agent-browser.open url="..."]]
  ↓ [[EXEC: web.agent-browser.snapshot]] → 拿 ref
  ↓ 前端弹 L1 确认卡(填 username=test @e2)
  ↓ 用户确认 → [[EXEC: web.agent-browser.fill ref="@e2" text="test"]]
  ↓ subprocess.run(["agent-browser", "fill", "@e2", "test", "--json"])
  ↓ 浏览器填入 → {ok: true}
  ↓ 前端显示 ✓
```

---

## 11. Commit 信息(2026-09-26)

- `feat(p3j t26): vercel-labs/agent-browser 接入(token 高效浏览器,Rust CLI 子进程桥)`
- 共 10 文件,+~900 行
- 18 bridge tests + 16 endpoint tests = 34/34 绿
- verify 全套 64/64 绿(60 既有 + 4 new ab check)