# PrisirAI × Agent-Reach 14 平台集成(P3j T20,2026-09-26 ship)

> Agent-Reach(Panniantong/Agent-Reach)是 GitHub 上 14 平台「读+搜」CLI 工具,
> 无需 API key 即可读写小红书 / B站字幕 / GitHub / V2EX / YouTube字幕 / RSS 等。
> 本次集成让 PrisirAI 主对话能直接调用这些平台,补齐 web_search/web_fetch
> 的覆盖盲区。

## 用户决策(5 项)

1. **路径 C** — 自写 web_search/web_fetch 基础保留 + Agent-Reach 作**子进程桥**
   接入(不替换现有)
2. **14 平台全覆盖** — 小红书 / B站字幕 / GitHub / V2EX / YouTube字幕 / RSS /
   B站搜索 / 微博 / 知乎 / Exa / Jina / Twitter / Reddit / LinkedIn
3. **入口** — wechat-publisher 「🧩 扩展」Tab(沿用现有 extensions/UI 范式)
4. **必须装** — Agent-Reach 不是可选 fallback,用户原话「为什么不正接装上呢」
5. **P0 6 平台默认开** — xhs / bilibili-subtitle / github / v2ex /
   youtube-subtitle / rss;其余默认关,UI toggle
6. **3 档状态灯** — ✅/⚠/❌(agent-reach 安装 + 各平台健康)

## 架构总览

```
┌────────────────────────────────────────────────────────────┐
│ 主对话(Web)                                                 │
│  "看看 B站视频说什么 https://www.bilibili.com/video/BV1"  │
└────────────────────────┬───────────────────────────────────┘
                         ↓ LLM 输出 EXEC 标记
┌────────────────────────────────────────────────────────────┐
│ agent_main_chat_hook.scan_and_exec()                        │
│  → [[EXEC: web.reach.read platform="bilibili-subtitle"     │
│        url="https://..."]]                                  │
└────────────────────────┬───────────────────────────────────┘
                         ↓ 调 endpoints._web_reach_read
┌────────────────────────────────────────────────────────────┐
│ agent_reach_bridge.read(platform, url)                      │
│  → subprocess.run(["agent-reach", "read",                   │
│                    "bilibili-subtitle", url, "--json"])    │
└────────────────────────┬───────────────────────────────────┘
                         ↓ JSON stdout
┌────────────────────────────────────────────────────────────┐
│ 前端 ws event: capability_exec_result                       │
│  {ok: True, content: "<字幕>", title: "..."}               │
│  ↓                                                         │
│  renderCapExecResult → 显示字幕 + 「📖 来源:B站」          │
└────────────────────────────────────────────────────────────┘
```

## 文件清单

| 类型 | 文件 | 内容 |
|------|------|------|
| 新增 | `prisir_work/agent_reach_bridge.py` | 子进程桥(doctor/read/search/platforms),~150 行 |
| 新增 | `tests/test_agent_reach_bridge.py` | 10 个 mock subprocess 测试 |
| 新增 | `tests/test_agent_reach_endpoints.py` | 6 个端点 + capability 测试 |
| 修改 | `prisir_work/endpoints.py` (+60) | 4 L0 端点 `/web/reach/{doctor,read,search,platforms}` |
| 修改 | `prisir_work/capability.py` (+30) | 4 L0 capability,中文 kw |
| 修改 | `prisir_work/research.py` (+~80) | `_detect_reach_intent` + research merge |
| 修改 | `prisir_work/agent_main_chat_hook.py` (+14) | 4 条 reach 错误翻译,最长前缀优先 |
| 修改 | `companion/prisIragent-wechat-publisher/static/index.html` (+~250) | 扩展 Tab + 14 平台 grid + 3 档灯 + toggle + 测试按钮 |
| 修改 | `verify_wechat_publisher.py` (+~150) | 4 个 verify check:bridge / endpoint / DOM / CSS |

## 设计要点

### 1. 子进程桥(`agent_reach_bridge.py`)

