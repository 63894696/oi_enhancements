# yt-dlp 通用 fetcher — PrisirAI 信息获取能力增强(P3j T21-B)

> 2026-09-26 ship · 单 commit · yt-dlp fetcher + endpoint + capability + picker 集成

## Context

yt-dlp (https://github.com/yt-dlp/yt-dlp) 是 Agent-Reach 上游工具之一,
**PrisirAI 已经在 youtube_bridge.py(402 行)用 yt-dlp 抽 YouTube 元数据 + 字幕**,
但只是 YouTube 专用,没跟着 yt-dlp 支持的 200+ 网站(B站/微博/Twitter/Reddit/
Vimeo/Niconico/TikTok 等)抽 metadata。

用户原话:「请将其上游工具逐一集成」— T21-B 是 3 个真 gap 里的第二个
(T21-A feedparser / T21-B yt-dlp / T21-C gh CLI)。

| 能力 | PrisirAI 原(youtube_bridge) | yt-dlp 通用 fetcher |
|------|---------------------------|-------------------|
| YouTube 元数据 | ✅ 用 yt-dlp 抽 | ✅ 同样能抽(but 不重复) |
| B站 / 微博 / Twitter / Reddit 元数据 | ❌ 没有 | ✅ `ytdlp_meta(url)` 直接 |
| 字幕探测 | ✅ YouTube 专用 | ✅ 任意 yt-dlp 支持的网站 |
| 单次 timeout 控制 | ✅ YouTube 路径有 | ✅ 通用 socket_timeout |
| 错误隔离 | 走 youtube_bridge | 走 web_fetch_ytdlp |

结论:yt-dlp 是个**通用 metadata 引擎**,PrisirAI 之前只用了 1/200 的能力。
**开 T21-B 把 yt-dlp 抽成通用 fetcher**,不替换 youtube_bridge(YouTube 上传路径
仍依赖它),只补齐"通用视频元数据查询"。

## 落地

### 模块

| 文件 | 行数 | 用途 |
|------|------|------|
| `prisir_work/web_fetch_ytdlp.py` | ~190 | `ytdlp_meta` / `ytdlp_health` 2 公开 fn |
| `prisir_work/endpoints.py` | +30 | 2 个 `/web/ytdlp/{meta,health}` 端点 |
| `prisir_work/capability.py` | +20 | 2 个 `web.ytdlp.*` capability |
| `prisir_work/web_fetch.py` | +25 | 注册 ytdlp_meta fetcher,picker 第三顺位 |
| `tests/test_web_fetch_ytdlp.py` | ~200 | 6 个 mock case |
| `verify_wechat_publisher.py` | +80 | 2 个 verify check |

### 设计要点

- **零下载**:`skip_download=True` + `writesubtitles=False`,只抽 metadata
  + 探测字幕语言列表(字幕文件留给 youtube_bridge 单独下载)
- **超时控制**:`socket_timeout=30s` 防止 yt-dlp 在 200+ 网站里被卡住
- **兼容 playlist / 单视频 / 站点不同返回**:统一提取 10 个关键字段
  (title / uploader / duration / description / webpage_url / extractor /
  view_count / like_count / upload_date / subtitle_languages)
- **字幕优先级**:manual 优先于 auto(避免重复展示)
- **截断**:markdown ≤ 50000 字符(description 前 2000)
- **永远不 raise**:`DownloadError / Exception / 非 dict info / 空 url /
  yt-dlp 未装` 全部 → `{ok: False, error: ...}`

### picker 优先级

```python
# web_fetch.py picker loop(四遍)
第一遍:jina(LLM 友好 markdown)
第二遍:feedparser(RSS/Atom/JSON Feed,仅 URL 启发式判定后)
第三遍:ytdlp_meta(视频类 URL,200+ 网站 metadata)
第四遍:first-wins 兜底(http_urllib / a11y / browser_use_cli)
```

跟 jina/feedparser 不冲突:ytdlp_meta 跟 jina 一样,但只在视频类 URL
(YouTube / B站 / Vimeo 等)拿到的元数据比 jina markdown 更结构化
(duration / view_count / 字幕列表)。

### 与 youtube_bridge.py 的关系

- **youtube_bridge.py**:专注 YouTube 专用路径
  - YouTube Data API v3(频道/列表/上传)
  - YouTube 字幕轨道下载(yt-dlp subs)
  - 不被 T21-B 改动
- **web_fetch_ytdlp.py**:通用元数据 fetcher
  - 任意 yt-dlp 支持 URL → metadata + 字幕探测
  - 不下载视频流
  - 主对话 / research 走 web_fetch 自动选

**互补不冲突**:YouTube 视频如需要 metadata + 字幕,jina + yt-dlp 都能选;
若需要更细的字幕内容,仍走 youtube_bridge 下载。

### 数据流

```
主对话「这个 YouTube 视频说什么 https://www.youtube.com/watch?v=jNQXAC9IVRw」
  ↓ LLM 解析出 [[EXEC: web.ytdlp.meta url="..."]]
  ↓ scan_and_exec (T16-A)
  ↓ endpoints._web_ytdlp_meta
  ↓ web_fetch_ytdlp.ytdlp_meta(url)
  ↓ yt_dlp.YoutubeDL(quiet=True, skip_download=True, socket_timeout=30)
  ↓ extract_info(url, download=False)
  ↓ {content: "# Me at the zoo\n\n## Description\nThe first...", meta: {...}}
  ↓ build_exec_result(ok=True, ...)
  ↓ ws event + 前端渲染

主对话「B站视频 https://www.bilibili.com/video/BV1xx411c7mD 说什么」
  ↓ 同上 → yt-dlp 自动识别 B站 extractor → 拿字幕语言列表
  ↓ LLM 看 subtitle_languages,引导用户进一步调字幕内容

web_fetch.fetch("https://www.youtube.com/watch?v=...", options={...})
  └─ 并发跑:http_urllib + jina + feedparser + ytdlp_meta
  └─ picker:ytdlp_meta 命中 → 选它(metadata markdown)
  └─ 缓存到 7d 磁盘 + 32 条内存 LRU
```

## 与 jina / feedparser 对照

| 维度 | jina(T20-I) | feedparser(T21-A) | ytdlp(T21-B) |
|------|-------------|------------------|-------------|
| 安装 | Python 库 + 可选 hosted | 纯 Python 库 | Python 库(2026.06.09) |
| 配置 | hosted_no_key / hosted_with_key / self_hosted | 零配置 | 零配置 |
| 覆盖 | 任意 URL → 干净 markdown | RSS / Atom / JSON Feed | 200+ 视频/音频站点 |
| 速率 | hosted_no_key 20 RPM | 无 | 无(单次 socket_timeout=30s) |
| 优先 | picker 第一顺位 | picker 第二顺位(URL 启发式) | picker 第三顺位 |

## 易踩坑

1. **yt-dlp 抽 info 时也偶发连接超时**:socket_timeout 选项管;失败返
   `meta.error=ytdlp_download_error` + detail 含原始错误

2. **部分网站返回 403 / 风控)**:meta.error=ytdlp_download_error + detail
   含 "403 Forbidden" / "Forbidden" / "Sign in to confirm you’re not a bot"
   — picker 落 urllib / 提示用户用浏览器

