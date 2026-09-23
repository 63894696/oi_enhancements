# P2.5+17c E2E 报告 — 2026-09-23

## Ship 清单
- [x] `prisir_work/crawl.py`(新)— BFS 全站爬虫
  - `crawl(start_url, *, max_pages=30, max_depth=2, same_host=True, respect_robots=True, rate_per_host=1.0)`
  - BFS 同 host 抓取,robots.txt 尊重(走 web_fetch,E2E 可 mock),限速
  - 自动去重 + 过滤 `#anchor`/`javascript:`/`mailto:`
  - 跳过 off_host / depth_exceeded / robots_disallowed / fetch_failed
- [x] `prisir_work/endpoints.py` — 注册 `/web/crawl`(POST L1,有外部流量 + cache 写入)
- [x] `tests/test_crawl.py`(新)— 14 个单测
- [x] `_p2b17c_e2e.py`(新)— 21 个 E2E 检查

## 过程两个 ship bug

### 1. robots.txt 不走 web_fetch mock → E2E 无法验证
- **症状**:`_robots_allowed` 直接 urllib 读 robots,绕开 `_FETCHERS` 注册表 → 测试用 mock fetcher 注册了但 robots 仍走真→ 默认可访问
- **修法**:`_load_robots_parser` 优先 `web_fetch.fetch`(可 mock)→ fallback raw urllib → 失败返 None(视为允许)
- **文件**:`prisir_work/crawl.py:55-100, 188-220`

### 2. robots 缓存错按 host 一次性布尔,所有 path 用同一结果
- **症状**:`robots_cache[host] = bool` → 同一 host 第一页 robots OK → 后续页(即使被 robots 拒)也放行
- **根因**:缓存了 `can_fetch(ua, path)` 的 bool 结果,不是 parser 实例
- **修法**:改为缓存 `RobotFileParser | None`,每次用 `rp.can_fetch(ua, parsed.path)` 现判
- **文件**:`prisir_work/crawl.py:138-160`

## 测试结果

### 单测(14/14)
- BFS 起点优先/同 host/深度限制/max_pages 截断
- 外部链接进 skipped
- 过滤 #anchor / javascript: / mailto:
- 无重复抓取
- 失败兜底(空 URL / 无效 URL)
- 元数据字段齐全 + title 提取
- 限速生效(2 页 ≥ 1s)

### E2E(21/21)
- A1-A8 同 host BFS + 跨 host 跳过 + 无重复
- B1-B3 参数截断/深度=0/空 URL
- C1-C3 title + depth + size + catalog + auth
- D1-D2 robots.txt Disallow 生效 / 其他路径仍可抓

### 回归(106/106)
- test_crawl 14 + test_diff 12 + test_cache_query 17 + test_web_fetch 8
- + test_web_search 8 + test_extract 13 + test_find_similar 12
- + test_research 14 + test_capability_web 8

## 端点状态
- 20 个 endpoint:19 原有 + 1 新增(/web/crawl,L1 因外部流量)

## 用法示例
```bash
# 抓取同 host 前 30 页,深度 2
curl -X POST http://127.0.0.1:18997/web/crawl \
  -H "X-OI-Token: ..." \
  -d '{"url": "https://example.com/", "max_pages": 30, "max_depth": 2}'

# 不限制 host
curl -X POST http://127.0.0.1:18997/web/crawl \
  -H "X-OI-Token: ..." \
  -d '{"url": "https://hub.example/", "same_host": false, "max_depth": 1}'

# 关闭 robots(应急测试)
curl -X POST http://127.0.0.1:18997/web/crawl \
  -H "X-OI-Token: ..." \
  -d '{"url": "https://x.example/", "respect_robots": false}'

# 高速(限速=0)
curl -X POST http://127.0.0.1:18997/web/crawl \
  -H "X-OI-Token: ..." \
  -d '{"url": "https://x.example/", "rate_per_host": 0}'
```