# P2.5+18a E2E 报告 — 2026-09-23

## Ship 清单
- [x] `prisir_work/health.py`(新)— web 子系统诊断门面
  - `web_health()` 聚合 5 个子项:cache / fetchers / search_providers / endpoints_capabilities / tune
  - 每个子项 `ok/error/warnings`,整体 `ok` = AND
  - 任何异常 → `cache_error:<type>` 加 warnings,不抛
- [x] `prisir_work/endpoints.py` — 注册 `/web/health`(GET L0,免 token)
- [x] `prisir_work/handlers.py` — 扩展 `/health` 响应含 `web` 子项
- [x] `tests/test_health.py`(新)— 11 个单测
- [x] `_p2b18a_e2e.py`(新)— 19 个 E2E 检查

## 设计要点
- **免 token**:`/web/health` 与 `/health` 一致,免 token 用于容器探活 / 监控
- **tune 子项预埋**:无 tune.json 时 `tuned_domains=0` + note,P2.5+18b 写 tune.json 后此处自动显示
- **聚合层 try-catch**:任一子项异常不阻断其他 + 计入 warnings
- **失败 fetcher 警告**:`fetchers.count == 0` → `no_fetchers_registered`

## 测试结果

### 单测(11/11)
- cache 可写 / 写后 entry 数 / fixture 隔离
- fetcher 名单含 mock 标记 / 空名单
- provider 默认 5 个(ddg/baidu/bing + env 限定)
- endpoint/capability 列表含 web 套件
- tune 无文件 fallback
- web_health 聚合 / 无 fetcher 警告 / 异常安全

### E2E(19/19)
- A1-A11 /web/health 各子项 + checked_at
- B1 cache 写后再查 entries 数
- C1 /health 含 web 子项
- D1-D2 catalog + 免 token
- E1 无 fetcher 警告

### 回归(129/129)
- test_health 11 + test_agent 12 + test_crawl 14 + test_diff 12
- + test_cache_query 17 + test_web_fetch 8 + test_web_search 8
- + test_extract 13 + test_find_similar 12 + test_research 14
- + test_capability_web 8

## 端点状态
- 22 个 endpoint:21 原有 + 1 新增(/web/health,L0 免 token)

## 用法示例
```bash
# 探活(免 token)
curl http://127.0.0.1:18997/web/health

# /health 含 web 子项(已有能力一起返)
curl http://127.0.0.1:18997/health

# 输出示例(简化)
{
  "ok": true,
  "checked_at": "2026-09-23T15:30:00Z",
  "cache": {"ok": true, "path": "...", "entries": 42},
  "fetchers": {"ok": true, "count": 3, "names": ["http_urllib", "tls_impersonate", "headless_browser"], "has_mocks": false},
  "search_providers": {"ok": true, "count": 5, "names": ["ddg_html", "baidu", "bing_public", "tavily", "serper"]},
  "endpoints_capabilities": {
    "total_endpoints": 22, "total_capabilities": 11,
    "web_endpoints": ["/web/search", "/web/fetch", ...],
    "web_capabilities": ["web.search", "web.fetch", ...]
  },
  "tune": {"ok": true, "tuned_domains": 3, "domains_sample": ["github.com", "stackoverflow.com"]},
  "warnings": []
}
```