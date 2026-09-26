# GitHub Agent 能力层项目调研(P3j T24,2026-09-26)

## Context

**用户原话**:
> 把 github 上 agent 能力层项目搜一遍,选一些高星项目来分析有什么我们可以直接接入,
> 有什么以类似 Easel 接入。

**目标**:扫 GitHub 上 agent 能力层 / 工具链 / 平台封装高星项目(>5k stars 或生态影响力大),
按以下 4 档决策:
1. **🟢 直接接入**(调上游库/API,跟 T21-A/B/C / T22-A/B 同模式)
2. **🟡 类似 Easel 接入**(子进程桥接第三方 CLI 平台,如 agent-reach)
3. **⚪ 推迟**(价值待定/等上游稳定/用户暂不需要)
4. **🔴 不做**(跟 PrisirAI 定位不符 / 已 ship 同类 / 违反产品边界)

## 调研覆盖范围

| 类别 | 代表项目 |
|------|---------|
| **Search API for Agents** | Tavily / Exa / SerpAPI / Brave Search / Perplexity |
| **Browser / Computer Use** | browser-use / Skyvern / Open Interpreter / LaVague / UI-TARS / web-eval-agent |
| **MCP Server 平台封装** | GitHub MCP / Playwright MCP / ChromeDevTools MCP / Blender MCP / AWS MCP |
| **Coding Agent(整 IDE)** | Cline / Continue / Aider / OpenHands / Goose / Devin |
| **多平台社媒/数据** | Agent-Reach / TikHub SDK / RedFox SDK / Easel / chinese-social-mcp / social-auto-upload |

---

## 🟢 直接接入候选(优先级排序)

按「对 PrisirAI 信息获取/创作的边际价值」排序。

### 1. **Tavily Python SDK** ⭐~1.4k · MIT · pip install tavily-python

**是什么**:AI 专用搜索 API,RAG/Agent 首选;提供 search / extract / crawl / qna_search / research 5 类方法。

**PrisirAI 直接接入价值**:**🔴 极低** — web_search.py 已 ship `tavily` provider(T20 早期);T22-A 加 Exa;T22-B 加 HN;现在已经有 8 个 provider 走 RRF 融合。**已 ship**。

**决策**:**已 ship**(不列入新增)。

### 2. **Playwright MCP** ⭐~18.5k · TypeScript

**是什么**:Microsoft 官方 Playwright MCP Server;让 LLM 通过 Playwright 操控浏览器(访问/点击/填表/截图)。

**PrisirAI 直接接入价值**:**🟡 中** — 我们没有「真浏览器交互」能力;若用户问「打开 XX 网站帮我点 Y」,目前只能 curl + parse HTML,部分 SPA 渲染不了。

**决策**:**⏸ 推迟到 M3.7**:
- 装 Playwright 需拉 Chromium(>200MB),跟「轻量化 install」原则冲突
- 现有 web_fetch_jina 已经能处理 80% 静态 + JS 渲染需求
- 若用户强烈需要「点按钮/填表单」,再 ship 单独 browser 子能力

### 3. **browser-use** ⭐~115k · MIT · `pip install browser-use`

**是什么**:GitHub 现象级项目,基于 DOM + vision 的浏览器 agent;LangChain 兼容。

**PrisirAI 直接接入价值**:**⏸ 推迟**:
- 已 ship 类似能力(web_fetch 体系)
- 安装成本高(需要 Chromium)
- 我们的「浏览器 agent」使用频率比编程 agent 低

**决策**:**⚪ 推迟**(优先级 P3)— 等用户强烈反馈需要时再 ship。

---

## 🟡 类似 Easel 接入候选

### 1. **Agent-Reach**(Panniantong/Agent-Reach)⭐ ~21k · Python CLI

**现状**:T20 已 ship 子进程桥。**当前只 ship rss + youtube 两个 channel**(README 列的 14 平台夸大)。

**用户拍板**:**🚫 不动**(已 ship,详见 [[agent-reach-upstream-decisions]])

### 2. **Easel**(ZJU-REAL/Easel)⭐(未公开统计)· Apache-2.0

**现状**:T10/T11/T12 已 ship 子进程桥(112 个 Skill,六平台发布 + 视频 + 数据回收)。