3. **B站部分视频被 yt-dlp 标记 2026-06 起风控**:meta.error 含 B站特定错误,
   改走 agent-reach `bilibili-subtitle` 子命令(已 ship T20)

4. **playlist URL 返 dict 不一样**:title / entries 都要兼容;我们用 `.get()`
   链式访问,字段缺失就当空

5. **测试 mock YoutubeDL**:`monkeypatch.setattr(_yt_dlp, "YoutubeDL", FakeYDL)` —
   注意 mock 的是全局 `yt_dlp` 模块(因为 web_fetch_ytdlp 里 import 是局部的)

6. **subtitle_languages 顺序**:manual 字幕语言先列,auto 字幕后列(避免重复
   展示同名 manual + auto)

## 测试 + verify

- `tests/test_web_fetch_ytdlp.py` — **6/6 绿**
  - 4 个 ytdlp_meta(成功 / DownloadError / 空 url / yt-dlp 未装)
  - 1 个 ytdlp_health(版本 + 1747 extractors)
  - 1 个 picker 集成(jina + feedparser 失败落 ytdlp_meta 或 urllib)
- `verify_wechat_publisher.py` — **44/44 绿**(+2 check:`check_ytdlp_module` +
  `check_ytdlp_endpoints_and_registration`)
- 既有 jina / feedparser / reach / research 测试无回归

## 兼容性

- 不破坏 web_fetch 现有调用方:`web_fetch.fetch()` 签名不变,行为对调用方透明
- 不破坏 youtube_bridge.py:YouTube 上传路径仍走它
- env 没配任何东西 → 行为完全等同之前(零差异)

## 后续可能

- [ ] yt-dlp 配置缓存(per-domain preferred extractor)
- [ ] yt-dlp 字幕下载走 youtube_bridge(避免重复实现)
- [ ] 200+ 站点的字段映射(各站字段名不同)
- [ ] 多 URL 批量抽 metadata(feedparser 同款思路)

---

## Why(决策记录)

用户原话:「前面提到多个平台接入未完成,而这个也是它作为能力层接入的上游工具
https://github.com/kurtmckee/feedparser,我们在PrisirAI中做了集成吗?请将其上游工具
逐一集成」

盘点 14 项上游工具(参见 `docs/why_ai-tools-decisions-2026-09-26.md`):
- T21-A feedparser 已 ship(纯 Python 库,直接可调)
- T21-B yt-dlp(已在 youtube_bridge 用,值得提升成通用 fetcher)
- T21-C gh CLI(下批)

**T21-B 关键洞察**:yt-dlp 是 metadata 引擎,PrisirAI 之前只用了 1/200 能力
(YouTube 字幕)。提为通用 fetcher 后,B站/微博/Twitter/Reddit/Vimeo/Niconico
/TikTok 等任意 yt-dlp 支持站点都能直接拿 metadata,跟 jina/feedparser 平级
进 picker。