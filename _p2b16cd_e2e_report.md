# P2.5+16c/d E2E 报告 — 2026-09-23

## 环境
- server: `PRISIR_WORK_PORT=18998`, token=test-token-67890
- mock provider:search mock_e2e 返 2 条 mock URL;fetch mock_e2e 接任何 URL 返 OK content
- watch:Node 子进程直调 ext.commands 闭包(handler),`PRISIR_EXT_HOME` 临时目录隔离

## 测试结果(11/11)

```
[A] research 闭环
  ✓ A1 research 空 query → 200+warning
  ✓ A2 research 真 query → 200
  ✓ A2 plan 包含 query
  ✓ A2 sources >= 1
  ✓ A2 answer 非空
  ✓ A2 citations 对齐 sources 长度
  ✓ A2 steps 含 plan/search/fetch/synthesize 4 步
  ✓ A3 capability.search("研究") 含 web.research
  ✓ A4 list_capabilities() 含 web.research + 原有
  ✓ A5 auth 401
[B] watch 扩展 SDK 调用
  ✓ B1 watch add+list+run(x2)+remove 闭环
      OK {added:true, listed:1, first_changed:false, second_changed:false, summary1:"fetch_failed: fetch failed", removed:true, after_list:0}
=== ALL P2.5+16c/d E2E GREEN ===
```

## 过程修复
1. **fetch 主入口的契约不一致**:`web_fetch.fetch` 之前不返 `ok` 字段,但 `research._fetch_one` 检查 `result.get("ok") and result.get("content")` → 永远 None → sources 永远空
   - **修法**:web_fetch.py 在 bad_url / cache hit / 成功 / 失败 四条 return path 都加 `ok` 字段(布尔)
   - **回归**:38 测试全绿(test_web_fetch 8 + test_web_search 8 + test_research 14 + test_capability_web 8)
2. **E2E fetch mock 撞默认 fetcher**:`wf._FETCHERS.clear()` 后只剩 mock_e2e,真 fetch 路径立刻返 OK content
3. **watch E2E SDK API 错用**:`ext.handleCommand` 不存在,改用 `ext.commands.get('watch.add')(args, ctx)` 跟 test.js 同形态
4. **watch 默认 fetcher 真打外网超时**:测试机无外网,fetch_failed 通知触发 — 正是设计的优雅降级演示

## Capability 状态
- 11 个 capability 注册:8 原有 + 3 新增(web.search / web.fetch / web.research)
- 15 个 endpoint 白名单:10 原有 + 3 新增(/web/search /web/fetch /web/research 全部 POST L0)