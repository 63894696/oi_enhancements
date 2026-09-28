---
name: prisIr-skills-workbench-phase-3
description: Skills 工作台 Phase 3 — 两阶段 replan 闸门 ship(2026-09-28)
metadata:
  type: project
---

# Skills 工作台 Phase 3 ship(2026-09-28,commit `58e8902`)

承接 [[prisIr-skills-workbench-phase-1-7]] Phase 1.7 baseline 6/8 暴露的 P4 agency.* 盲区 + P5 video 犹豫;Phase 2 已 ship 走 tool_use 协议(commit `f78a64d`)。Phase 3 解**多 L1+ skill 任务的统一闸门**——LLM 二次调用 + 决策矩阵。

## 关键设计

**两阶段 replan 不是"LLM 自规划"**,而是「主对话结束后,异步旁路问 LLM:『用户这条想调哪些 skill?』」。

### replan LLM 二次调用
```
plan_skill_calls(user_text, plan_llm_call)
  → LLM 二次调用,输入 system=REPLAN_SYSTEM + user 模板(嵌入 skills_index)
  → 返 [{"skill_id": "X", "args": {...}}, ...]
  → parse_plan_response 容错 5 形态
  → 失败/超时/parse 错 → [] (fail-open,Phase 1 registry 兜底)
```

### `_should_replan` 决策矩阵(5 场景)
| 条件 | 弹卡? |
|------|------|
| `replan_enabled=False` | ❌ 全过(LLM 自己执行) |
| 空 `calls` | ❌ |
| 全 L0(只读) | ❌ |
| L1+ ≤ 阈值(默认 2) | ❌ auto_execute |
| L1+ > 阈值 | ✅ 弹 skill_plan_request 卡 |

`DEFAULT_AUTO_EXECUTE_L1_THRESHOLD = 2`。

### fail-open 三档
1. LLM 抛异常 → `[]` + `reason="empty_plan"`
2. LLM 超时(默认 8s)→ `[]` + `reason="empty_plan"`
3. JSON parse 失败 → `[]` + log warning

任何一档失败,**用户对话不卡**,降级进 Phase 1 registry,LLM 自己挑 skill。

### parse 容错 5 形态
1. `{"calls": [...]}` 标准
2. `{"calls": [{"arguments": ...}]}` arguments 别名
3. `{"plan": [...]}` plan 别名
4. `[...]` 直接 list
5. ` ```json ... ``` ` 包装

## 关键文件
| 文件 | 行数 | 用途 |
|------|------|------|
| `prisIr_work/skills/replan.py` | 270 | 闸门 + 二次调用 + parse + maybe_replan_and_execute |
| `tests/test_replan.py` | 263 | 8 测试 |

## 测试(8/8 绿)
- **#1 plan_skill_calls** — LLM 返 JSON → SkillCall list(risk 自动补)
- **#2 parse_plan_response** — 5 形态 + 3 失败路径
- **#3 plan_skill_calls fail** — 异常 + 超时 → []
- **#4 _should_replan** — 5 决策场景全对
- **#5 maybe_replan auto_execute** — 2 L0 calls 自动执行
- **#6 maybe_replan need_replan** — 3 L1+ → 弹卡回调触发
- **#7 maybe_replan empty_plan** — LLM 异常 → empty_plan
- **#8 replan_enabled=False** — need_replan=False, but still auto_executed

## Phase 3.5 TODO(待用户拍板)
- `companion/prisIragent-companion-web.py:build_messages` 加 `_maybe_inject_replan` 钩子(默认配置项 `skills.replan_enabled=true`)
- `ai_done` 后(1517 行附近)若 replan 需要确认,emit `skill_plan_request` ws 事件
- 前端复用 `capability_confirm_request` 卡片模板,新事件名让后端路由区分

## 关键复用
- `phase-2-shipped` tool_use 适配层(走 `protocol="auto"` 智能选)
- `phase-1-7-baseline` skills_index 实测
- `capability_confirm_request` ws 事件(同款 UI 卡片)
- `execute_skill(call, force=True)` 跳过 L1 confirm(因为 replan 总闸已决策)

## 累计测试
- handraw-style 29 + free-for-dev 26 + agency A 5 + Skills P1 9 + P1.5 6 + P1.6 6 + P1.7 8LLM + P2 9 + **P3 8** = **106 测试**

## 风险登记
1. **replan 二次调用延迟** — 每个用户任务 +1 LLM 调用、+1s 延迟、+$0.001,需评估
2. **replan LLM 选错 skill** — Phase 4 EXEC 兼容路径兜底;fail-open 让 LLM 自己挑
3. **skill_plan_request UI 卡片复用** — 跟 capability_confirm_request 共用卡片,前端 0 改动只是目标,实际可能要微调 CSS

**Why:** Phase 1.7 实测暴露 agency/video 类 skill 关键词不锐利,LLM 不调;Phase 3 用「plan LLM 看到全索引」+「自动跳过 L0」+「L1+ 弹卡」三档解决系统性失忆。
**How to apply:** 任何 L1+ skill 主导任务(agent 角色/视频处理/多平台发布)走 `maybe_replan_and_execute`;L0 单查询仍走 Phase 1 registry 直发。