**统一异常处理**(永 raise 给上层):
- `FileNotFoundError`(agent-reach 未装)→ `{ok: False, installed: False, hint: "pip install agent-reach"}`
- `subprocess.TimeoutExpired`(超时)→ `{ok: False, error: "agent_reach_timeout", hint: "超时 30s"}`
- `returncode != 0`(子进程失败)→ `{ok: False, error: "agent_reach_failed", returncode, stderr[-300:]}`
- `JSONDecodeError`(stdout 非 JSON)→ `{ok: True, data: {raw: stdout}}`(兜底返原文)

**静态 14 平台目录**(与安装状态无关,UI 总能渲染):

```python
PLATFORMS = [
    {"id": "xhs",               "title": "小红书",      "category": "cn_social"},
    {"id": "bilibili-subtitle", "title": "B站字幕",     "category": "cn_video"},
    {"id": "github",            "title": "GitHub",      "category": "dev"},
    {"id": "v2ex",              "title": "V2EX",        "category": "cn_tech"},
    {"id": "youtube-subtitle",  "title": "YouTube 字幕", "category": "global_video"},
    {"id": "rss",               "title": "RSS 通用",     "category": "feed"},
    {"id": "bilibili-search",   "title": "B站搜索",      "category": "cn_video"},
    {"id": "weibo",             "title": "微博",         "category": "cn_social"},
    {"id": "zhihu",             "title": "知乎",         "category": "cn_qa"},
    {"id": "exa",               "title": "Exa 搜索",     "category": "search"},
    {"id": "jina",              "title": "Jina Reader",  "category": "read"},
    {"id": "twitter",           "title": "Twitter/X",   "category": "global_social"},
    {"id": "reddit",            "title": "Reddit",      "category": "global_social"},
    {"id": "linkedin",          "title": "LinkedIn",    "category": "career"},
]

P0_PLATFORMS = {"xhs", "bilibili-subtitle", "github", "v2ex",
                "youtube-subtitle", "rss"}
```

### 2. 端点 + capability(`endpoints.py` + `capability.py`)

**4 端点**(全 L0 只读):

| 路径 | 方法 | 入参 | 出参 |
|------|------|------|------|
| `/web/reach/doctor` | POST | `{}` | `{ok, installed, version, bin, platforms[]}` |
| `/web/reach/read` | POST | `{platform, url, timeout?}` | `{ok, content, title, meta}` |
| `/web/reach/search` | POST | `{platform, query, limit?, timeout?}` | `{ok, results[], sources[]}` |
| `/web/reach/platforms` | POST | `{}` | `{ok, platforms[]}` |

**4 capability**(主对话 LLM 触发):

| ID | 中文 kw | 英文 kw | 风险 |
|----|---------|---------|------|
| `web.reach.doctor` | reach doctor, 信息源健康 | agent-reach | L0 |
| `web.reach.read` | 读小红书, 看视频字幕, 看 GitHub, 看 V2EX, 看 RSS | read post, xhs | L0 |
| `web.reach.search` | 搜小红书, 搜 B站, 搜 V2EX, 搜 RSS | xhs search, bilibili search | L0 |
| `web.reach.platforms` | reach 平台, 信息源列表 | agent-reach platforms | L0 |

### 3. 扩展 Tab UI(`companion/prisIragent-wechat-publisher/static/index.html`)

**布局**:

```
┌─ 🧩 扩展 ──────────────────────────────────────────────┐
│ ┌─────────────────────────────────────────────────┐   │
│ │ ✅ agent-reach v0.x.x · 12/14 平台就绪          │   │  ← reach-banner (ok/warn/err)
│ │ [🔬 重新检测]  [📖 安装指引]                     │   │
│ └─────────────────────────────────────────────────┘   │
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐           │
│ │ ●小红书│ │ ●B站字幕│ │ ●GitHub│ │ ●V2EX │  ← reach-card
│ │ P0   ⏹🔬│ │ P0   ⏹🔬│ │ P0   ⏹🔬│ │ P0   ⏹🔬│
│ └────────┘ └────────┘ └────────┘ └────────┘           │
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐           │
│ │●YouTube│ │ ●RSS  │ │ ●微博  │ │ ●知乎  │           │
│ │ P0   ⏹🔬│ │ P0   ⏹🔬│ │     ⏹🔬│ │     ⏹🔬│
│ └────────┘ └────────┘ └────────┘ └────────┘           │
│ ...(共 14 张)                                          │
└────────────────────────────────────────────────────────┘
```

