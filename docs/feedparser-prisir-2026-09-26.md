# feedparser 直接整合 — PrisirAI 信息获取能力增强(P3j T21-A)

> 2026-09-26 ship · 单 commit · feedparser fetcher + endpoint + capability + research 集成

## Context

整合 Agent-Reach 时,用户审视上游工具链,点名 [kurtmckee/feedparser](https://github.com/kurtmckee/feedparser)
作为参考:**PrisirAI 当前只能通过 agent-reach 子进程间接调用 feedparser**,
没有直接集成 — 跟 jina reader(T20-I)一样的差距。

| 能力 | PrisirAI 原(间接) | feedparser 直接接入 |
|------|------------------|-------------------|
| RSS / Atom / JSON Feed 解析 | ❌ 只走 agent-reach `read rss` 子进程 | ✅ `feedparser_fetch(url)` 直接调库,免子进程 |
| 条件 GET(ETag / Last-Modified) | ❌ 每次全量抓,浪费带宽 | ✅ feedparser 自带 304 短路 |
| RSS 内容 → LLM 友好 markdown | ❌ agent-reach 返原始 XML | ✅ 自动剥 HTML,组装 ## entries 列表 |
| 错误隔离 | 走子进程,失败要 spawn 整个 Python | 库级 try/except,几行兜底 |

结论:跟 jina 一样,feedparser 在 RSS/Atom/JSON Feed 这条线上**显著比 agent-reach 间接路径好**(零子进程 / 零延迟 / 304 短路 / 直接 markdown)。
**开 T21-A 把 feedparser 复现进来**(不替换 agent-reach,作为新 fetcher 并发竞速,
命中即用,失败 urllib 兜底)。

## 落地

### 模块

| 文件 | 行数 | 用途 |
|------|------|------|
| `prisir_work/web_fetch_feedparser.py` | ~190 | `feedparser_fetch` / `feedparser_health` 2 公开 fn |
| `prisir_work/endpoints.py` | +30 | 2 个 `/web/feedparser/{fetch,health}` 端点 |
| `prisir_work/capability.py` | +20 | 2 个 `web.feedparser.*` capability |
| `prisir_work/web_fetch.py` | +15 | 注册 feedparser fetcher,picker 第二顺位 |
| `prisir_work/research.py` | +60 | `_detect_feedparser_intent` + `_feedparser_fetch_one` + step 2c 集成 |
| `tests/test_web_fetch_feedparser.py` | ~210 | 9 个 mock case |
| `verify_wechat_publisher.py` | +80 | 2 个 verify check |

### 设计要点

- **URL 启发式判定**(避免跟 jina/urllib 抢活):
  - 含 `.xml` / `.rss` / `/feed` / `/atom` / `/rss` / `?alt=rss` /
    `?format=rss` / `?output=rss` / `?type=rss` / `/feed.json` / `/index.xml`
    → 真抓 feedparser.parse
  - 否则返 `not_a_feed_url`,picker 落 urllib
- **304 Not Modified**:返 `ok=False + error=not_modified`,上层 web_fetch
  缓存命中应已覆盖
- **bozo=True 不致命**:feedparser 在 XML 有错时仍可能给出部分 entries,
  `meta.bozo=True` + `meta.bozo_exception` 标记,内容正常返回
- **截断**:单 feed ≤ 50 条 + markdown ≤ 50000 字符(双截断防 LLM context 爆炸)
- **永远不 raise**:所有异常(网络 / 解析 / 空 feed)→ `{ok: False, error: ...}`

### picker 优先级

```python
# web_fetch.py picker loop(三遍)
第一遍:优先选 jina(LLM 友好 markdown)
第二遍:其次 feedparser(RSS/Atom/JSON Feed 类 URL 比 jina/urllib 更专业)
第三遍:first-wins 兜底(http_urllib / a11y / browser_use_cli)
```

跟 jina picker 不冲突:jina 优先级仍最高;feedparser 补 RSS 类盲区。

### 与 Agent-Reach 互补

- **Agent-Reach `read rss`**:子进程 + agent-reach 0.x,适合"平台统一封装"
- **feedparser 直接**:库调用 + 零子进程,适合 PrisirAI 主对话/研究流水线

**不替换**:agent-reach 仍管 cookie / 平台登录 / 反爬的 14 个平台;
feedparser 只管 RSS / Atom / JSON Feed 三类开放协议 feed。

### 数据流

```
主对话「看看 https://news.ycombinator.com/rss 的最新」
  ↓ LLM 解析出 [[EXEC: web.feedparser.fetch url="..."]]
  ↓ scan_and_exec (T16-A)
  ↓ endpoints._web_feedparser_fetch
  ↓ web_fetch_feedparser.feedparser_fetch(url)
  ↓ feedparser.parse(url, agent=feedparser.USER_AGENT, timeout=30)
  ↓ {content: "# Hacker News\n\n## 1. Post A ...", meta: {item_count: 3, ...}}
  ↓ build_exec_result(ok=True, ...)
  ↓ ws event: capability_exec_result {ok, result.content}
  ↓ 前端渲染 markdown + 用户继续追问

research("调研 https://news.ycombinator.com/rss")
  ↓ step 2 web_search 并发
  ↓ step 2c _detect_feedparser_intent → 命中 feed URL
  ↓ _feedparser_fetch_one(url, timeout=15, max_items=20)
  ↓ 把 feed 内容作为 #1 source 插入 search_results 最前面
  ↓ step 3 fetch top URLs(feed 已经在 sources 里)
  ↓ step 4 LLM 合成

web_fetch.fetch("https://example.com/feed.xml", options={"no_cache": True})
  └─ 并发跑:http_urllib + a11y + browser_use_cli + jina + feedparser
  └─ picker loop:feedparser 命中 → 选它(markdown)
  └─ 缓存到 7d 磁盘 + 32 条内存 LRU
```

### 配置(无需 env)

feedparser 是个**纯 Python 库,零配置零 env**。装好就能用:

```bash
pip install feedparser
# 默认 6.0.14+ 即可;不需要 API key
```

## 与 jina reader 的对照

| 维度 | jina reader(T20-I) | feedparser(T21-A) |
|------|--------------------|-------------------|
| 安装 | Python 库 + 可选 hosted | 纯 Python 库 |
| 配置 | hosted_no_key / hosted_with_key / self_hosted | 零配置 |
| 覆盖 | 任意 URL → 干净 markdown | RSS / Atom / JSON Feed 三类 |
| 速率 | hosted_no_key 20 RPM 限流 | 无 |
| 缓存 | jina hosted 5min + web_fetch 7d | web_fetch 7d + feedparser 304 |
| 优先 | picker 第一顺位(任何 URL) | picker 第二顺位(仅 feed 类 URL) |

## 易踩坑

1. **feedparser 6 vs 5**:6.x 是 Python 3.8+ 重写版本,API 略不同;
   6.0.14 已支持 `timeout` kwarg(老版本要传 `request_timeout`)

2. **bozo=True 不算错误**:很多 feed 都有轻微 XML 错误但仍能解析出 entries;
   我们用 `meta.bozo=True` 标记但 content 正常返回

3. **status=0 表示 feedparser 没拿到 HTTP 状态**(网络层失败),
   但可能仍有 entries;我们 status=0 也允许走完解析逻辑

4. **超大 feed**:HN 的 /rss 也就 ~30 条,但有的博客 feed 一发上千条;
   `max_items=50 + max_chars=50000` 双截断兜底

5. **测试 mock**:mock `feedparser.parse` 时要让返回对象有 `status` /
   `bozo` / `bozo_exception` / `feed` / `entries` 5 个字段,
   否则 `_current_mode_str()` 之类访问会 AttributeError

6. **跟 jina 重叠**:普通 HTML 页面 jina 已经能处理,
   不应让 feedparser 也去抢(jina 优先);feedparser 启发式
   只在 URL 真的像 feed 时才动手

## 测试 + verify

- `tests/test_web_fetch_feedparser.py` — **9/9 绿**
  - 4 个 feedparser_fetch(成功 RSS / 304 / 5xx / not_a_feed_url)
  - 2 个 feedparser_fetch(bozo=True / 空 entries)
  - 1 个 feedparser_health
  - 2 个 picker 集成(jina 失败落 feedparser / 都成功走 jina 优先)
- `verify_wechat_publisher.py` — **42/42 绿**(+2 check:`check_feedparser_module` +
  `check_feedparser_endpoints_and_registration`)
- 既有 jina / yt-dlp / reach / research 测试无回归

## 兼容性

- 不破坏 web_fetch 现有调用方:`web_fetch.fetch()` 签名不变,行为对调用方透明
- 不破坏 web_search 现有调用方:feedparser 不参与 search(只参与 fetch)
- env 没配任何东西 → 行为完全等同之前(零差异)

## 后续可能

- [ ] Atom 0.3 旧版兼容(现在依赖 feedparser 自带兼容)
- [ ] feedparser OPML 输出(订阅列表导入) — 看用户需求
- [ ] 自定义 User-Agent(部分 feed 站点要求特定 UA) — 看需求
- [ ] web_research 加 feedparser 聚合(多 feed URL → 一次返回多源) — 看需求

---

## Why(决策记录)

用户原话:「前面提到多个平台接入未完成,而这个也是它作为能力层接入的上游工具
https://github.com/kurtmckee/feedparser,我们在PrisirAI中做了集成吗?请将其上游工具
逐一集成」

盘点 14 个 Agent-Reach 上游工具:
- ✅ 真有 gap 的:**feedparser**(纯 Python 库,直接可调)、**yt-dlp**(已在 youtube_bridge
  用,值得提升成通用 fetcher)、**gh CLI**(系统已装,直接可调)
- ⏸ 推迟:Exa(需 key)/ Whisper(已 ship)/ twitter-cli / rdt-cli / xhs-cli /
  bili-cli / linkedin-mcp(都需 cookie)/ Node.js(不依赖)
- ❌ 不做:bs4 / lxml / aiohttp / httpx(jina markdown 已替代)/ ffmpeg(转码无关)/
  playwright(已有)

**T21-A 是 3 个真 gap 里的第一个**,跟 T20-I jina 同样按
"模块 + endpoint + capability + tests + verify + docs + memory + commit" 模式 ship。