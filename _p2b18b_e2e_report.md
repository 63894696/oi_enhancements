# P2.5+18b E2E 报告 — 2026-09-23

## Ship 清单
- [x] `prisir_work/tune.py`(新)— per-domain fetcher 优先级学习
  - 累加器 `(host, fetcher) → [ok, fail, ms_sum, ms_count]`
  - 阈值:MIN_SAMPLES=3 / MIN_OK_RATIO=0.8 / MS_BEST_MARGIN=0.20
  - tune.json 路径 = `cache_dir().parent / "tune.json"`(与 health._tune_health 对齐)
  - 原子写:tempfile + os.replace
  - MAX_HOSTS=200 LRU 淘汰
  - CLI:stats / recommend / flush / reset
- [x] `prisir_work/web_fetch.py` — 集成 tune
  - fetch 前:查 host learned → 命中只跑 learned 列表(learned 列表全失效回退全并发)
  - fetch 后:record 每个跑完的 fetcher(r is None → 跳过,避免 break 后未完成 fetcher 误记 fail)
  - flush_if_ready() 触发落盘
- [x] `prisir_work/endpoints.py` — 3 个端点
  - /web/tune/recommend(L0) — 查 host learned
  - /web/tune/stats(L0) — 累加器 + tune.json 调试视图
  - /web/tune/flush(L1) — 手动触发
- [x] `prisir_work/capability.py` — 3 个能力注册
  - web.tune.recommend / web.tune.stats / web.tune.flush
- [x] `tests/test_tune.py`(新)— 19 个单测
- [x] `_p2b18b_e2e.py`(新)— 24 个 E2E 检查

## 设计要点

### 学习门
- **样本门槛** MIN_SAMPLES=3:小样本(冷启动)不污染 tune.json
- **质量门槛** MIN_OK_RATIO=0.8:某 fetcher ok 率 ≥ 80% 才考虑
- **优势门槛** MS_BEST_MARGIN=0.20:与第一名差距 ≥ 20% 才纳入,差距小的视为同档都跑

### 集成策略
- **读路径**:fetch 入口查 recommend(host) → 命中 → fetchers = learned 子集(不命中空列表回退全并发)
- **写路径**:并发循环结束后 record 每个 *跑完* 的 fetcher;None = 未完成,跳过(关键坑:否则 break 后未完成 fetcher 误记 fail)
- **持久化**:flush_if_ready 每次 fetch 完顺手触发,无开销(累加器空 → noop)

### 路径对齐
- tune.tune_path() = `cache_dir().parent / "tune.json"`
- health._tune_health() 用同路径
- 测试 monkeypatch `_CACHE_DIR_OVERRIDE` 后,两个模块都跟着迁(无需额外同步)

## 测试结果

### 单测(19/19)
- 路径对齐 / 缺失文件
- 累加器递增 / fail 分离 / 坏输入跳过
- recommend 无数据 / 空 host
- flush 阈值(< MIN_SAMPLES / < OK_RATIO 不写)
- flush 选最快 / 接近都纳入 / 改写时更新
- tune.json 腐蚀恢复 / 坏格式
- reset + snapshot
- web_fetch 集成:用直接 record 喂数据(突破 MIN_SAMPLES 确定性)+ recommend 命中跳过非 listed + record 真的会被调

### E2E(24/24)
- A1-A3 catalog 含 3 个端点
- B1-B4 /web/tune/recommend(抽取 host / hint / 空 url)
- C1-C4 /web/tune/stats 字段齐
- D1-D4 tune 累加器:fast 胜出 + noisy 不进 + 落盘
- E1-E2 /web/tune/flush 端点
- F1-F3 /web/health tune 子项显示 learned
- G1-G2 web_fetch 命中 learned → 只调 listed
- H1-H2 无文件 / 坏文件兜底

### 回归(148/148)
- 19 tune + 11 health + 12 agent + 14 crawl + 12 diff
- + 17 cache_query + 8 web_fetch + 8 web_search
- + 13 extract + 12 find_similar + 14 research + 8 capability_web

## 端点状态
- 25 个 endpoint:22 原有(#18a 后) + 3 新增(/web/tune/recommend/stats/flush)

## 用法示例
```bash
# 查 host 是否有 learned
curl -X POST http://127.0.0.1:18997/web/tune/recommend \
  -H "X-OI-Token: $TOK" -H "Content-Type: application/json" \
  -d '{"url":"https://github.com/torvalds/linux"}'
# {"ok":true,"host":"github.com","recommended":["fast"],"hint":null}

# 看累加器 + tune.json
curl -X POST http://127.0.0.1:18997/web/tune/stats -H "X-OI-Token: $TOK"

# 手动 flush(确认卡)
curl -X POST http://127.0.0.1:18997/web/tune/flush -H "X-OI-Token: $TOK"
```

## 已知边界
- **首次访问慢**:冷启动第一次访问某 host 走全并发,3 次后才开始学
- **跨进程不共享**:tune.json 是文件,但累加器是进程内;重启后从 tune.json 读推荐,但累加器从零
- **fetcher 全失效回退**:learned 列表里的 fetcher 在新进程中没了 → 静默回退全并发
- **不可重入锁**:`_lock` 用 threading.Lock(单线程没问题);并发场景需 RLock