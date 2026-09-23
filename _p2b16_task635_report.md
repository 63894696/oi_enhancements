# P2.5+16 task #635 — web_search / web_fetch 注册进 prisir_work 能力层

## Summary
把 web_search 和 web_fetch 注册进 prisir_work 能力层(capability + endpoints),L0 只读免确认,handler 真调底层函数(不 stub)。

## Files changed

| 文件 | 状态 | 大小 | mtime |
|------|------|------|-------|
| `C:\Users\Administrator\oi_enhancements\prisir_work\capability.py` | 改(追加 2 个 register_capability) | 4194 B | 2026-09-23T21:19:43 |
| `C:\Users\Administrator\oi_enhancements\prisir_work\endpoints.py` | 改(追加 2 个 @register handler) | 3507 B | 2026-09-23T21:19:47 |
| `C:\Users\Administrator\oi_enhancements\tests\test_capability_web.py` | 新增 | 2375 B | 2026-09-23T21:19:50 |

## 改动要点

### `prisir_work/capability.py`(只追加,不动现有函数)
在文件底部 search() 之后追加 2 个 register_capability 调用:
- `web.search` → endpoint `/web/search`, method POST, risk L0, auth True
- `web.fetch` → endpoint `/web/fetch`, method POST, risk L0, auth True

### `prisir_work/endpoints.py`(只追加,不动现有函数)
在文件底部 catalog() 之后追加 2 个 @register handler:
- `_web_search(body)` → 调用 `prisir_work.web_search.search(query, limit=10)`,空 query 返 `warning="empty_query"` + 空 list
- `_web_fetch(body)` → 调用 `prisir_work.web_fetch.fetch(url, options={"timeout": 10.0})`,空 url 返 `warning="empty_url"` + 空 content
- 任何异常兜底:200 + `warning=type(e).__name__`,**绝不 raise 给上层**

### `tests/test_capability_web.py`(新增)
8 个测试覆盖能力注册、关键词命中、endpoint 白名单、handler 空入参兜底、现有 capability 完整性。

## Test result

```
============================= test session starts =============================
platform win32 -- Python 3.12.9, pytest-8.3.4, pluggy-1.6.0
collecting ... collected 24 items

tests/test_capability_web.py::test_web_search_in_list PASSED             [  4%]
tests/test_capability_web.py::test_web_search_keyword_hit PASSED         [  8%]
tests/test_capability_web.py::test_web_fetch_keyword_hit PASSED          [ 12%]
tests/test_capability_web.py::test_english_keyword_hit PASSED            [ 16%]
tests/test_capability_web.py::test_endpoint_whitelist PASSED             [ 20%]
tests/test_capability_web.py::test_web_search_handler_empty PASSED       [ 25%]
tests/test_capability_web.py::test_web_fetch_handler_empty PASSED        [ 29%]
tests/test_capability_web.py::test_existing_capabilities_not_broken PASSED [ 33%]
tests/test_web_search.py::test_url_normalize PASSED                      [ 37%]
tests/test_web_search.py::test_empty_query PASSED                        [ 41%]
tests/test_web_search.py::test_no_providers PASSED                       [ 45%]
tests/test_web_search.py::test_rrf_fusion PASSED                         [ 50%]
tests/test_web_search.py::test_provider_failure_isolated PASSED          [ 54%]
tests/test_web_search.py::test_provider_timeout PASSED                   [ 58%]
tests/test_web_search.py::test_url_dedup PASSED                          [ 62%]
tests/test_web_search.py::test_lru_cache_hit PASSED                      [ 66%]
tests/test_web_fetch.py::test_cache_roundtrip PASSED                     [ 70%]
tests/test_web_fetch.py::test_cache_concurrent_put PASSED                [ 75%]
tests/test_web_fetch.py::test_fetch_uses_cache PASSED                    [ 79%]
tests/test_web_fetch.py::test_fetch_fallback_to_empty PASSED             [ 83%]
tests/test_web_fetch.py::test_fetch_picks_fastest PASSED                 [ 87%]
tests/test_web_fetch.py::test_url_normalize PASSED                       [ 91%]
tests/test_web_fetch.py::test_a11y_local_only PASSED                     [ 95%]
tests/test_web_fetch.py::test_disk_cache_path PASSED                     [100%]

============================= 24 passed in 1.01s ==============================
```

## 现有 10 个 capability 验真

`list_capabilities()` 输出(共 10 条,8 原有 + 2 新):

| id | risk | endpoint | title(前 50 字) |
|----|------|----------|------------------|
| plugins.load | L1 | /plugins/load | 加载能力包(声明式 plugin.json → 注册进门面,不执行任意代码) |
| system.health | L0 | /health | 探活:进程是否在线 + 版本 + 能力目录 |
| team.list | L0 | /team/list | 查 prisiragent 协作队列状态 |
| team.submit | L1 | /team/submit | 派单进 prisiragent 协作队列 |
| wallet.history | L0 | /wallet/history | 到账查账 |
| wallet.payto | L3 | /wallet/payto | 付款(两阶段:先构造 unsigned 待确认,确认+口令才签名广播) |
| wallet.receive | L0 | /wallet/receive | 生成收款地址 |
| wallet.status | L0 | /wallet/status | 查询钱包状态 |
| **web.fetch** | **L0** | **/web/fetch** | **web 抓取(多 fetcher 并发竞速 + 7d 本地缓存)** |
| **web.search** | **L0** | **/web/search** | **web 搜索(多源 rank fusion,免 API key 优先)** |

完整 endpoint 白名单(共 12 条):
```
/cap/execute         POST  L1  auth=True
/cap/search          POST  L0  auth=True
/health              GET   L0  auth=False
/plugins/load        POST  L1  auth=True
/team/list           POST  L0  auth=True
/team/submit         POST  L1  auth=True
/wallet/history      GET   L0  auth=True
/wallet/payto        POST  L3  auth=True
/wallet/receive      POST  L0  auth=True
/wallet/status       GET   L0  auth=True
/web/fetch           POST  L0  auth=True   ← 新
/web/search          POST  L0  auth=True   ← 新
```

## 约束遵守

- 改动文件精确 = 3 个(`prisir_work/capability.py` 改、`prisir_work/endpoints.py` 改、`tests/test_capability_web.py` 新增)
- 不动 `prisir_work/web_search.py` / `web_fetch.py` / `cache.py`(已完成 ship)
- 不动 `prisir_work/handlers.py` / `server.py`(已 working)
- 现有 `register_capability` / `register` 函数定义一个字符未动 — 只追加调用和 handler
- handler 真调底层 `_ws.search(query, limit=limit)` / `_wf.fetch(url, options={"timeout": 10.0})`,不 stub
- 任何异常兜底:200 + `warning=type(e).__name__`,绝不 raise

## Concerns

- 测试中 `test_existing_capabilities_not_broken` 需要导入 `prisir_work.handlers` 才能看到现有 8 个 capability(它们注册在 handlers.py 里,通过 `prisir_work.__main__` 才被加载)。test 文件顶部已 `from prisir_work import handlers  # noqa: F401` 确保注册被触发。这是测试侧的必要 import,不影响生产代码。
- `prisir_work.web_fetch.fetch()` 默认会真去抓 URL,所以测试只用空 query / 空 url 兜底路径,不真发请求。
- 本次改动没动 `prisir_work/__init__.py`,也不改 `__main__.py`(handlers 仍走原入口)。下次重启 prisir-work 进程,新 2 条端点 + 2 条 capability 自动生效。