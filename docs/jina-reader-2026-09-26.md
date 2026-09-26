# jina-ai/reader 集成 — PrisirAI 信息获取能力升级(P3j T20-I)

> 2026-09-26 ship · 单 commit · jina fetcher + search provider + research pipeline 集成

## Context

整合 Agent-Reach 时,用户审视了上游工具链,点名 jina-ai/reader 作为参考:
> "我看了Agent Reach的上游工具比如https://github.com/jina-ai/reader,认为PrisirAI的搜索能力大概是无法对齐它的。是否有将它的功能与PrisirAI在同事项上的能力做了对比分析?不要在乎对比后可能替换掉我们已做过的开发,如果确实是更好的就整合过来。"

对比项:
| 场景 | PrisirAI 原 | jina-ai/reader |
|------|------------|----------------|
| `fetch(url)` | urllib + HTML(广告/导航条/cookie banner 一起返) | r.jina.ai 返 LLM-friendly markdown,自动剥噪音 |
| `search(query)` | DDG/Baidu/Bing 仅返 title+snippet | s.jina.ai 返 top N URL + **全文 markdown** |
| 全文 LLM 吃 | 还要再 fetch 一次 | 一发就带正文 |
| 缓存 | 自带 7d 本地磁盘 | hosted 共享 CDN cache(命中率高) |

结论:jina 在"URL→LLM"和"query→top N 全文"两条线上都明显更好。
**开 T20-I 把 jina 复现进来**(不替换 web_fetch/web_search,作为新 fetcher/provider 并发竞速,
命中即用,失败 urllib 兜底)。

## 落地

### 模块

| 文件 | 行数 | 用途 |
|------|------|------|
| `prisir_work/web_fetch_jina.py` | ~210 | jina_fetch / jina_search / jina_health 3 公开 fn |
| `prisir_work/web_fetch.py` | +20 | 注册 jina fetcher,picker 优先选 |
| `prisir_work/web_search.py` | +15 | JINA_API_KEY/URL env 设了 → 注册 jina_search provider |
| `prisir_work/research.py` | +30 | step 2a jina 全文搜索插队(每个 plan query) |
| `prisir_work/endpoints.py` | +60 | 3 个 `/web/jina/{health,fetch,search}` 端点 |
| `prisir_work/capability.py` | +35 | 3 个 `web.jina.*` capability |
| `tests/test_web_fetch_jina.py` | ~260 | 11 个 mock case |
| `verify_wechat_publisher.py` | +60 | 2 个 verify check |

### 配置(env)

```bash
# 三选一/多选,env 全空 → 走 hosted 零依赖

# 1. hosted + key(正本,免费层 1M token)
export JINA_API_KEY="jina_xxxx"

# 2. 自部署 Docker(零 key、零外部依赖、隐私)
#    docker run -p 8081:8081 ghcr.io/jina-ai/reader:oss
export JINA_READER_URL="http://localhost:8081"
export JINA_SEARCH_URL="http://localhost:8081"

# 3. 都不设 → hosted(r.jina.ai / s.jina.ai),无 key 也能用(返 cached snapshot)
```

### 数据流

```
web_fetch.fetch("https://example.com")
  └─ 并发跑:http_urllib + a11y + browser_use_cli + jina
  └─ picker loop 优先选 jina(干净 markdown)
  └─ jina 失败 → 自动落回 urllib
  └─ 缓存到 7d 磁盘 + 32 条内存 LRU

web_search.search("Claude Code 评价")
  └─ 并发跑:ddg_html + baidu + bing_public [+ jina_search(若 env 配了)]
  └─ RRF 融合去重排序
  └─ jina 命中带正文,其它只带 snippet

research("调研 X")
  └─ step 1 plan_queries → 4 个 plan query
  └─ step 2 web_search 并发
  └─ step 2a jina 全文搜索插队(若 env 配了)
  └─ step 2b reach.search 插队(若 query 含垂类关键词)
  └─ step 3 fetch top URLs(走 web_fetch,jina 优先)
  └─ step 4 LLM 合成 + 引用编号

agent 主对话
  └─ LLM 看到 system prompt 的 web.jina.{fetch,search,health} capability 标记
  └─ 输出 [[EXEC: web.jina.fetch url="..."]]
  └─ scan_and_exec → /web/jina/fetch → 干净 markdown → LLM 接着总结
```

### 与 Agent-Reach 互补

- **Agent-Reach**:14 个**垂类平台**(小红书/B站字幕/GitHub/V2EX/RSS…),子进程桥
- **jina reader**:任意 URL → 干净 markdown,**通用**,HTTP 走 hosted/自部署
- **不冲突**:web_fetch 并发跑全部 fetcher,jina 命中就选它

## 易踩坑

1. **JINA_API_KEY 配了反而变慢?** — hosted 直连 r.jina.ai 平均 5-15s,自部署 Docker 更慢。
   web_fetch 的并发竞速机制保证:第一个成功就跳出去,慢 fetcher 变成孤儿线程被 GC。

2. **s.jina.ai 返回非 JSON?** — 我们的 jina_search 加了 `Accept: application/json` 头,
   jina 会按 JSON 返。fallback 解析失败时整段当一个 result,不会崩。

3. **picker 选错 fetcher?** — picker 第一遍循环专门挑 jina,
   第二遍才 first-wins 兜底。`_FETCHERS` 注册顺序不重要,只看 picker 的优先级逻辑。

4. **自部署 URL 末尾的 `/`?** — `JINA_READER_URL` 会自动 `rstrip("/")`,多写少写无所谓。

5. **research step 2a 抢 web_search 资源?** — jina 跟 web_search 是两个 ThreadPoolExecutor,
   并行不互抢。jina 默认 30s timeout,每个 plan query 跑一次。

## 测试 + verify

- `tests/test_web_fetch_jina.py` — **11/11 绿**
  - 4 个 jina_fetch(成功/4xx/超时/自部署 URL)
  - 2 个 jina_search(成功 JSON/空 query)
  - 2 个 jina_health(hosted ok/双端 down)
  - 2 个 picker 集成(jina 优先/兜底 urllib)
  - 1 个 web_search provider 注册
- `verify_wechat_publisher.py` — **39/39 绿**(+2 check:`check_jina_module` + `check_jina_endpoints_and_registration`)
- 既有 12 reach test 全部仍绿,无回归

## 兼容性

- 不破坏 web_fetch 现有调用方:web_fetch.fetch() 签名不变,行为对调用方透明
- 不破坏 web_search 现有调用方:web_search.search() 签名不变,只多了一个 provider
- env 没配 JINA_* → 行为完全等同之前(零差异)

## 后续可能

- [ ] web_research 加 jina 全文 LLM 摘要(目前 jina 直接给 LLM 吃)
- [ ] jina self-hosted Docker 一键拉取脚本(companion/_setup_jina.sh)
- [ ] 多 jina 实例并发(目前 hosted 一个 URL,自部署一个 URL)
- [ ] cache layer:web_fetch 已 7d 磁盘缓存,jina hosted 自己有 CDN cache,两层互不冲突