**用户拍板**:**🚫 不动**(已 ship,详见 [audit-direct-vs-indirect-2026-09-26.md](audit-direct-vs-indirect-2026-09-26.md))

### 3. **chinese-social-mcp**(zhaohongyuziranerran)· MIT · Python

**是什么**:MCP server 覆盖 6 大中国社交平台(小红书/抖音/公众号/微博/知乎/B站),10 个 tools(读/改/合规检查/热搜/发布/分析)。

**vs Easel**:
- Easel = 浙大团队的「运营工作台」(112 Skill,端到端)
- chinese-social-mcp = 「轻量 MCP 接口」(10 tools,接口清晰)

**PrisirAI 接入价值**:**⚪ 推迟**:
- Easel 已经覆盖 6 平台发布,功能重叠
- chinese-social-mcp 没有「比 Easel 强」的能力(`adapt_content`/`check_content` 我们可在 LLM 层做)
- 平台账号登录态共享是个难题(扫码 cookie 不通用)

**决策**:**⚪ 不做**(跟 Easel 重叠,价值不增)

### 4. **TikHub API Python SDK**(TikHub/TikHub-API-Python-SDK)· Python

**是什么**:统一 REST API 包装 16+ 平台(抖音/TikTok/小红书/B站/微博/快手/Instagram/YouTube/Twitter/LinkedIn/Reddit/Zhihu 等),1000+ endpoints,带 Captcha Solver。

**PrisirAI 接入价值**:**🔴 低**:
- TikHub 是付费服务(API 调用计费)
- 用户原话「接有 value 平台」原则下,TikHub 跟我们「零成本开放接入」原则冲突
- 我们已 ship yt-dlp / feedparser / Exa / HN,公开数据源已够用

**决策**:**🔴 不做**(付费服务,跟产品边界不符)

### 5. **social-auto-upload**(dreammis/social-auto-upload)⭐ ~15k · Python

**是什么**:自动上传视频到抖音/小红书/视频号/TikTok/YouTube/B站 6 平台。

**vs Easel 视频处理能力**:
- Easel 已有 video_creator + publish 自动链路(T11-E)
- social-auto-upload 是「单点上传」,没有编排能力

**PrisirAI 接入价值**:**⚪ 推迟**:
- Easel bili_upload.py 已 ship(T10-D)
- 小红书/视频号/抖音 上传 Easel 已有 skill

**决策**:**⚪ 不做**(跟 Easel 重叠)

---

## ⚪ 推迟 / 不做

### 整 IDE Coding Agent(Cline/Continue/Aider/OpenHands/Goose)

**决策**:**🔴 不做**(产品边界):
- 这些是 IDE 内的编程 agent,我们 PrisirAI 是「主对话 Agent」+ 已有 research / web_fetch 链
- 我们的 code 能力靠 LLM 直接生成(PrisirAI 主对话里写代码已 ship,见 [[prisIr-agent-natural-video]] 同系列架构)
- 「接管 IDE」跟我们的「Web 端壳 + 主对话」定位不符

### 截图 / 视觉工具(agent-screenshot / vision-skills / capture-screen)

**决策**:**⚪ 推迟**:
- 我们目前没有「电脑屏幕截图给 LLM」的需求
- 若未来需要 OCR 桌面应用,再 ship
- vision_tools.py 已有基础(看 mcp_prisiragent_server/vision_tools.py)

### 整 MCP server 平台封装(Blender MCP / AWS MCP / Notion MCP / Kubernetes MCP)

**决策**:**⚪ 推迟**:
- Blender 3D 建模跟 PrisirAI 不搭
- AWS / K8s 跟「个人 AI 助手」边界不符(我们是本地工具)
- Notion MCP 可能有 value(用户记笔记),但 Notion API 我们可直接调(无需 MCP 中间层)

### 整 search API(SerpAPI / Brave / Perplexity / Kagi)

**决策**:**⚪ 推迟 / 部分 ship**:
- SerpAPI 我们 web_search.py 已 ship(serper 走的是类似 SerpAPI 的代理)
- Brave Search API 是 web_fetch 兜底候选(可考虑替换部分搜索)
- Perplexity / Kagi 是消费产品,API 暂未公开 agent 接入

