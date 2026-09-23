# 档 C 设计 — wigolo 未覆盖维度(P2.5+17,2026-09-23)

## 调研结论
wigolo(KnockOutEZ/wigolo, AGPL-3.0,Node 20+,~1.5GB 本地模型)提供 10 个工具。
Prisir 档 A+档 B 已 ship 6 个,剩 4 个未覆盖维度:

| wigolo 工具 | Prisir 现状 | 档 C 计划 | 优先级 |
|---|---|---|---|
| search / fetch / extract / find_similar / research / watch | ✅ 档 B 已 ship | — | — |
| **cache**(查本地命中) | ⚠️ 仅存储层 `cache.cache_get`,无查询门面 | **P2.5+17a**:加 `cache_list/cache_invalidate/cache_stats` | 半天,zero-risk |
| **diff**(页面版本对比) | ❌ 无 | **P2.5+17b**:两 URL/两时间快照 diff | 半天,zero-risk |
| **crawl**(全站 BFS/DFS/sitemap) | ❌ 无 | **P2.5+17c**:BFS + robots.txt + 同 host 限速 | 1-2 天,中风险 |
| **agent**(自主多源采集) | ⚠️ research 半覆盖,无 LLM 决策闭环 | **P2.5+17d**:LLM 驱动多步抓取 + 结构化落盘 | 1-2 天,高 ROI 但重 |

## 设计原则(沿用档 B)
- **失败降级,绝不抛**:wigolo 风格是「本地优先」,任何 HTTP 失败先查本地
- **零外部依赖**:仅 stdlib urllib + node:sqlite(扩展)
- **复用现有模块**:web_fetch / web_search / cache / extract / research 一字不动
- **接口一致**:所有能力走 `@register("/web/<name>", method, risk, auth)` 模式
- **多源 fallback**:wigolo 18 引擎 vs Prisir 5 provider,差距靠 RRF + on-device rerank 弥补

## 实施顺序
1. **P2.5+17a web.cache**(半天)
   - 公共:`cache_list(host=None, limit=20) / cache_invalidate(url) / cache_stats()`
   - 端点:`/web/cache/list`、`/web/cache/invalidate`、`/web/cache/stats`
   - 复用:`cache.cache_get` + glob `~/.prisIrai/cache/web/*.json`
   - 测试:list 列表/host 过滤/stats 字段/invalidate 真删/不存在返工

2. **P2.5+17b web.diff**(半天)
   - 公共:`diff(url_a, url_b, *, mode='url'|'time', since=None) -> {'ok', 'a', 'b', 'diff': {added, removed, changed}, 'summary'}`
   - mode='url':两 URL 对比;mode='time':同 URL 两时间快照(从 cache 拿)
   - 端点:`/web/diff`(POST)
   - 复用:web_fetch.fetch + cache
   - 算法:字符级 difflib 或行级 unified_diff(选行级,可读 + 性能平衡)
   - 测试:同 URL 对比/两 URL 对比/cache 时间快照/未变化返空 diff

3. **P2.5+17c web.crawl**(1-2 天)
   - 公共:`crawl(start_url, *, max_pages=50, max_depth=2, same_host=True, respect_robots=True, rate_per_host=1.0) -> {'ok', 'pages': [...], 'skipped': [...]}`
   - BFS:从 start 抓 → 解析 <a href> 同 host → 限速 → 去重 → 写到 cache
   - 端点:`/web/crawl`(POST)
   - 复用:web_fetch + cache + urlparse
   - 测试:mock 5 页同 host site/深度截断/robots.txt 尊重/跨 host 过滤/限速间隔

4. **P2.5+17d web.agent**(1-2 天)
   - 公共:`agent(query, *, max_steps=8, llm_call=None) -> {'ok', 'plan': [...], 'steps': [...], 'findings': {...}}`
   - 与 research 区别:research = 「query → search×N → fetch → synthesize」;agent = 「LLM 决策每步 search/fetch/crawl/extract → 累积 findings → 直到 max_steps 或目标达成」
   - 端点:`/web/agent`(POST)
   - 复用:web_search + web_fetch + extract + research
   - LLM 接口:同 research.llm_call(prompt) → str
   - 测试:mock LLM 返固定 plan → 各 step 真跑 → findings 累加/目标达成提前终止

## 任务表
- Task #13:P2.5+17a web.cache 查询门面 + E2E
- Task #14:P2.5+17b web.diff 版本对比 + E2E
- Task #15:P2.5+17c web.crawl 全站爬虫 + E2E
- Task #16:P2.5+17d web.agent 自主采集 + E2E