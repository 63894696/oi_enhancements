# P2.5+17d E2E 报告 — 2026-09-23

## Ship 清单
- [x] `prisir_work/agent.py`(新)— LLM 驱动自主采集
  - `agent(query, max_steps=6, llm_call=None, timeout=10.0)`
  - 循环 LLM 决策 search / fetch / extract / stop;无 LLM → 模板决策链
  - 累积 findings(URL → brief summary)供后续决策参考
  - 早停:LLM 返 stop / max_steps 耗尽
  - 失败降级:LLM 异常或坏 JSON → 模板 fallback(mode='template_fallback')
- [x] `prisir_work/endpoints.py` — 注册 `/web/agent`(POST L1)
- [x] `tests/test_agent.py`(新)— 12 个单测
- [x] `_p2b17d_e2e.py`(新)— 18 个 E2E 检查

## 设计要点
- **与 research 边界**:research 是「线性 plan→search×N→fetch→synthesize」;agent 是「循环 LLM 决策 action → 执行 → 累积 → 早停」
- **JSON 决策协议**:`{"action": "search"|"fetch"|"extract"|"stop", "args": {...}, "reason": "..."}`
- **findings 截断**:prompt 只喂最近 5 条 finding(避免超长)
- **Provider 限定**:`PRISIR_WEB_SEARCH_PROVIDERS` env 允许测试/E2E 限定 provider 走 mock(避免无外网环境超时)

## 过程 1 个性能 bug
- **症状**:agent test 跑 129s(web_search 默认走 ddg/baidu/bing 三个 provider,每个 8s 超时 × 多次调用)
- **根因**:测试没限定 provider,默认全跑真外网
- **修法**:
  1. agent.py 读 env `PRISIR_WEB_SEARCH_PROVIDERS`,空则 None(全跑)
  2. test_agent.py 顶部 `os.environ.setdefault('PRISIR_WEB_SEARCH_PROVIDERS', 'e2e_mock')`
- **效果**:12 tests 0.36s(原 129s)

## 测试结果

### 单测(12/12)
- 模板决策链(search → fetch → stop)+ 累积 findings
- LLM 决策 search / fetch / extract / stop + 累积 findings
- LLM 坏 JSON → fallback template_fallback
- LLM 异常 → fallback + warnings 含 llm_failed_step
- 边界:空 query / max_steps 截断 / findings 含 step 字段

### E2E(18/18)
- A1-A6 模板决策闭环 / mode / findings / answer / steps
- B1-B2 max_steps 截断 / 空 query
- C1-C2 catalog 注册 + auth 401
- D1-D3 module 直接调用 search+fetch 链路
- E1-E3 LLM 路径 / 坏 JSON fallback / 异常 fallback

### 回归(118/118)
- test_agent 12 + test_crawl 14 + test_diff 12 + test_cache_query 17
- + test_web_fetch 8 + test_web_search 8 + test_extract 13
- + test_find_similar 12 + test_research 14 + test_capability_web 8

## 端点状态
- 21 个 endpoint:20 原有 + 1 新增(/web/agent,L1 因自主决策可能多次抓取)

## 用法示例
```bash
# 模板决策(无 LLM)
curl -X POST http://127.0.0.1:18997/web/agent \
  -H "X-OI-Token: ..." \
  -d '{"query": "wigolo 替代品", "max_steps": 6}'

# Python 直接调(注入 LLM)
from prisir_work import agent
def my_llm(prompt: str) -> str:
    return call_my_llm(prompt)  # 返 JSON: {"action": ..., "args": ..., "reason": ...}
result = agent.agent("wigolo 替代品", max_steps=6, llm_call=my_llm)
print(result['answer'])
print(result['findings'])  # 全部决策历史
```

## 档 C 全收尾

| 任务 | 模块 | 端点 | E2E | 回归 | 状态 |
|---|---|---|---|---|---|
| P2.5+17a | cache_query.py | /web/cache/{list,stats,invalidate} | 24 | 80 | ✅ |
| P2.5+17b | diff.py | /web/diff | 19 | 92 | ✅ |
| P2.5+17c | crawl.py | /web/crawl (L1) | 21 | 106 | ✅ |
| P2.5+17d | agent.py | /web/agent (L1) | 18 | 118 | ✅ |

**wigolo 10 个工具对应**:search / fetch / extract / find_similar / research / watch / **cache** / **diff** / **crawl** / **agent** — **10/10 全覆盖**。

仅余 wigolo CLI 工具(doctor / verify / init / warmup / tune / skills)未对应,但属于运维工具不在能力门面范围。