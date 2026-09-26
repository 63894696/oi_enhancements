# Exa MCP 集成到 PrisirAI(P3j T22-A,2026-09-26)

## Context

**用户原话**:
- 推迟项决策(2026-09-26):「**Exa MCP**」推迟理由是「需 key,价值待评估」。
- 重新启动决策:「我倾向 exa+reddit,而且系统环境变量有 exa 的 key 可用。」
- 配 key 确认:「有,马上能配」

Exa MCP 提供 4 类语义搜索能力(embedding + LLM 重排序):
- `search`:自然语言查询 → top-N 高质量结果($0.005/次)
- `find_similar`:URL → 类似内容(类似 jina reader 反向)
- `answer`:问答 + 引用 citations(类似 Perplexity)
- 官方端点 `https://api.exa.ai/{search,findSimilar,answer}`,header `x-api-key`

## 现状盘点

| 现状 | 决策 |
|------|------|
| `prisir_work/web_search.py` 已支持 `register_provider(name, fn)` + RRF rank fusion | **复用**:exa 当一个 provider,并发跑 DDG/Baidu/Bing/Exa |
| `prisir_work/endpoints.py` `@register(path, ...)` + L0 装饰器 | **复用**:4 端点 `/web/exa/{health,search,find_similar,answer}` |
| `prisir_work/capability.py` `register_capability(cid, ...)` | **复用**:4 capability |
| `prisir_work/web_fetch.py` 已支持 `register_fetcher` + picker | **不动**:Exa 不当 fetcher(它是 search provider) |
| 用户环境 `EXA_API_KEY` 已配置(`7232f072...` 前缀) | env 在 → provider 自动注册;env 不在 → 不注册,graceful 跳过 |

## Goals & Constraints

### Goals
1. **exa_bridge.py**:4 public fns(`exa_health` / `exa_search` / `exa_find_similar` / `exa_answer`) + `_http_post` urllib helper
2. **4 endpoints + 4 capability**,全 L0 只读免确认
3. **exa_search provider** 注册到 `web_search.py`(env 触发)
4. **tests**:unit 8(bridge) + 11(endpoints) + verify 4 check = 23 绿
5. **docs + memory + commit**

### Non-Goals(明确不做)
- ❌ 不做 fetcher(Exa 不抓全文,只返 search 结果)
- ❌ 不做 rate limiter(Exa API 没明确 429 上限,key 失效才报)
- ❌ 不做自动付费升级提示(等用户主动改配额)
- ❌ 不接 Exa Find Similar URL 的内容抓取(它是找 URL,不是读 URL)

## Design

### A1. `prisir_work/exa_bridge.py`(子进程桥的形态换成纯 HTTP)

```python
"""exa_bridge.py — Exa MCP 直接调(2026-09-26 ship,P3j T22-A)。

复现 Exa MCP 4 类语义搜索能力:search / findSimilar / answer / health。

设计:
  · urllib.request POST https://api.exa.ai/{search,findSimilar,answer}
  · header: x-api-key + Content-Type: application/json
  · timeout 30s 默认
  · 失败一律返 {ok: False, error: "exa_xxx"} 不 raise
  · costDollars 字段透传给 caller(计费用)

公开 API:
  · exa_health()             → {ok, installed, key_set, mode, key_prefix, search_time_ms}
  · exa_search(query, **opts)→ {ok, query, results[{url,title,snippet,text}],
                                cost_dollars, search_time_ms, sources}
  · exa_find_similar(url,**opts) → 同上,key=url
  · exa_answer(query, **opts)→ {ok, query, answer, citations[{url,title}],
                                cost_dollars, search_time_ms}
"""
```

### A2. exa_search provider 注册到 web_search.py

```python
# env 在才注册
if os.environ.get("EXA_API_KEY", "").strip():
    def exa_search_provider(query: str, limit: int = 10) -> list[dict[str, Any]]:
        try:
            from prisir_work import exa_bridge as _ex
            r = _ex.exa_search(query, num_results=min(max(limit, 1), 30),
                               max_chars=2000)
            if not r.get("ok"):
                return []
            return [{"url": it["url"], "title": it["title"],
                     "snippet": it.get("snippet", "")[:300]}
                    for it in r.get("results", []) if it.get("url")]
        except Exception:
            return []
    register_provider("exa_search", exa_search_provider)
```

### A3. endpoints + capability(全 L0)

4 endpoint + 4 capability 跟 T21-C gh 同模式。

### A4. tests(22 mock case)

- **test_exa_bridge.py** 9 个:`exa_health` 3 路 + `exa_search` 4 路 + `exa_find_similar` 1 路 + `exa_answer` 1 路
- **test_exa_endpoints.py** 11 个:endpoint 集成 5 + 空 query/url 2 + capability 1 + provider 注册/未注册 2 + health invalid_key 1
- **verify** 4 check:`check_exa_bridge_module` / `check_exa_endpoints_and_registration` / `check_exa_health_mode` / `check_exa_capability_keywords`

