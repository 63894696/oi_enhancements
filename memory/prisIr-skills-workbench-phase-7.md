---
name: prisIr-skills-workbench-phase-7
description: Skills 工作台 Phase 7 — skills_index 紧凑化 + 默认配置全开 ship(2026-09-28)
metadata:
  type: project
---

# Skills 工作台 Phase 7 ship(2026-09-28)

承接 [[prisIr-skills-workbench-phase-6]] 用户决策:

> 「我想还是改成所有技能给LLM看得到,我们可以优化去掉重复技能,但不能让LLM不知道都有哪些技能,
> 我们接受等待时间和成本,但是确保了任务质量和降低可能的返工概率。」

## 关键决策
**保留全量 69 项**(LLM 看得到所有能力)+ **接受 replan 成本/延迟** + **接受 system prompt 体积**。
不去重(endpoint 0 重复,53 项 web 是不同能力如 click/eval/fill/scroll,合理分群)。

## 优化(只针对字符,不针对能力)

### 1. `describe_registry_compact()` 标准紧凑版
- 去 `emoji` 字段(LLM 不需要视觉锚,从 id 就够了)
- `name` 截断 24 字(原 80,过长 LLM 看不完)+ `...`
- `tags` 上限 4(原 6)
- `backend=builtin` 不输出(默认 = 全 builtin)
- **12882c → 7993c(-38%)**

### 2. `describe_registry_compact(ultra=True)` 极致紧凑版
- 字段名短化:schema_version→v, total→n, skills→s, id→i, name→n, risk→r, tags→t
- **12882c → 7281c(-43.5%)**
- 默认 False,标准紧凑是默认(LLM 看到完整字段名更易理解)

### 3. 默认配置全开
- 主面板 `PRISIRAI_SKILLS_REPLAN` 默认 `0` → `1`(自动跑 replan,弹规划卡)
- 主面板 `PRISIRAI_SKILLS_INDEX` 保持 `1` 默认开
- companion `skills_replan_enabled` `False` → `True`
- companion `skills_index_enabled` `False` → `True`
- 用户想关就手动设 `PRISIRAI_SKILLS_REPLAN=0`(主面板)/ 改源码 `False`(companion)

## 关键发现

### 重复度扫描结果(为什么不去重)
- 0 endpoint 重名
- 53 项 `web.*` 同 namespace 但 endpoint 都不同(click/eval/fill/scroll/...)
- 9 项 `video.*` 同 namespace 也是分群(asr/bgm/burn/create/...)
- 共享 tag 的 9 个是分类标签(如 `publish` 跨 4 平台合理)
- **真冗余 = 0**,不存在可合并实现

### token 真实节省
| 模式 | 字符 | token(估 0.7c/token) | 节省比 |
|------|------|---------------------|--------|
| 原(全字段) | 12882 | ~18403 | 基准 |
| 标准紧凑 | 7993 | ~11418 | **-38%** |
| ultra 紧凑 | 7281 | ~10401 | -43.5% |

## 测试(7/7 绿)+ 回归 9/9 绿

### Phase 7 自身测试
- **#1** 标准紧凑 7000-8500c
- **#2** ultra ≤7500c
- **#3** standard 节省 ≥35%(实测 38%)
- **#4** ultra 节省 ≥40%(实测 43.5%)
- **#5** 保留 schema_version + total + id/name/risk/tags 5 字段
- **#6** emoji 字段 + 字符都不存在
- **#7** 默认配置变更到位(主面板 + companion)

### Phase 6 回归:8/8 绿
### Phase 1 skills_registry:9/9 绿

## 关键文件
| 文件 | 改动 | 用途 |
|------|------|------|
| `prisIr_work/skills/registry.py` | +40 行 | `_skill_to_compact` helper + ultra=True 字段名短化版 |
| `prisIragent_web.py` | ±2 行(注释+默认值) | `PRISIRAI_SKILLS_REPLAN` 默认 `1` |
| `companion/prisIragent-companion-web.py` | ±4 行(注释+配置) | `skills_index_enabled/replan_enabled` 改 `True` |
| `tests/test_phase_7_compact_and_default.py` | +180 行(NEW) | 7 测试 |

## 累计测试 **148**
PRIO 89 + P1 9 + P1.5 6 + P1.6 6 + P1.7 8LLM + P2 9 + P3 8 + P3.5 6 + P4 5 + P5 8 + P6 8 + **P7 7**

## 8 阶段 ship 路径总览
```
Phase 0 — 架构设计 ✓
Phase 1 — skill registry + lazy loader ✓
Phase 1.5 — build_messages 接入 ✓
Phase 1.6 — 4 类 capability 补齐 ✓
Phase 1.7 — LLM 准确率实测 ✓
Phase 2 — tool_use 协议适配 ✓
Phase 3 — 两阶段 replan 闸门 ✓
Phase 3.5 — 接入 build_messages + ws 事件 ✓
Phase 4 — EXEC ↔ tool_use 兼容 ✓
Phase 5 — 前端渲染 + 后端 confirm(companion) ✓
Phase 6 — 主面板集成 ✓(commit a7830eb)
Phase 7 — 紧凑化 + 默认全开 ✓(2026-09-28)
```

## 风险登记
1. **name 截断可能丢关键信息** — 24 字 + `...`,LLM 看不到完整人话。**mitigation**:用户说"LLM 不知道都有哪些技能",但具体人话不重要(描述在 `describe_skill()` 里按需加载)。标准模式用 24 字已测试 8 case 通过(Phase 1.7 baseline 6/8 hit)。
2. **replan LLM 误判弹卡频率** — 默认全开后每次多 1s 延迟 + $0.001 成本,**L1+ 任务都弹规划卡**(用户先 ack 才执行)。**mitigation**:threshold=2 控制 ≤2 自动执行不弹;用户想关就 env 改 0。
3. **字段名短化(ultra)可读性下降** — `i/n/r/t` 不直观,只在用户主动选 ultra=True 才生效。默认 standard 仍用完整字段名。
4. **69 项索引 + 紧凑化 7993c** 仍占主对话 system 约 8K chars,长对话 history 折叠时需注意(主面板已有 `_apply_compact` 兜底)。

**How to apply:**
- 想完全关:`PRISIRAI_SKILLS_REPLAN=0` 设环境变量(主面板)或改源码 `"skills_replan_enabled": False`(companion)
- 想更省 token:把 `_shell_system_prompt` 调 `skills_index_block(ultra=True)`(默认 standard)
- 想看 LLM 真用了哪些 skill:查 `desc.tool_use` log(Phase 2 已 ship)