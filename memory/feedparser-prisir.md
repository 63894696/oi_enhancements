---
name: feedparser-prisir
description: "PrisirAI 集成 kurtmckee/feedparser — RSS/Atom/JSON Feed fetcher,2026-09-26 ship P3j T21-A"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8770f801-490d-474f-9950-81a5d72bde7b
  modified: 2026-09-26T06:35:26.916Z
---

# PrisirAI × kurtmckee/feedparser 整合(P3j T21-A,2026-09-26 ship)

## 为什么做

Agent-Reach 上游工具有 feedparser(https://github.com/kurtmckee/feedparser)—
**PrisirAI 之前只能通过 agent-reach `read rss` 子进程间接调它**,没有直接集成。
跟 T20-I jina reader 一样的差距,只是这次目标更窄:RSS / Atom / JSON Feed 三类开放协议。

**核心优势**:
- 纯 Python 库,零 env 零配置
- 自带 HTTP client + ETag/Last-Modified 条件 GET(304 短路)
- 6.0.14 已支持 `timeout` kwarg

用户拍板:「请将其上游工具逐一集成」— T21-A 是 3 个真 gap 里的第一个
(其他两个:T21-B yt-dlp provider 化 / T21-C gh CLI 直接)。

## 集成方式(路径 A+C)

| 能力 | PrisirAI 原 | feedparser 接入 | 入口 |
|------|------------|----------------|------|
| `web_fetch.fetch(url)` | urllib(原始 XML/HTML) | **feedparser 第二顺位 picker**(对 .xml/.rss/.feed/.atom URL) | web_fetch 注册 feedparser fetcher |
| `web.feedparser.fetch` | 没意识 | 直接 endpoint + capability | endpoints.py / capability.py |
| `research()` pipeline | web_search + reach + jina | **step 2c** feedparser 插队(query 里直接出现 feed URL → 抓) | research.py |

## 模块/端点

- `prisir_work/web_fetch_feedparser.py` — ~190 行,2 公开 fn:`feedparser_fetch / feedparser_health`
- `prisir_work/endpoints.py:_web_feedparser_*` — 2 个 L0 端点(`/web/feedparser/{fetch,health}`)
- `prisir_work/capability.py:web.feedparser.{fetch,health}` — 2 个 capability
- `prisir_work/web_fetch.py:_FETCHERS` — 注册 feedparser,picker 第二顺位
- `prisir_work/research.py:_detect_feedparser_intent` + step 2c — query 含 feed URL → 优先抓

## 设计要点

- **URL 启发式判定**:含 `.xml / .rss / /feed / /atom / /rss / ?alt=rss /
  ?format=rss / ?output=rss / ?type=rss / /feed.json / /index.xml` 才动手
- **304 Not Modified**:返 `ok=False + error=not_modified`,上层 web_fetch
  缓存命中已覆盖
- **bozo=True 不致命**:XML 轻微错误仍可能给出 entries,
  `meta.bozo=True + meta.bozo_exception` 标记,content 正常返回
- **永远不 raise**:所有异常 → `{ok: False, error: ...}`
- **截断**:`max_items=50 + max_chars=50000` 双截断

## 与 Agent-Reach 互补

- **Agent-Reach `read rss`**:子进程 + agent-reach 0.x,适合"平台统一封装"
- **feedparser 直接**:库调用 + 零子进程,适合 PrisirAI 主对话/研究流水线

不替换:agent-reach 仍管 cookie / 平台登录 / 反爬;feedparser 只管 RSS 三类开放协议。

## 与 jina reader 对照

| 维度 | jina reader(T20-I) | feedparser(T21-A) |
|------|--------------------|-------------------|
| 安装 | Python 库 + 可选 hosted | 纯 Python 库 |
| 配置 | hosted_no_key / hosted_with_key / self_hosted | 零配置 |
| 覆盖 | 任意 URL → 干净 markdown | RSS / Atom / JSON Feed 三类 |
| 速率 | hosted_no_key 20 RPM 限流 | 无 |
| 优先 | picker 第一顺位 | picker 第二顺位(仅 feed 类 URL) |

## Why

User asked "前面提到多个平台接入未完成,而这个也是它作为能力层接入的上游工具
https://github.com/kurtmckee/feedparser,我们在PrisirAI中做了集成吗?请将其上游工具
逐一集成"。

盘点 14 项上游工具后,feedparser 是真 gap(纯 Python 库,直接可调);其余要么已做
(jina / yt-dlp 部分 / whisper),要么需鉴权延迟(twitter-cli / rdt-cli 等),
要么无价值(bs4 / lxml / ffmpeg)。**接进来**。

## How to apply

- 任何抓 RSS / Atom / JSON Feed URL 的场景,默认走 `web_fetch.fetch()`,它
  会优先选 feedparser(URL 启发式判定后)
- 想单独调 feedparser,用 `web.feedparser.fetch` endpoint
- 在 system prompt 看到 `web.feedparser.{fetch,health}` capability 标记,
  LLM 知道有 RSS / Atom / JSON Feed 通道

## 关键 commit

- T21-A(本批):feedparser fetcher + endpoint + capability + tests + verify + docs
- T21-B: yt-dlp provider 化(下批)
- T21-C: gh CLI 直接(下批)