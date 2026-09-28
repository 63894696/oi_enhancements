---
name: prisIr-phase-11-om-p3-pre-compose
description: Phase 11 OM-P3 Pre-compose 预算校验 ship(2026-09-28)
metadata:
  type: project
---

# Phase 11 OM-P3 Pre-compose 校验 ship(2026-09-28)

承接 [[prisIr-phase-10-om-p2-scoring]] + 用户「免费优先,单集 ≤ $0.10」拍板。

## 关键拍板(用户原话)
「我们之前定的预算会不会低了,尤其是现在在讲生成视频短剧这样的任务。」

**用户拍板**:**免费优先,单集 ≤ $0.10**(7 步 × ≤ $0.014/步)。

## OM-P3 ship 内容

### 1. 预算常量(`video_budget.py`)
| 常量 | 值 | 含义 |
|------|----|----|
| `DEFAULT_BUDGET_PER_EPISODE` | $0.10 | 单集硬上限(用户拍板) |
| `DEFAULT_COST_PER_STEP_HARD_CAP` | $0.014 | 单步硬上限(7 步 ÷ 预算) |
| `DEFAULT_STEPS_PER_EPISODE` | 7 | 60s 短剧典型步数 |

### 2. 关键 API
| 函数 | 用途 |
|------|------|
| `estimate_step_cost(step, provider_map)` | 单步成本(读 provider 的 cost_per_call) |
| `estimate_workflow_cost(steps, provider_map)` | 全 workflow 成本 + over_cap_count |
| `suggest_replacements(provider, tag)` | 同 tag 免费替代(quality 降序) |
| `check_budget(steps, provider_map, budget)` | 返 BudgetCheck(ok / exceeded / replace_plan) |

### 3. 关键文件
| 文件 | 改动 | 用途 |
|------|------|------|
| `prisIr_work/video_budget.py` | NEW ~220 行 | CostEstimate / BudgetCheck / check_budget + suggest_replacements |
| `prisIr_work/agent_video_workflow.py` | +60 行 | pre_compose hook + WorkflowResult.artifact + budget 参数 |
| `tests/test_phase_11_om_p3_pre_compose.py` | NEW ~270 行 | **14/14 测绿** |

## 关键设计

### 1. 失败 fail-fast(不消耗预算)
- 超预算 → workflow **不进入执行阶段**,返 `ok=False, error="budget_exceeded:..."`
- artifact 含完整 budget_check 结果 + replace_plan,前端弹卡用
- 用户可选「降级免费 / 加大预算 / 取消」三档

### 2. 替换建议(suggest_replacements)
- 自动找同 tag 免费 provider(quality 降序)
- 例:`kling` → `[local_wan]` / `piper` → `[]`(无需替换)
- 推荐但不强制:用户可在弹卡里选「就是用 veo3」加大预算

### 3. 0 破坏向后兼容
- 不传 `budget` 或 `pre_compose_check=True` → 默认拦截
- `pre_compose_check=False` → 跳过拦截,旧行为不变
- OM-P1 checkpoint 协议 + OM-P2 provider scoring 完全不破坏(Phase 9 14/14 仍绿)

### 4. 单步硬上限 vs 单集预算
- 区分:7 步平均 vs 单步超限
- 单步超(如 1 个 veo3 $0.10 > $0.014 硬上限)→ `over_cap_steps` 非空
- 但若总成本 ≤ budget(如 1 个 veo3 单集 $0.10 = $0.10)→ 仍算 ok
- 设计意图:防止「一个 veo3 单步吃掉 14 集预算」+ 给用户警告

## 测试矩阵(14/14 全绿)

### 估算层(7 项)
1. estimate_step_cost 免费 → $0
2. estimate_step_cost kling → $0.05
3. estimate_step_cost 未知 provider → $0(保守)
4. 全免费 7 步 → $0
5. 含 veo3 7 步 → $0.10,over_cap=1
6. suggest_replacements kling → [local_wan]
7. suggest_replacements piper(免费)→ []

### 校验层(4 项)
8. 全免费 → ok=True
9. 4 个付费 provider → ok=False, replace_plan 4 项
10. 空 steps → ok=False
11. 单 veo3 → 单步 over_cap 但 total = budget, ok=True

### 集成层(3 项)
12. run_workflow 超预算 → fail-fast + budget_check artifact
13. 全免费 run_workflow → 不被拦截,正常跑通
14. pre_compose_check=False → 跳过预算拦截

## 累计测试 **197**

Phase 10 183 + Phase 11 14 = 197

## 关键洞察

1. **用户拍板预算不是固定数** — 这是 2026-09-28 的拍板,未来跑多了可校准
2. **pre-compose ≠ runtime cost** — 这里是估算,真实成本可能因 provider 失败 retry 变高
3. **OM-P5 是预算治理**(实时计费 / 月度限额 / auto-degrade),OM-P3 是 **预检**
4. **用户可选升级** — 弹卡不只是「降级」,也可「加预算」(`run_workflow(budget=1.0)`)
5. **降级到免费 ≠ 一定 OK** — local_wan 需 GPU,可能本地不可用;这种情况走 fallback silent

## 与已有 ship 路径的关系

```
Skills 工作台(8 阶段 ship):
  P0-P8 ✓
视频 OpenMontage 借鉴:
  P0  决策 ✓
  P1  checkpoint ✓(Phase 9)
  P2  provider scoring ✓(Phase 10)
  P3  pre-compose 校验 ✓(本 commit)
  P4  免费资源真集成 pending
  P5  成本预算治理 pending(实时计费)
多 agent OpenAI Agents SDK 借鉴:
  P1  handoff schema ✓(Phase 9)
  P2-P6 pending
```

## 风险登记

1. **估算 ≠ 实际成本** — provider 失败 retry / API 涨价 / quota 限制都可能让真实成本高过估算
2. **local_wan 需 GPU** — 降级建议里 local_wan 可能本地不可用,需 fallback 到 local_silence 之类
3. **用户硬改 provider** — 即使弹卡拒绝,用户仍可能手动改 dsl,需 UI 二次校验
4. **月预算未实现** — OM-P3 只算单集,月聚合留 OM-P5

**How to apply:**
- 用户问「跑视频多少钱」→ `estimate_workflow_cost(steps)` 返总价
- 用户问「超预算怎么办」→ `check_budget()` 返 replace_plan,前端弹卡
- 用户问「免费 provider 质量够吗」→ 看 `replace_plan` 第一项的 quality score(OM-P2 评分)
- 用户问「每集预算能不能调」→ `run_workflow(budget=0.5)` 临时加大
- 用户问「月总成本」→ OM-P5 ship 后接月聚合,目前只算单集