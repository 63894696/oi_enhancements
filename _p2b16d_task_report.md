# P2.5+16d Task Report — prisir_work/research.py 多步研究门面

## 落盘文件 + mtime

| 文件 | 状态 | mtime |
|---|---|---|
| `C:\Users\Administrator\oi_enhancements\prisir_work\research.py` | 新 | 2026-09-23 21:49:46 |
| `C:\Users\Administrator\oi_enhancements\prisir_work\endpoints.py` | 改(追加 handler) | 2026-09-23 21:49:53 |
| `C:\Users\Administrator\oi_enhancements\prisir_work\capability.py` | 改(追加 register_capability) | 2026-09-23 21:49:56 |
| `C:\Users\Administrator\oi_enhancements\tests\test_research.py` | 新 | 2026-09-23 21:51:27 |

## pytest 全绿输出(4 文件,38 用例)

```
============================= test session starts =============================
platform win32 -- Python 3.12.9, pytest-8.3.4, pluggy-1.6.0
rootdir: C:\Users\Administrator\oi_enhancements
collected 38 items

tests/test_research.py::test_plan_default PASSED                         [  2%]
tests/test_research.py::test_plan_with_llm_mock PASSED                   [  5%]
tests/test_research.py::test_plan_with_llm_bad_json_falls_back PASSED    [  7%]
tests/test_research.py::test_plan_empty PASSED                           [ 10%]
tests/test_research.py::test_search_failure_isolated PASSED              [ 13%]
tests/test_research.py::test_fetch_failure_skips_url PASSED              [ 15%]
tests/test_research.py::test_synthesize_llm_failure_degrades PASSED      [ 18%]
tests/test_research.py::test_synthesize_no_llm_provided PASSED            [ 21%]
tests/test_research.py::test_citations_match_sources PASSED              [ 23%]
tests/test_research.py::test_empty_query PASSED                          [ 26%]
tests/test_research.py::test_capability_registered PASSED                [ 28%]
tests/test_research.py::test_endpoint_whitelist PASSED                   [ 31%]
tests/test_research.py::test_endpoint_handler_empty PASSED               [ 34%]
tests/test_research.py::test_existing_capabilities_not_broken PASSED     [ 36%]
tests/test_capability_web.py::test_web_search_in_list PASSED             [ 39%]
tests/test_capability_web.py::test_web_search_keyword_hit PASSED         [ 42%]
tests/test_capability_web.py::test_web_fetch_keyword_hit PASSED          [ 44%]
tests/test_capability_web.py::test_english_keyword_hit PASSED            [ 47%]
tests/test_capability_web.py::test_endpoint_whitelist PASSED             [ 50%]
tests/test_capability_web.py::test_web_search_handler_empty PASSED       [ 52%]
tests/test_capability_web.py::test_web_fetch_handler_empty PASSED       [ 55%]
tests/test_capability_web.py::test_existing_capabilities_not_broken PASSED [ 57%]
tests/test_web_search.py::test_url_normalize PASSED                      [ 60%]
tests/test_web_search.py::test_empty_query PASSED                        [ 63%]
tests/test_web_search.py::test_no_providers PASSED                       [ 65%]
tests/test_web_search.py::test_rrf_fusion PASSED                         [ 68%]
tests/test_web_search.py::test_provider_failure_isolated PASSED          [ 71%]
tests/test_web_search.py::test_provider_timeout PASSED                  [ 73%]
tests/test_web_search.py::test_url_dedup PASSED                          [ 76%]
tests/test_web_search.py::test_lru_cache_hit PASSED                      [ 78%]
tests/test_web_fetch.py::test_cache_roundtrip PASSED                     [ 81%]
tests/test_web_fetch.py::test_cache_concurrent_put PASSED                [ 84%]
tests/test_web_fetch.py::test_fetch_uses_cache PASSED                    [ 86%]
tests/test_web_fetch.py::test_fetch_fallback_to_empty PASSED             [ 89%]
tests/test_web_fetch.py::test_fetch_picks_fastest PASSED                 [ 92%]
tests/test_web_fetch.py::test_url_normalize PASSED                       [ 94%]
tests/test_web_fetch.py::test_a11y_local_only PASSED                     [ 97%]
tests/test_web_fetch.py::test_disk_cache_path PASSED                     [100%]

============================= 38 passed in 1.09s ==============================
```

## 现有 capability 验真(未动)

- `system.health` — 在(原有)
- `wallet.status` — 在(原有)
- `team.submit` — 在(原有)
- `web.search` — 在(原有,P2.5+16)
- `web.fetch` — 在(原有,P2.5+16)
- `web.research` — **新加**(P2.5+16d)

测试 `test_existing_capabilities_not_broken` 显式断言上述 6 个 id 全部命中,验证函数体一字未动,仅追加。

## 关键实现说明

1. **`plan_queries(query, llm_call=None)`**: LLM 优先(JSON 数组正则提取 + 容错),失败降级到模板拼接(原 query + 中文变体 + "最新 2026" + "是什么 what is"),保序去重。
2. **`_search_one` / `_fetch_one`**: 内部函数,任何异常 → 吞掉,降级返 `[]` / `None`。
3. **`research()`**: 4 步流水线 `plan → search → fetch → synthesize`,每步记录 duration_ms / 计数到 `steps` 列表;失败必加 `warnings` 字符串。
4. **synthesize 降级**: LLM 返空 / raise → 拼接 snippets + "[n] 标题 (url)" 模板;无 sources → "无任何可用素材" + `no_sources` warning。
5. **endpoint handler**: 顶层再加一层 try/except,任何异常 → 200 + ok=True + warning=异常类型名(双保险)。
6. **零外部依赖**: 仅 stdlib(`logging`/`time`/`concurrent.futures`/`json`/`re`),生产代码 `import` web_search/web_fetch 在函数内(动态,避免循环 import)。

## 已 ship 的依赖(本次只读)

- `prisir_work/web_search.py` — 未改
- `prisir_work/web_fetch.py` — 未改
- `prisir_work/cache.py` — 未改
- `prisir_work/handlers.py` — 未改(P2.5+16 现有 capability 通过它隐式注册,test_capability_web 仍绿)

## 已知小坑(已修)

1. **`research` 模块 vs 函数同名**: `from prisir_work import research` 把模块绑到 `research`,不能直接 `research(...)`。测试改用 `from prisir_work.research import research as research_fn`。
2. **`mf.return_value = lambda ...` 是反模式**: `return_value` 整体替换,函数体内若调用 `_fetch_one(u, t)` 拿到的就是 lambda 本体而不是 dict。改 `mf.side_effect = lambda u, t: {...}` 才对(每次调用都重新评估)。
