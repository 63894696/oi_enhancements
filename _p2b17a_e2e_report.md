# P2.5+17a E2E 报告 — 2026-09-23

## Ship 清单
- [x] `prisir_work/cache_query.py`(新)— 3 函数门面
  - `cache_list(host, limit, include_expired)` — 列已缓存条目
  - `cache_invalidate(url, host, all_expired)` — 强制失效
  - `cache_stats()` — 总数/大小/按 host 聚合 Top10/过期数
- [x] `prisir_work/endpoints.py` — 注册 3 个端点(POST L0/L1)
  - `/web/cache/list`(L0)— list 查询
  - `/web/cache/stats`(L0)— stats 查询
  - `/web/cache/invalidate`(L1)— invalidate 删条目(L1 因有副作用)
- [x] `tests/test_cache_query.py`(新)— 17 个单测
- [x] `_p2b17a_e2e.py`(新)— 24 个 E2E 检查

## 设计决策
- **复用零改动**:`prisir_work/cache.py` 一字不动,门面只读文件+ glob
- **失败降级**:任何 IO/解析异常 → 返空 list / 0 deleted / 0 stats,绝不抛
- **monkeypatch 友好**:`_cache._CACHE_DIR_OVERRIDE` 已是 cache.py 公开的测试钩子,测试 + E2E 都用它隔离到 tmpdir
- **损坏文件跳过**:坏 JSON 不影响其他条目(_read_record 返 None → continue)
- **风险等级**:`/web/cache/invalidate` 标 L1(有写副作用,删文件),其他 L0

## 测试结果

### 单测(17/17)
- list 空目录/返回已种子/host 过滤/host 大小写不敏感/limit 截断/排除过期/include_expired/排序
- invalidate 无参 noop/url 精确/host 模糊/all_expired/不存在 noop
- stats 空目录/按 host 聚合/统计过期数
- 损坏文件跳过

### E2E(24/24)
- A1-A3 list 字段完整 + host 过滤 + limit 截断
- B1 stats 总数/大小/by_host/cache_dir
- C1-C5 invalidate url 精确/host 模糊/noop/不存在/全删空
- D1-D2 all_expired 路径
- E1-E4 端点注册 + auth 401

### 回归(80/80)
- test_cache_query 17 + test_web_fetch 8 + test_web_search 8 + test_extract 13
- + test_find_similar 12 + test_research 14 + test_capability_web 8

## 端点状态
- 18 个 endpoint:15 原有 + 3 新增(cache/*)
- 11 capability(无新 capability,cache 走 endpoint 即可)

## 用法示例
```bash
# 列最近 20 条
curl -X POST http://127.0.0.1:18997/web/cache/list \
  -H "X-OI-Token: ..." -d '{}'

# 只看 github.com 的缓存
curl -X POST http://127.0.0.1:18997/web/cache/list \
  -H "X-OI-Token: ..." -d '{"host": "github.com"}'

# 统计
curl -X POST http://127.0.0.1:18997/web/cache/stats \
  -H "X-OI-Token: ..." -d '{}'

# 失效某 URL
curl -X POST http://127.0.0.1:18997/web/cache/invalidate \
  -H "X-OI-Token: ..." -d '{"url": "https://github.com/foo"}'

# 清整个 host
curl -X POST http://127.0.0.1:18997/web/cache/invalidate \
  -H "X-OI-Token: ..." -d '{"host": "github.com"}'

# 清所有过期
curl -X POST http://127.0.0.1:18997/web/cache/invalidate \
  -H "X-OI-Token: ..." -d '{"all_expired": true}'
```