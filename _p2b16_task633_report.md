# Task #633 完成报告 — P2.5+16 web_search.py

## 落盘
- `prisir_work/web_search.py` 19968 bytes mtime 21:16
- `tests/test_web_search.py` 10215 bytes mtime 21:15

## 测试
```
============================== 8 passed in 0.82s ==============================
tests/test_web_search.py::test_url_normalize PASSED                      [ 12%]
tests/test_web_search.py::test_empty_query PASSED                        [ 25%]
tests/test_web_search.py::test_no_providers PASSED                       [ 37%]
tests/test_web_search.py::test_rrf_fusion PASSED                         [ 50%]
tests/test_web_search.py::test_provider_failure_isolated PASSED          [ 62%]
tests/test_web_search.py::test_provider_timeout PASSED                   [ 75%]
tests/test_web_search.py::test_url_dedup PASSED                          [ 87%]
tests/test_web_search.py::test_lru_cache_hit PASSED                      [100%]
```

## 设计要点
- 多 provider:ddg_html / baidu / bing_public(免 key,urllib+re 抓 HTML);tavily / serper(env 有 key 才注册)
- RRF:score = Σ 1/(k+rank),k=60,URL 规范化去重(strip fragment + lowercase host + strip trailing slash)
- 并发:ThreadPoolExecutor(max_workers=min(8, len(providers))),每个 provider timeout=8s,异常/超时全吞
- 内存 LRU:max 32,ttl 300s,key=md5(query+limit+sorted(providers))
- CLI:python -m prisir_work.web_search "query"
- 零外部依赖