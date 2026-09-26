---
name: ytdlp-prisir
description: "PrisirAI 提升 yt-dlp 为通用 fetcher — 200+ 网站 metadata + 字幕探测,2026-09-26 ship P3j T21-B"
metadata: 
  node_type: memory
  type: project
  originSessionId: 8770f801-490d-474f-9950-81a5d72bde7b
  modified: 2026-09-26T06:40:02.541Z
---

# PrisirAI × yt-dlp 通用 fetcher(P3j T21-B,2026-09-26 ship)

## 为什么做

Agent-Reach 上游工具有 yt-dlp (https://github.com/yt-dlp/yt-dlp),
**PrisirAI 之前只在 youtube_bridge.py 用它抽 YouTube 字幕**,
但 yt-dlp 实际支持 200+ 网站(B站 / 微博 / Twitter / Reddit / Vimeo / Niconico
/ TikTok 等)的元数据 + 字幕 — 之前只用了 1/200 能力。

## 集成方式(路径 A+C)

| 能力 | PrisirAI 原 | yt-dlp 通用接入 | 入口 |
|------|------------|----------------|------|
| `web_fetch.fetch(url)` | jina/feedparser/urllib | **ytdlp_meta 第三顺位 picker**(视频类 URL) | web_fetch 注册 ytdlp_meta fetcher |
| `web.ytdlp.meta` | 没意识 | 直接 endpoint + capability | endpoints.py / capability.py |
| YouTube 字幕下载 | ✅ youtube_bridge | youtube_bridge 仍负责(不替换) | 不动 |

## 模块/端点

- `prisir_work/web_fetch_ytdlp.py` — ~190 行,2 公开 fn:`ytdlp_meta / ytdlp_health`
- `prisir_work/endpoints.py:_web_ytdlp_*` — 2 个 L0 端点(`/web/ytdlp/{meta,health}`)
- `prisir_work/capability.py:web.ytdlp.{meta,health}` — 2 个 capability
- `prisir_work/web_fetch.py:_FETCHERS` — 注册 ytdlp_meta,picker 第三顺位

## 设计要点

- **零下载**:`skip_download=True + writesubtitles=False`,只抽 metadata
  + 探测字幕语言列表
- **超时控制**:`socket_timeout=30s` 防止 yt-dlp 在 200+ 网站里被卡
- **字幕优先级**:manual 优先于 auto(避免重复展示)
- **永远不 raise**:DownloadError / Exception / 空 url / yt-dlp 未装 全返
  `{ok: False, error: ...}`
- **截断**:markdown ≤ 50000 字符(description 前 2000)

## 与 youtube_bridge 的关系

- **youtube_bridge.py**:YouTube 专用(数据 API v3 / 字幕下载)
- **web_fetch_ytdlp.py**:通用 fetcher(任意 yt-dlp 支持 URL → metadata)
- **互补不冲突**:YouTube 上传仍走 youtube_bridge;通用元数据查询走新 fetcher

## picker 优先级

```python
第一遍:jina(LLM 友好 markdown,任意 URL)
第二遍:feedparser(RSS/Atom/JSON Feed,URL 启发式判定后)
第三遍:ytdlp_meta(视频类 URL,200+ 网站 metadata)
第四遍:first-wins 兜底(http_urllib / a11y / browser_use_cli)
```

## Why

User asked 「请将其上游工具逐一集成」。
盘点 14 项上游工具,yt-dlp 是真 gap #2(纯 Python 库,直接可调;已在用 1/200)。
**接进来**。

## How to apply

- 任何抓视频/音频站点 URL(YouTube/B站/微博/Twitter/Reddit/Vimeo/TikTok 等)
  的 metadata 场景,默认走 `web_fetch.fetch()`,它第三顺位选 ytdlp_meta
- 想单独抽 metadata,用 `web.ytdlp.meta` endpoint
- 在 system prompt 看到 `web.ytdlp.{meta,health}` capability 标记,LLM 知道有
  200+ 网站的 metadata 通道

## 易踩坑

1. yt-dlp 抽 info 也偶发超时 — socket_timeout 控制;失败自动落 urllib
2. 部分网站 403 / 风控 — picker 自动落 urllib + 提示用浏览器
3. B站 2026-06 起被 yt-dlp 标记风控 — 改走 agent-reach `bilibili-subtitle`
4. playlist URL 返回结构不同 — 用 `.get()` 链式访问,字段缺失当空
5. 测试 mock 要 monkeypatch 全局 `yt_dlp.YoutubeDL`,不是模块属性

## 关键 commit

- T21-A feedparser(已 ship)
- T21-B yt-dlp 通用 fetcher(本批)
- T21-C gh CLI(下批)