### A5. 不动 research.py

T20-I.2 + T21-C 阶段已经集成过类似 provider(gh_search 等),Exa 也走同模式,
不需在 research.py 单独加路由 — `web_search.search()` 已 RRF 融合。

## Critical Files

**新建**
- [prisir_work/exa_bridge.py](prisir_work/exa_bridge.py) ~210 行
- [tests/test_exa_bridge.py](tests/test_exa_bridge.py) ~250 行
- [tests/test_exa_endpoints.py](tests/test_exa_endpoints.py) ~240 行
- [docs/exa-prisir-2026-09-26.md](docs/exa-prisir-2026-09-26.md)
- [memory/exa-prisir.md](memory/exa-prisir.md)

**修改**
- [prisir_work/endpoints.py](prisir_work/endpoints.py) +60 行(4 端点)
- [prisir_work/capability.py](prisir_work/capability.py) +30 行(4 capability)
- [prisir_work/web_search.py](prisir_work/web_search.py) +25 行(exa_search provider)
- [verify_wechat_publisher.py](verify_wechat_publisher.py) +95 行(4 check)
- [memory/MEMORY.md](memory/MEMORY.md) index +1 行

**不动**
- web_fetch.py / web_research.py / web_fetch_jina.py / agent_reach_bridge.py

## Implementation Steps

1. **exa_bridge.py**(~2h):4 public fns + urllib helper + 3 mode(无 key / 失效 / live)
2. **test_exa_bridge.py**(~1h):9 mock case
3. **endpoints.py + capability.py**(~0.5h):4 端点 + 4 capability
4. **web_search.py provider**(~0.3h):env 触发注册
5. **test_exa_endpoints.py**(~0.5h):11 case
6. **verify 4 check**(~0.3h)
7. **docs + memory + commit**(~0.5h)

**总计 ~5h,1 commit**

## Verification

```bash
# 单元测试
python tests/test_exa_bridge.py        # 9/9
python tests/test_exa_endpoints.py     # 11/11

# verify
python verify_wechat_publisher.py -SkipHttp
# 48 + 4 = 52/52

# E2E:env 在时真跑(已实测)
EXA_API_KEY=7232f072... python -c "
from prisir_work.exa_bridge import exa_search
r = exa_search('Claude Code review', num_results=3)
print(r['results'][0]['title'], r['cost_dollars']['total'], '\$')
"
# 期望:mode=live + 3 高质量结果 + cost ~0.005
```

## Rollback

- **endpoints**:4 行装饰器删除
- **capability**:4 行 register_capability 删
- **web_search provider**:`if os.environ.get("EXA_API_KEY")` 块整段删
- **exa_bridge.py / 测试**:文件删
- **verify check**:4 check 行删 + CHECKS 列表 4 行删

## Risks

| 风险 | 缓解 |
|------|------|
| Exa API key 失效(INVALID_API_KEY) | `exa_health.mode=key_invalid` 显式标记,UI 可看 |
| Exa 收费爆炸($0.005-0.01/次) | 用户主动配 key 已知;`exa_answer` 比 search 贵,默认 query length cap |
| Exa 服务挂了(5xx) | 5xx 返 `error=exa_http_5xx`,web_search RRF 自动跳过(provider 返空) |
| 配错 key(secret-key vs api-key 混) | mode=key_invalid + hint 提示「检查 Exa dashboard」 |
| 网络抖动 | timeout=30s 兜底,失败由 web_search RRF 跳过 |
| env 在但 key 是 placeholder | `exa_health` 真发请求 ping → 401 → mode=key_invalid |
| 配额耗尽 | Exa 不返 429 直接报错 tag,显式 show 给 UI |

## 数据流

```
主对话「调研 Claude Code 的 review 功能」
  ↓ LLM 解析出 [[EXEC: web.research]]
  ↓ research.plan_queries → 3 个 sub query
  ↓ research.search("Claude Code review")
  ↓ web_search.search() RRF 融合
    ├─ DDG search    → 5 results
    ├─ Baidu search  → 5 results
    ├─ Bing search   → 5 results
    └─ exa_search    → 5 results (env 在才跑)
  ↓ 合并去重 + RRF 重排 → top 5
  ↓ fetch top 5 URLs(每个可能走 jina / feedparser / urllib)
  ↓ LLM 合成 + 引用 [n]

或主对话直接说:
「用 Exa 查 Claude Code review」
  ↓ LLM 解析出 [[EXEC: web.exa.search query="Claude Code review" num_results=5]]
  ↓ endpoints._web_exa_search
  ↓ exa_bridge.exa_search → urllib POST /search
  ↓ {ok, query, results, cost_dollars, search_time_ms}
  ↓ 前端展示 markdown 列表 + cost 字段
```