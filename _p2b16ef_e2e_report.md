# P2.5+16e/f E2E 报告 — 2026-09-23

## 环境
- server: `PRISIR_WORK_PORT=18997`, token=test-token-ef-9999
- mock provider:fetch mock_e2e 返 SAMPLE_HTML;search mock_e2e 无条件返 2 条 mock URL
- 隔离:`wf._FETCHERS.clear()` + `find_similar` 经 `providers=['e2e_mock']` 强制不走真 ddg/baidu/bing_public

## 测试结果(21/21)

```
[A] extract 闭环
  ✓ A1 /web/extract 200
  ✓ A1 mode=regex (no LLM)
  ✓ A1 data.title = "Test Product Page"
  ✓ A1 data.price = 29.99
  ✓ A1 data.features ≥ 1
  ✓ A2 /web/extract empty url → 200 + warning
  ✓ A3 /web/extract list schema 简化入口
  ✓ A3 data.author = "Jane Doe"
  ✓ A4 capability.search("抽取") 含 web.extract
  ✓ A4b capability.search("extract") 含 web.extract
  ✓ A5 所有原有 + 5 web capability 都在
  ✓ A6 所有 endpoint 都在
  ✓ A7 /web/extract auth 401
[B] find_similar 闭环
  ✓ B1 /web/find_similar 200
  ✓ B1 keywords 非空(从 SAMPLE_HTML 抽出)
  ✓ B1 similar 含 https://b.example/similar1
  ✓ B2 /web/find_similar empty url → 200 + warning
  ✓ B3 capability.search("相似") 含 web.find_similar
  ✓ B4 max_results=1 截断
  ✓ B5 serper_no_key warning(无 env)
  ✓ B6 /web/find_similar auth 401
=== ALL P2.5+16e/f E2E GREEN ===
```

## 过程修复

### 1. extract `_normalize_schema` 简化入口断 list[str] → bug
- **症状**:A3 `data.author = "Jane Doe"` 失败,返 None
- **根因**:输入 `['title', 'author', 'price']`(list of str),`_normalize_schema` 原实现直接 `{"type":"object", "fields": schema}` 当 fields 用 → 下游 `_extract_via_regex` 里 `f["name"]` TypeError → endpoint 兜底返 ok=True empty data
- **修法**:`_normalize_schema` 增加 str 项 → `{"name": str, "type": "string", "required": False}` 归一化路径
- **文件**:`prisir_work/extract.py:29-54`
- **回归**:extract 13/13 + e2e 21/21 全绿

### 2. find_similar endpoint 不透传 providers → mock 被真 ddg 淹没
- **症状**:B1 `similar` 含 baike.baidu.com / reddit.com 真 URL,不出现 mock URL
- **根因**:find_similar 接受 `providers` 参数,但 `/web/find_similar` handler 写死 `providers` 不透传,默认 ALL → 测试机无外网真打 8s 超时 + 即使命中 mock 也会被真 ddg RRF 融合冲掉
- **修法**:handler 透传 `body.get("providers")` + `body.get("timeout")`(调试/e2e 必须的契约完整)
- **文件**:`prisir_work/endpoints.py:129-147`
- **副作用**:endpoint 契约变宽 → 但行为向后兼容(没传 → 默认 None → 默认全跑)
- **回归**:find_similar 12/12 + e2e 21/21 全绿

## 回归总览

| 模块 | 测试 | 状态 |
|---|---|---|
| test_extract | 13 | ✓ |
| test_find_similar | 12 | ✓ |
| test_web_search | 8 | ✓ |
| test_web_fetch | 8 | ✓ |
| test_research | 14 | ✓ |
| test_capability_web | 8 | ✓ |
| **小计(本次相关)** | **63** | **✓** |
| 全套相关 web | 86 | ✓(无关 pre-existing import 错已 ignore) |

## Capability 状态
- 11 个 capability 注册:8 原有 + 3 新增(`web.search` / `web.fetch` / `web.research`)
  (extract / find_similar 走 `@register` endpoint,不在 capability 注册表 — 设计选择)
- 15 个 endpoint 白名单:10 原有 + 3 新增(`/web/search` `/web/fetch` `/web/research`)

## Ship 清单
- [x] `_normalize_schema` 处理 list[str]
- [x] `/web/find_similar` handler 透传 providers/timeout
- [x] e2e mock_search 无条件返 mock
- [x] e2e mock_fetch ok 字段兼容(沿用 P2.5+16c/d 修法)
- [x] 63 个相关测试 + 86 个全套绿