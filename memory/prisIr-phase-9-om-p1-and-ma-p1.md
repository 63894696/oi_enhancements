---
name: prisIr-phase-9-om-p1-and-ma-p1
description: Phase 9 OM-P1(checkpoint)+ MA-P1(handoff schema)双线 ship(2026-09-28)
metadata:
  type: project
---

# Phase 9 OM-P1 + MA-P1 ship(2026-09-28)

承接 [[prisIr-openmontage-recon]] + 用户「按最小 agent 团队设计」决策。

## 双线 ship

### OM-P1:video checkpoint 协议(借鉴 OpenMontage)
**Commit `8653940`** — 4 文件 +1125 行,**14/14 测绿**

新文件:
- `prisIr_work/video_checkpoint.py` — CheckpointManager(atomic 写 / LRU 清理 / load_step / resume_from)
- `prisIr_work/agent_handoff.py` — HandoffPayload / HandoffCaps / accept/reject

改造:
- `prisIr_work/agent_video_workflow.py:run_workflow` 加可选参数 `workflow_id` / `resume`
  - 不传 → 旧行为,**向后兼容**
  - 传 wf_id → 每 step 落 checkpoint
  - resume=True → 跳过 status=='ok' 的 step,从失败 step 续跑

### MA-P1:multi-agent handoff schema(借鉴 OpenAI Agents SDK)

借鉴 5 设计模式(本 phase ship 1,余 4 留 P2-P6):

| 模式 | Phase | 状态 |
|------|------|------|
| **handoffs 原语** | P1 | ✅ |
| **input guardrail** | P2-B | pending |
| **output guardrail** | P3-B | pending |
| **input_filter**(交接上下文过滤)| P4-B | pending |
| **handoff caps + escalate to human** | P5-B | pending |

### 最小 agent 团队示例(已 ship 可用)

```
Triage Agent (用户对接)
  ├─ Creative Agent (编剧 + 分镜)
  ├─ Art Agent (美术 + 角色卡持久)
  ├─ Audio Agent (配音 + BGM)
  ├─ Edit Agent (合成 + 剪辑)
  └─ Ops Agent (发布 + 反馈回收)
```

链式 handoff 范式:
```python
task_id = new_task_id()
trace_id = new_trace_id()
# Triage → Creative
p1 = HandoffPayload(task_id, trace_id, "做个 60 秒短片",
                     from_agent="Triage", to_agent="Creative",
                     pending_steps=["script", "shot_breakdown"])
# Creative 接受后转 Art(链式)
p2 = HandoffPayload(task_id, trace_id, p1.original_goal,
                     from_agent="Creative", to_agent="Art",
                     current_state="script 完成",
                     completed_steps=["script"],
                     pending_steps=["shot_breakdown", "keyframe_render"],
                     accumulated_context={"script": "..."})
r = accept_handoff(p1, new_payload=p2)  # Creative accept + 转交
```

## 关键设计

### OM-P1 — Checkpoint 协议
- **不存二进制**(视频/图片/音频)— 只存路径 + metadata
- **不存 LLM 完整响应** — 只存摘要 + 关键 ID
- **atomic 写**:tempfile + os.replace,失败不留半写文件
- **LRU 默认 20**,可调
- **失败 step 也落** `status='failed'`,但 resume 时不当作 done

### MA-P1 — Handoff Schema
- **task_id** workflow 级唯一 + **trace_id** 跨 agent 链路唯一
- **ACCEPTED/REJECTED 显式表态**,reject 必须填 reason
- **handoff caps** 默认 5,超限抛 HandoffCapExceeded(escalate to human)
- **超时** 默认 60s,is_expired() 可判定
- **链式交接**:accept_handoff(p, new_payload=p2) 一并转交

## 测试矩阵

### OM-P1(6/6 全绿)
1. CheckpointManager save/load 闭环
2. atomic 写无残留
3. resume_from 跳过 status='ok' 的 step,failed 不算 done
4. cleanup_lru(keep=20) 删 5 留 20
5. workflow 集成(checkpoint 落盘 + resume + 向后兼容)
6. 不传 wf_id 旧行为不变

### MA-P1(8/8 全绿)
1. HandoffPayload 默认值齐全
2. to_dict/from_dict JSON 闭环
3. accept/reject(reason 必填,空 reason 抛 ValueError)
4. is_expired 超时判定
5. HandoffCaps 计数 + 超 cap 抛 HandoffCapExceeded
6. reset(单 trace / 全部)
7. task_id/trace_id 50 次循环无重复
8. accept 附 new_payload 链式 handoff(Triage→Creative→Art)

## 累计测试 **170**

PRIO 156 + P9 OM-P1 6 + P9 MA-P1 8 = 170

## 关键文件
| 文件 | 改动 | 用途 |
|------|------|------|
| `prisIr_work/video_checkpoint.py` | NEW ~200 行 | CheckpointManager(借鉴 OpenMontage)|
| `prisIr_work/agent_handoff.py` | NEW ~150 行 | HandoffPayload / HandoffCaps |
| `prisIr_work/agent_video_workflow.py` | +60 行 | workflow 集成 checkpoint + resume |
| `tests/test_phase_9_om_p1_and_ma_p1.py` | NEW ~250 行 | 14 测试 |

## 与已有 ship 路径的关系

```
Skills 工作台(8 阶段 ship):
  Phase 0 — 架构设计 ✓
  Phase 1 — skill registry ✓
  Phase 1.5/1.6/1.7 ✓
  Phase 2 — tool_use 协议 ✓
  Phase 3/3.5/4/5 ✓
  Phase 6 — 主面板 ✓
  Phase 7 — 紧凑化 ✓
  Phase 8 — tier 分层 ✓
视频 OpenMontage 借鉴:
  P0  决策 ✓
  P1  checkpoint ✓(本 commit)
  P2-P5 pending
多 agent OpenAI Agents SDK 借鉴:
  P1  handoff schema ✓(本 commit)
  P2-P6 pending
```

## 风险登记
1. **checkpoint 文件膨胀** — LRU 默认 20,可调
2. **不存二进制** — 只存路径,需保证路径稳定
3. **handoff caps=5** — 借鉴 OpenAI Agents SDK 默认,真实场景需用户校准
4. **现有 workflow 未传 wf_id** — 走旧行为,不影响生产

**How to apply:**
- 用户问「workflow 失败怎么恢复」→ `cm = CheckpointManager(); cm.resume_from(wf_id, step_ids)`
- 用户问「多 agent 怎么交接」→ 用 `HandoffPayload` + `accept_handoff`/`reject_handoff`
- 用户问「handoff 死循环」→ `HandoffCaps(cap=5)` 超 cap 抛 `HandoffCapExceeded`,走 escalate-to-human
- 用户问「单集短片链路」→ OM-P1 ship 后已稳定(失败从最近 checkpoint 恢复);P2-P5 ship 后全自动
- 用户问「何时用多 agent」→ 单集 60 秒短片**不用**,多集/多语言/客户定制才上 Triage+团队

## NTFS 大小写坑(本 commit 踩到)
git 索引里小写 `prisir_work/`,NTFS 折叠 `prisIr_work`/`prisir_work`。
第一次 `git add prisIr_work/video_checkpoint.py` 没 add 上(跟 Phase 8 同坑)。
**修正**:小写路径 `git add prisir_work/video_checkpoint.py`。
详见 [[prisIr-skills-workbench-phase-8]] 备忘。