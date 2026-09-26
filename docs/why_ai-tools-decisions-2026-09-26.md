# Agent-Reach 上游工具集成决策(2026-09-26,P3j T21 盘点结果)

> 2026-09-26 ship · 锁定 T21 边界 · 防止下次重复讨论

## Context

Agent-Reach (https://github.com/Panniantong/Agent-Reach) 是一个 14 平台「读+搜」
CLI 工具,内部依赖了多个 Python 库 + 系统 CLI + 第三方 CLI。

用户原话:「前面提到多个平台接入未完成,而这个也是它作为能力层接入的上游工具
https://github.com/kurtmckee/feedparser,我们在PrisirAI中做了集成吗?请将其上游工具
逐一集成」

P3j T21 启动后,我们盘点 14 项上游工具,逐项评估是否值得在 PrisirAI 里直接调用
(而不是只走 agent-reach 子进程间接调用)。

**核心权衡**:
- 直接调 = PrisirAI 拿到更细粒度的控制(超时 / 重试 / 错误码 / picker 优先),
  可以跟 jina / feedparser / yt-dlp 平级,被 web_fetch.fetch / web_search.search
  picker 自动选
- 不直接调 = 仍走 agent-reach 子进程,PrisirAI 当一层薄 wrapper

## 决策表(14 项)

### ✅ 已 ship(PrisirAI 已直接调)

| 工具 | 版本 | 接入方式 | 入口 | 何时 ship |
|------|------|----------|------|----------|
| **feedparser** | 6.0.14 | `prisir_work/web_fetch_feedparser.py` 独立 fetcher | picker 第二顺位(URL 启发式) + `web.feedparser.{fetch,health}` | P3j T21-A(2026-09-26) |
| **yt-dlp** | 2026.06.09 | `prisir_work/web_fetch_ytdlp.py` 通用 fetcher(200+ 网站) | picker 第三顺位 + `web.ytdlp.{meta,health}` | P3j T21-B(2026-09-26) |
| **gh CLI** | 2.96.0 | `prisir_work/gh_bridge.py` + `gh_api_provider.py` | 4 endpoints + 4 capabilities + `gh_api` fetcher + `gh_search` provider | P3j T21-C(2026-09-26) |
| **Jina Reader** | hosted / self_hosted | `prisir_work/web_fetch_jina.py` | picker 第一顺位 + `web.jina.{fetch,search,health}` | P3j T20-I + T20-I.2(2026-09-26) |
| **OpenAI Whisper** | via video_ext_creator | 视频字幕生成 | 主对话 / 视频 Tab | P3j T11(2026-09-25) |
| **playwright** | 已装 | 微信公众号视频 Tab 后端 | wechat-publisher 静态 | 已 ship(2026-08 前) |

### ⏸ 推迟(本次不接,但写明何时重新评估)

| 工具 | 推迟原因 | 何时重新评估 |
|------|---------|-------------|
| **Exa MCP** | 需 API key;免费层价值待评估;jina + gh + agent-reach 已够用 | 若用户提「需要更强语义搜索」或 Exa 接入 MCP 协议后 |
| **OpenCLI** | 不依赖;功能与 jina/feedparser 重叠 | 若 OpenCLI 加新平台覆盖(jina/feedparser 没的) |
| **rdt-cli / reddit-cli** | 需 cookie / 鉴权;与 agent-reach `read reddit` 没差异 | 若 reddit 改 API(免 cookie) |
| **twitter-cli** | 需鉴权;X API 付费墙;与 agent-reach `read twitter` 没差异 | 若 X API 改免费层 |
| **xhs-cli / bili-cli** | 需 cookie;专门走 agent-reach 已 ship;直接调难度大 | 若用户提「高频小红书/B站抓取」+ agent-reach 不稳 |
| **linkedin-mcp** | 鉴权复杂;使用频率低 | 若用户提「linkedin 关键人物监控」 |
| **mcporter** | 第三方工具管理,跟 PrisirAI 核心能力无关 | 不评估(无关) |
| **Node.js** | PrisirAI 主线 Python;Node 仅给扩展用 | 若 PrisirAI 全面切 Node 后端 |
| **xueqiu / xiaoyuzhou / boss** | 用户暂未提;agent-reach 没列 | 若用户明确要接雪球/小宇宙/Boss直聘 |

### ❌ 明确不做(PrisirAI 不需要)

| 工具 | 不做的原因 |
|------|----------|
| **bs4 / lxml** | jina markdown 已替代 HTML 解析;不直接调第三方 HTML parser |
| **aiohttp / httpx** | stdlib urllib + requests 已够;无异步需求 |
| **ffmpeg** | 视频转码不在研究范围;yt-dlp 内部已含 ffmpeg 集成 |
| **whisper** | ✅ 已 ship(见上方)— 不在不做表 |

## Why(决策依据)

### T21-A feedparser 选做的理由
- 纯 Python 库,零外部依赖
- 自带 ETag/Last-Modified 条件 GET,节省带宽
- RSS/Atom/JSON Feed 三种格式都解析,`bozo=True` 也能兜底
- PrisirAI 之前 RSS URL 走 jina(浪费配额),改 feedparser 后**释放 jina
  配额给高价值 URL**

### T21-B yt-dlp 选做的理由
- **已在 youtube_bridge 用**(1/200 能力),值得提升成通用 fetcher
- 200+ 网站支持(B站/微博/Twitter/Reddit/Vimeo/Niconico/TikTok),PrisirAI
  之前全靠 jina markdown
- 提取 metadata + 字幕语言列表(skip_download=True,不下载)
- youtube_bridge 不动(YouTube 上传仍依赖)

### T21-C gh CLI 选做的理由
- 系统 CLI 已装 + 已登录(`gh auth status` 返 logged_in)
- github.com URL 走 jina 经常 403 风控;gh api 直查结构化 JSON
- 不解析 HTML,免去 selector 维护成本
- public 仓库免 token,加 agent-reach 后还能继续覆盖 private 仓库

### Exa 推迟的理由
- 需 API key;免费层 1000 次/月,价值待评估
- jina 已有 hosted_no_key 20 RPM(覆盖大部分搜索需求)
- 若用户提「需要语义搜索」「跨语言研究」,再评估

### twitter/rdt/xhs/bili/linkedin CLI 推迟的理由
- 全部需要 cookie / 鉴权
- agent-reach 内部已处理 cookie / 鉴权 / 反爬,PrisirAI 直接调没优势
- 价值边际 = 0(与 agent-reach 等价)

### bs4 / lxml 不做的理由
- jina markdown 已是「干净的 HTML 提取」,无需再 HTML parser
- PrisirAI 主对话 / research 阶段拿的是 markdown,不是 raw HTML

### aiohttp / httpx 不做的理由
- stdlib urllib 已够(web_fetch 用了)
- 当前没有异步 I/O 性能瓶颈
- requests 也不依赖(简单 GET / POST 用 urllib)

### ffmpeg 不做的理由
- 视频转码不在 PrisirAI 范围
- yt-dlp 内部已集成 ffmpeg(需要时 yt-dlp 自动调)
- 用户在视频 Tab 走 webm/mp4 直传,不需要 ffmpeg 转码

## 后续何时重新评估

### 立即重新评估的触发器
1. 用户明确提「需要接 X 平台 / X 工具」(任何工具都可能重启)
2. jina / feedparser / yt-dlp / gh 实际生产遇到问题(权限 / 限流 / 精度)
3. 新平台 / 新工具涌现(MCP 协议工具 / Agent-Reach v2.x)

### 季度复盘建议(每 90 天)
1. 重新看 14 项里有没有新的「零成本接」机会
2. 重新看 Exa / OpenCLI 等推迟项的 API / 价格变化
3. 重新评估 picker 优先级(jina 在前是否还合理?)

## 关键 commit

- T21-A feedparser:`feat(p3j+t21-a/b): feedparser + yt-dlp 通用 fetcher 集成`(6bed7b0)
- T21-B yt-dlp:同上
- T21-C gh CLI:`feat(p3j+t21-c): gh CLI 直接整合 — 仓库元数据 + Issue/PR + 搜索`(dbb1781)

## 相关 memory / docs

- [memory/feedparser-prisir.md](memory/feedparser-prisir.md)
- [memory/ytdlp-prisir.md](memory/ytdlp-prisir.md)
- [memory/gh-prisir.md](memory/gh-prisir.md)
- [memory/agent-reach-upstream-decisions.md](memory/agent-reach-upstream-decisions.md) — 简短备忘
- [docs/feedparser-prisir-2026-09-26.md](docs/feedparser-prisir-2026-09-26.md)
- [docs/ytdlp-provider-prisir-2026-09-26.md](docs/ytdlp-provider-prisir-2026-09-26.md)
- [docs/gh-prisir-2026-09-26.md](docs/gh-prisir-2026-09-26.md)