**3 档状态灯**:

```css
.reach-light.ok      { background: #4caf50; }  /* ✅ 绿 */
.reach-light.warn    { background: #ff9800; }  /* ⚠ 橙 */
.reach-light.err     { background: #f44336; }  /* ❌ 红 */
.reach-light.unknown { background: #9e9e9e; }  /* 灰 */
```

**P0 卡片左边框**:

```css
.reach-card.p0 { border-left: 4px solid #4d6b5b; }
```

**Toggle 开关**(自定义,不用外部库):

```css
.reach-toggle {
  appearance: none; width: 36px; height: 20px;
  border-radius: 10px; background: #ccc; position: relative;
  cursor: pointer; transition: background 0.2s;
}
.reach-toggle:checked { background: #4d6b5b; }
.reach-toggle::after {
  content: ''; position: absolute;
  width: 16px; height: 16px; border-radius: 50%;
  background: white; top: 2px; left: 2px;
  transition: left 0.2s;
}
.reach-toggle:checked::after { left: 18px; }
```

**localStorage 持久化 toggle**:

```js
function saveReachEnabled() {
  const enabled = {};
  document.querySelectorAll('.reach-toggle').forEach(t => {
    enabled[t.dataset.pid] = t.checked;
  });
  localStorage.setItem('reach_enabled', JSON.stringify(enabled));
}
```

**测试 URL 静态表**(每个 P0 平台给一条可测 URL):

```js
const REACH_TEST_URLS = {
  'xhs':               'https://www.xiaohongshu.com/explore/test',
  'bilibili-subtitle': 'https://www.bilibili.com/video/BV1xx411c7mD',
  'github':            'https://github.com/anthropics/anthropic-sdk-python',
  'v2ex':              'https://v2ex.com/?tab=hot',
  'youtube-subtitle':  'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
  'rss':               'https://news.ycombinator.com/rss',
  // 其他 8 平台空(暂未提供测试链接)
};
```

### 4. research 集成(`research.py`)

**`_detect_reach_intent(query)`** — 单次研究只取首个命中:

```python
_REACH_RULES = [
    ("小红书", "xhs"),
    ("xhs", "xhs"),
    ("b站", "bilibili-search"),
    ("bilibili", "bilibili-search"),
    ("v2ex", "v2ex"),
    ("github", "github"),
    ("youtube", "youtube-subtitle"),
    ("字幕", "bilibili-subtitle"),
    ("rss", "rss"),
]
```

**merge 顺序**:reach 优先(reach_results 先入 seen_urls),后到的 web_search 同 URL 被去重。

### 5. 错误翻译(`agent_main_chat_hook.py`)

**4 条 reach 错误**(插在 `_TRANSLATIONS` 表前面,最长前缀优先):

```python
("agent_reach_not_installed",
 "Agent-Reach 未安装(信息源覆盖不全,装上后才能读小红书/B站字幕等)",
 "/extensions"),
("reach_unknown_platform",
 "Agent-Reach 不支持此平台(看 🧩 扩展 列出的 14 个)",
 "/extensions"),
("reach_platform_disabled",
 "该信息源在 🧩 扩展 面板已关闭,去重新打开",
 "/extensions"),
("agent_reach_timeout",
 "Agent-Reach 子进程超时(网络或平台限速,可重试)",
 "/extensions"),
```

最长前缀匹配:`agent_reach_not_installed`(26 chars) > `agent_reach_failed`(20)
> `agent_reach`(11)。避免短串误吞。

## 数据流

### 主对话「看看 B站视频说什么」

