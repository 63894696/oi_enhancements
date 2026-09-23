# P2.5+16f Task #639 — find_similar 相似 URL 发现 ship 报告

## 落盘文件 + mtime
- `prisir_work/find_similar.py`  mtime 1790172526  (新, 184 lines)
- `prisir_work/endpoints.py`     mtime 1790172542  (改, 追加 /web/find_similar handler)
- `prisir_work/capability.py`    mtime 1790172544  (改, 追加 web.find_similar capability)
- `tests/test_find_similar.py`   mtime 1790172579  (新, 12 tests)

## pytest 全绿(6 文件, 63 tests)

```
tests/test_find_similar.py    12 passed
tests/test_extract.py         13 passed
tests/test_research.py        14 passed
tests/test_capability_web.py   8 passed
tests/test_web_search.py       8 passed
tests/test_web_fetch.py        8 passed
============================= 63 passed in 1.25s ==============================
```

## 现有 capability 验真
- `web.extract` ✓
- `web.fetch` ✓
- `web.find_similar` ✓ (新增)
- `web.research` ✓
- `web.search` ✓
- 端点 `/web/extract /web/fetch /web/find_similar /web/research /web/search` 全在白名单
- 原 capability.py/endpoints.py 已有 register/register_capability 函数一字不动

## 设计要点
- 多源:web_search(全部已注册 provider RRF)+ 可选 Serper /google.serper.dev/related
- 任一源失败 / 超时 / raise → 吞,warnings 透出;全失败 ok=True similar=[]
- 同 host 上限 3 条,score 排序,top max_results
- 关键词抽取:title/h1/h2/meta description → 英文 [a-z]{3,}+ 中文 [一-鿿]{2,4} + 停用词过滤
- URL fallback:无内容时从 path 最后一段抽
- 零外部依赖(Serper 走 urllib.request stdlib)