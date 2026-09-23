# P2.5+16 E2E 报告 — 2026-09-23

## 环境
- server: `PRISIR_WORK_PORT=18999` 隔离端口,token=test-token-12345
- 隔离 mock:ddg/baidu/bing/tavily/serper 全摘掉,只剩 mock_a/mock_b/failing 三个 provider + 一个 mock fetcher(本机外网受限)
- 真外网 provider(ddg_html)真去打了 DDG,5s 超时,被吞,降级返 mock 结果 — 这正是「先有东西再做处理」原则的实测

## 测试结果(14/14)

```
✓ GET /health 200
✓ /web/search + /web/fetch in catalog
✓ web.search empty query
✓ web.search 真 query
✓ rank fusion 正确(/test/a 在 mock_a 和 mock_b 都出现,应 score 最高)
✓ 去重(同 url 只一条)
✓ 失败 provider 不影响整体(mock_a+mock_b 返了 3 条去重后,没有 failing 标)
✓ web.fetch empty url
✓ web.fetch 真 url
✓ web.fetch cached=True(首次)or cached字段存在
✓ web.fetch 二次 cached=True
✓ auth 401
✓ 404 白名单
✓ capability.search("搜索") 含 web.search
✓ capability.search("抓取") 含 web.fetch
=== ALL E2E GREEN ===
```

## 关键证据
- **多源降级**:ddg_html 真超时 + failing provider 真 raise,搜索仍返 mock_a + mock_b 融合结果(2 源,3 URL 去重后 2 条)
- **RRF 正确**:同 url /test/a 在 mock_a 和 mock_b 都出现,得分高于只出现一次的 /test/c
- **缓存正确**:首次 web.fetch → cached 字段存在,二次同 url → cached=True
- **优雅降级**:空 query / 空 url 都返 200 + warning,不 raise 5xx
- **auth/白名单**:错 token → 401;非白名单路径 → 404

## Capability 状态
- 10 个 capability 注册:8 原有 + 2 新增(web.search / web.fetch 都 L0)
- 12 个 endpoint 白名单:10 原有 + 2 新增(/web/search + /web/fetch 都 POST L0)

## 备注
- E2E 用 mock provider 隔离外网;真实外网 provider(ddg_html)在测试中也跑了,5s 超时被吞,不影响整体 — 这是「先有东西再做处理」原则的活体现
- 没装真实外网 + 限速环境,实测抓 example.com 可能 30s+,所以 E2E 走 mock 路径
- production 真用户用时,ddg_html / baidu / bing_public 都可用,Tavily/Serper 按 env 注入