```
用户输入 → LLM 输出
  [[EXEC: web.reach.read platform="bilibili-subtitle" url="https://..."]]
  ↓ scan_and_exec (T16-A)
  endpoints._web_reach_read({platform, url})
  ↓ agent_reach_bridge.read("bilibili-subtitle", url)
  subprocess.run(["agent-reach", "read", "bilibili-subtitle", url, "--json"])
  ↓ {content: "<字幕内容>", title: "B站视频标题", meta: {}}
  build_exec_result(ok=True, result={content, title})
  ↓ ws event capability_exec_result
  前端 renderCapExecResult → 显示字幕 + 来源:B站
```

### 主对话「小红书 PrisirAI 怎么评价」(走 research 路径)

```
research("小红书 PrisirAI 怎么评价")
  ↓ plan_queries
  ↓ _detect_reach_intent("小红书 PrisirAI 怎么评价")
  → ("xhs", "PrisirAI 怎么评价")
  ↓ _reach_search_one("xhs", "PrisirAI 怎么评价", limit=5)
  → agent-reach search xhs "PrisirAI 怎么评价" --limit 5 --json
  → [{url, title, snippet}] × 5
  ↓ merge reach + web_search(URL 去重,reach 优先)
  ↓ fetch top URLs + LLM 合成
  ↓ 综述返回
```

### 用户点 🧩 扩展 Tab

```
refreshReachDoctor()
  GET /web/reach/doctor → {installed, version, platforms[]}
  GET /web/reach/platforms → [{id, title, category, default_on, p0}] × 14
  ↓
  banner 4 状态:
    ✅ v0.x.x · N/14 平台就绪
    ⚠ 已装但 doctor 异常
    ❌ 未安装(显示「pip install agent-reach」+ 安装指引按钮)
  renderReachGrid 14 张卡片
```

## 易踩坑

- **`shutil.which()` 在 Windows 找 agent-reach**:若 pip 装到其他 Python
  环境会返 None,doctor() 必须先校验 bin 再调 subprocess.run
- **`timeout=30` 太宽**:网络不稳时单次研究能拖 2-3 分钟,前端可显示
  「⏳ reach 子任务…」进度
- **reach.search 关键词误命中**:"xhs 文件夹"不是小红书 — 当前不做语义
  消歧,但取了「首个命中 + 限 query 长度」降低误触概率
- **agent-reach 依赖平台(playwright/youtube-dl)缺失**:doctor 返回 detail
  字段,UI 卡片展示,用户手动装(不强制)
- **extension Tab 没绑 tab 切换事件**:必须在 tab 切换到 extensions 时自动
  refreshReachDoctor(),否则用户看不到当前状态
- **localStorage toggle 状态不同步**:必须每次 toggle 后立即 saveReachEnabled,
  否则刷新页面 toggle 状态丢失
- **测试 URL 表更新**:每加一个 P0 平台必须加一条测试 URL,否则 🔬 测试按钮
  提示「暂未提供测试链接」

## 验证

### 单元测试

```bash
python tests/test_agent_reach_bridge.py        # 10/10 绿(mock subprocess)
python tests/test_agent_reach_endpoints.py     # 6/6 绿(mock bridge)
```

### verify 自检

```bash
python verify_wechat_publisher.py -SkipHttp    # 37/37 全绿
# +4 T20 check:bridge 模块 + endpoint/UI DOM/CSS
```

### E2E 真跑(待 T20-H)

```bash
pip install agent-reach
agent-reach doctor
python prisiragent-companion-web.py
# 浏览器:http://localhost:18899 → 🧩 扩展 Tab
# 主对话:"看看 B站视频说什么 https://www.bilibili.com/video/BV1xx411c7mD"
# → 自动调 web.reach.read → 字幕反馈
```

## 后续

- T20-G:INSTALL.md 加 agent-reach 安装段
- T20-H:真装 agent-reach + E2E 主对话真跑

## Commit

`feat(p3j+t20): Agent-Reach 14 平台接入`(T20-A/B/C/D/E/F 全 ship)