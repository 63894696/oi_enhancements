# P2.5+17b E2E 报告 — 2026-09-23

## Ship 清单
- [x] `prisir_work/diff.py`(新)— 页面版本对比门面
  - `diff(url_a, url_b, *, mode='url'|'time', raw=False, snapshot_b=True)`
  - mode='url': 两 URL 内容对比
  - mode='time': 同 URL 两时间快照对比(a 来自 cache / b 实时,绕 cache)
- [x] `prisir_work/web_fetch.py`(改)— 加 `options.no_cache` 选项,绕过 mem+disk cache
- [x] `prisir_work/endpoints.py` — 注册 `/web/diff`(POST L0)
- [x] `tests/test_diff.py`(新)— 12 个单测
- [x] `_p2b17b_e2e.py`(新)— 19 个 E2E 检查

## 过程两个 ship bug + 一个测试 bug

### 1. diff `_unified_lines` 用 `lineterm=""` + `splitlines()` → summary 全 0
- **症状**:A1 diff 非空但 `summary.added_lines=0`
- **根因**:difflib `unified_diff` 在 lineterm="" 且输入行无 `\n` 结尾时,把多行变更压成一行;后续 `splitlines()` 切到 1 行 → `_summary` 数 `+` `-` 时全部落在首行(已被 `---` 排除),剩余 0
- **修法**:`_unified_lines` 主动给每个 yield 行补 `\n`(若没有);输入行也加 `\n`
- **文件**:`prisir_work/diff.py:55-75`
- **副作用**:之前整个 diff 文本是不可解析的(unified diff 标准要求每行独立),已修

### 2. diff mode='time' 用 `web_fetch.fetch` 命中 cache → 永远 unchanged
- **症状**:`test_diff_time_mode_with_cache_history` 用 cache 存 Old,fetch 返 New,但 fetch 优先 cache → b = Old,unchanged
- **根因**:web_fetch 查 cache(mem+disk)优先,fetcher/mock 不参与
- **修法**:web_fetch.fetch 加 `options.no_cache=True` 选项,跳过两层 cache
- **文件**:`prisir_work/web_fetch.py:283-320`
- **扩散**:research/extract/find_similar 都不传 no_cache → 行为不变(向后兼容)

### 3. cache 隔离不足 → 测试间互相污染
- **症状**:不同测试用相同 URL,fetcher mock 注入但内容来自 cache
- **修法**:test_diff 用 `@pytest.fixture(autouse=True)` 隔离 tmpdir + 清 `_MEM_CACHE`
- **文件**:`tests/test_diff.py:7-21`
- **复用模式**:跟 `test_cache_query.py` 的 `_setup_tmp_cache` 套路一致

## 测试结果

### 单测(12/12)
- mode='url' 同内容/不同内容/strip HTML/raw 比原文
- mode='time' 有历史 cache 命中/no cache 自动 snapshot/no snapshot
- 失败兜底 fetch 失败/空 url/无效 mode
- summary 字段完整性

### E2E(19/19)
- A1-A4 url mode 差异检测 + 空 url
- B1 mode='time' cache+live 双源
- C1-C3 raw + catalog + auth
- D1 summary 字段

### 回归(92/92)
- test_diff 12 + test_cache_query 17 + test_web_fetch 8 + test_web_search 8
- + test_extract 13 + test_find_similar 12 + test_research 14 + test_capability_web 8

## 端点状态
- 19 个 endpoint:18 原有 + 1 新增(/web/diff)

## 用法示例
```bash
# 两 URL 对比(strip HTML 后行级 diff)
curl -X POST http://127.0.0.1:18997/web/diff \
  -H "X-OI-Token: ..." \
  -d '{"url_a": "https://a.example/v1", "url_b": "https://a.example/v2"}'

# 同 URL 两时间快照(a 来自 cache, b 实时绕 cache)
curl -X POST http://127.0.0.1:18997/web/diff \
  -H "X-OI-Token: ..." \
  -d '{"url": "https://watched.example/page", "mode": "time"}'

# raw 模式(比原文 HTML,不做 strip)
curl -X POST http://127.0.0.1:18997/web/diff \
  -H "X-OI-Token: ..." \
  -d '{"url_a": "x", "url_b": "y", "raw": true}'
```