### RedFox SDK(付费)· chinese-social-mcp · OpenBiliClaw · creatorhub

**决策**:**🔴 不做**(付费 / 重叠 / 价值不明确)

---

## 决策汇总表

| 项目 | stars | 类型 | 决策 | 优先级 |
|------|-------|------|------|------|
| Tavily | ~1.4k | search API | 🟢 已 ship | — |
| **Playwright MCP** | ~18.5k | browser MCP | ⏸ **推迟到 M3.7** | P2 |
| **browser-use** | ~115k | browser agent | ⏸ 推迟 | P3 |
| Agent-Reach | ~21k | 多平台 CLI | 🟡 已 ship(Easel-like) | — |
| Easel | — | 多平台运营 | 🟡 已 ship(Easel-like) | — |
| chinese-social-mcp | — | 6 平台 MCP | 🔴 不做(重叠 Easel) | — |
| TikHub SDK | — | 付费多平台 SDK | 🔴 不做(付费) | — |
| social-auto-upload | ~15k | 6 平台上传 | 🔴 不做(重叠 Easel) | — |
| Cline/Continue/Aider/OpenHands | 17-62k | 整 IDE Coding Agent | 🔴 不做(产品边界) | — |
| agent-screenshot / vision-skills | 1-1.4k | 截图能力 | ⏸ 推迟 | P3 |
| Blender/AWS/Notion/K8s MCP | 5-13k | 整 MCP 平台 | ⏸ 推迟 / 不做 | — |
| SerpAPI / Brave / Perplexity / Kagi | — | search API | ⚪ 部分 / 推迟 | — |

## 给未来 T25+ 留 hook

- 「M3.7 Playwright 桥接」任务模板:web_fetch 增强,加 /web/playwright/* 端点
- 「browser-use 桥接」任务模板:整 LLM 浏览器 agent,优先级 P3

## Critical Files

**新建**
- [docs/agent-tool-survey-2026-09-26.md](docs/agent-tool-survey-2026-09-26.md)
- [memory/agent-tool-survey.md](memory/agent-tool-survey.md)

**修改**
- [memory/MEMORY.md](memory/MEMORY.md) — index 加 1 行

**不动**
- 无代码改动(纯调研)

## Verification

不需要测试(无代码改动)。仅:
```bash
# 1. 跑 verify 确认无回归
python verify_wechat_publisher.py -SkipHttp
```

## Risks

无 — 这是调研决策文档,不动代码。若用户做错决策,可随时重新评估。

---

# 数据来源

- [browser-use 主页](https://browser-use.com/)
- [browser-use 评估](https://swarm.beetlix.com/reviews/browser-use-review-2026)
- [Agent-Reach](https://github.com/Panniantong/Agent-Reach)
- [TikHub SDK](https://github.com/TikHub/TikHub-API-Python-SDK)
- [chinese-social-mcp](https://glama.ai/mcp/servers/zhaohongyuziranerran/chinese-social-mcp)
- [Easel 简介](https://blog.mushroom.cv/blog/easel-open-source-social-media-agent-workbench)
- [MaxHub API Skills](https://github.com/XieWxx/maxhub-api-skills)
- [Tavily Python](https://github.com/tavily-ai/tavily-python)
- [Microsoft Playwright MCP](https://github.com/microsoft/playwright-mcp)
- [GitHub Official MCP](https://github.com/github/github-mcp-server)
- [Skyvern](https://github.com/Skyvern-AI/skyvern)
- [LaVague](https://github.com/lavague-ai/LaVague)
- [UI-TARS](https://github.com/bytedance/UI-TARS)
- [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter)
- [web-eval-agent](https://mcpmarket.com/server/web-eval-agent-1)
- [Cline](https://github.com/cline/cline)
- [Continue](https://github.com/continuedev/continue)
- [Aider](https://github.com/Aider-AI/aider)
- [OpenHands](https://github.com/All-Hands-AI/OpenHands)
- [social-auto-upload](https://github.com/dreammis/social-auto-upload)
- [vision-skills](https://claudeatlas.com/skills/Anionex/vision-skills)
- [agent-screenshot](https://github.com/rodbland2021/agent-screenshot)