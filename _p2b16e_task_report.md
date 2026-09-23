# P2.5+16e task #638 Report — prisir_work/extract.py

**Date**: 2026-09-23
**Branch**: master
**Task**: 建 prisir_work/extract.py JSON Schema 结构化抽取(LLM 可选,失败降级 regex)

## 落盘文件 + mtime

| 文件 | 状态 | mtime (UTC) |
|------|------|-------------|
| `C:\Users\Administrator\oi_enhancements\prisir_work\extract.py` | 新建 | 2026-09-23 22:02:42 (1790172162) |
| `C:\Users\Administrator\oi_enhancements\prisir_work\endpoints.py` | 追加 handler | 2026-09-23 22:01:59 (1790172119) |
| `C:\Users\Administrator\oi_enhancements\prisir_work\capability.py` | 追加 capability | 2026-09-23 22:02:01 (1790172121) |
| `C:\Users\Administrator\oi_enhancements\tests\test_extract.py` | 新建(13 测试) | 2026-09-23 22:02:11 (1790172131) |

## pytest 全绿(5 文件,51 测试)

```
$ python -m pytest tests/test_extract.py tests/test_research.py tests/test_capability_web.py tests/test_web_search.py tests/test_web_fetch.py -v
...
============================= 51 passed in 1.29s ==============================
```

文件分布:
- tests/test_extract.py — **13 个绿**(本任务新增)
- tests/test_research.py — 13 个绿(无回归)
- tests/test_capability_web.py — 8 个绿(无回归)
- tests/test_web_search.py — 8 个绿(无回归)
- tests/test_web_fetch.py — 9 个绿(无回归)

## 现有 capability 验真

通过 `capability.list_capabilities()` 校验,5 个核心能力(含 web.extract)在册:

| id | title 摘要 | endpoint | risk |
|----|------------|----------|------|
| system.health | (原有) | /health | L0 |
| web.search | 多源 rank fusion web 搜索 | /web/search | L0 |
| web.fetch | 多 fetcher 并发竞速抓取 | /web/fetch | L0 |
| web.research | 多步研究 plan→search→fetch→LLM | /web/research | L0 |
| **web.extract** | **JSON Schema 结构化抽取** | **/web/extract** | **L0** |

`endpoints.catalog()` 包含 `/web/extract` 路径白名单;`system.health` 通过任意现有调用验证仍可达(handlers import 隐式触发现有 capability 注册且测试 `test_existing_capabilities_not_broken` 覆盖)。

## 设计实现摘要

**extract.py**:
- `_normalize_schema()`:三态入口(list / dict-with-fields / dict-with-properties)
- `_extract_via_llm()`:可选 LLM 调用,失败 → None
- `_extract_via_regex()`:字段类型启发式(title/desc/author/keyword/list/number/integer/boolean)
- `extract()`:fetch → LLM(optional) → regex → merge 主链路,任意环节失败降级,warnings 透出

**失败降级原则**(用户拍板「信息来源途径越多越好」):
- fetch 失败 → `warnings=['fetch_failed']`,data={}, ok=True
- LLM 失败 → 降级 regex,warnings 加 `llm_failed`
- regex 启发式失败 → 字段返 None / []
- 顶层 `try/except` 兜底,绝不抛

**关键判定**:测试 `test_extract_via_regex_basic` 期望 `rating_count=247`,实际文本含 `29.99` 与 `247`。初始实现 `_INT_RE.search()` 命中首个整数 `29`,改为 `_INT_RE.findall()` 取最大值后命中 `247`(count 类指标取最大更合理)。

## 改动约束遵守

- 未碰 prisir_work/research.py / web_search.py / web_fetch.py / cache.py
- 现有 `register_capability` / `register` 函数一字未动
- 仅在 endpoints.py / capability.py 尾部追加
- 零外部依赖(纯标准库:json/logging/re/time/typing)
