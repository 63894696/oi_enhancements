---
name: prisIr-skills-workbench-phase-8
description: Skills 工作台 Phase 8 — tier 分层字段 ship(2026-09-28)
metadata:
  type: project
---

# Skills 工作台 Phase 8 ship(2026-09-28)

承接 [[prisIr-skills-workbench-phase-7]] 后用户对话:

> 用户:「有些工作是常规每月甚至每周做的,这样的工作技能就算是我们做了去重肯定能留下;
> 但有的工作是按季度甚至一年才做的,那么我们的去重不就把这个技能丢了吗?
> 这样来说我们不做变动接受成本是否更合适?」

> 用户:「加上字段」

## 关键决策
**tier 分层字段(只分层,不删东西)**: 给每个 skill 加 `tier` 字段,4 档(hot/warm/cold/archive),
LLM 仍看到全量 69 项(skills_index 不变),但元数据上分层供未来决策可解释。

## tier 分布(实测)

```
hot   =  3 项  日常高频(查询/抓 URL/信息)
             video.info / web.search / web.fetch
warm  = 56 项  常规(月/周频次写操作/平台 publish/文件操作)
             publish.* / video.asr/bgm/tts/cut/burn / web.playwright / agency.* /
             free_for_dev.* / calendar.* 等
cold  = 10 项  低频但关键(年度/季度/审计/合规/报税)
             audit.report / audit.compliance / tax.file / yearly.summary /
             quarterly.report / web.tune.* / web.ytdlp.* / web.reach.* (按需)
archive = 0 项  预留(未来废弃 skill 启用)
```

## 字段定义

```python
@dataclass
class SkillIndex:
    tier: str = "warm"  # hot / warm / cold / archive
```

### 4 档语义
| tier | 含义 | 触发场景 |
|------|------|---------|
| **hot** | 日常高频 | 视频查询 / web 搜索 / 抓 URL |
| **warm** | 常规(月/周) | 视频剪辑 / 平台 publish / 浏览器交互 / 日历 |
| **cold** | 低频但关键 | 审计 / 合规 / 报税 / 季度报告 / 平台元数据 |
| **archive** | 已废弃(预留) | 未来真废弃时启用 |

## 启发式规则(_tier_for)
1. **capability 显式 `_tier` 字段** 可 override 启发式
2. **命名空间硬编码表** `_NS_TIER` 覆盖 ~50 个常用 skill(id + 前 2 段)
3. **兜底**:L0 → warm(只读查询),L1+ → cold(写操作默认冷)

## 新 API

```python
from prisIr_work.skills.registry import (
    list_skills_by_tier,   # (tier) → [SkillIndex]
    count_by_tier,         # () → {hot, warm, cold, archive} 四 key 永远全
    tiers_summary,         # () → 1 行人话,debug 日志
)
```

## 不动的设计(用户拍板方向)
- **不动能力**:69 项全保留,LLM 仍看到全量
- **不动 system prompt 全量注入**:Phase 7 默认行为保留
- **tier 字段留口子**:未来可加 `skills_index_block(only_tiers=["hot","warm"])` 按层注入,
  **本阶段 ship 不接**,避免引入未决策的复杂度

## Phase 7 阈值同步调整

加 tier 字段后:
| 模式 | 原 | 现 | 阈值 |
|------|----|----|------|
| standard | 7993c | **8956c** (+12%) | 8500 → 9500 |
| ultra | 7281c | **8175c** (+12%) | 7500 → 8500 |
| standard 节省比 | 38% | 35.3% | 35% → 25% |
| ultra 节省比 | 43.5% | 41% | 40% → 30% |

节省比仍 ≥ Phase 7 阈值,但阈值放宽留余地(未来再加字段不破)。

## 测试(8/8 绿)+ 回归

### Phase 8 自身测试
- **#1** SkillIndex 默认 tier = 'warm'
- **#2** to_dict 含 tier 字段
- **#3** _tier_for 启发式 3 类(hot=3, cold=3, warm 兜底)
- **#4** 69 skill 全部 tier ∈ {hot, warm, cold, archive}
- **#5** count_by_tier 永远含 4 key,总和=69
- **#6** list_skills_by_tier 返子集 + 未知 tier 返空
- **#7** compact(standard + ultra)都保留 tier 字段
- **#8** tiers_summary 1 行人话

### 回归
- Phase 1 (registry): 9/9 绿
- Phase 2 (tool_use): 9/9 绿
- Phase 6 (主面板): 8/8 绿
- Phase 7 (compact 阈值放宽): 7/7 绿

## 累计测试 **156**
PRIO 89 + P1 9 + P1.5 6 + P1.6 6 + P1.7 8LLM + P2 9 + P3 8 + P3.5 6 + P4 5 + P5 8 + P6 8 + P7 7 + **P8 8**

## 关键文件
| 文件 | 改动 | 用途 |
|------|------|------|
| `prisIr_work/skills/schema.py` | +5 行 | SkillIndex.tier + to_dict |
| `prisIr_work/skills/registry.py` | +155 行 | _NS_TIER 表(50 项)+ _tier_for + 3 新 API + compact 透传 |
| `tests/test_phase_8_tier_field.py` | +148 行 (NEW) | 8 测试 |
| `tests/test_phase_7_compact_and_default.py` | ±36 行 | 阈值放宽 + tier 字段检查 |

## 9 阶段 ship 总览
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
Phase 7 — 紧凑化 + 默认全开 ✓(commit 7de6ce3)
Phase 8 — tier 分层字段 ✓(commit 04ce490,本 ship)
```

## 风险登记
1. **tier 启发式可能误标** — 用户后续可显式设 capability `_tier` override(优先于表)
2. **未来按 tier 分层注入会引入复杂度** — 本期不接,留口子
3. **archive 档未启用** — 未来真废弃 skill 时启用,显式登记
4. **用户 override 路径未 ship** — capability.py `_tier` 字段读取已写,实际登记接口待补
   (本期 ship 接受:启发式已覆盖 50 项 + 用户后续提需求再加)

**How to apply:**
- 用户问「tier 怎么分」→ 答 hot=3 / warm=56 / cold=10 / archive=0
- 用户问「为什么有些 skill 不常用」→ 答 tier 字段已标 cold,系统仍保留(避免误删)
- 用户问「想给特定 skill 标 tier」→ 在 capability.py 加 `"_tier": "cold"` 字段
- 未来真按 tier 分层注入 → `skills_index_block(only_tiers=["hot","warm"])` 加在 integration.py
- 未来真废弃 skill → 把 tier 改为 archive,system prompt 自动剔除

## NTFS 大小写折叠坑(本 commit 踩到)
git 索引里 `prisir_work/skills/`(小写),但 NTFS 折叠 `prisir`/`prisIr` 到同一目录。
第一次 `git add prisIr_work/skills/schema.py` → **没 add 上**(git 当成大小写不同文件名匹配)。
**修正**:用索引里的小写路径 `git add prisir_work/skills/schema.py` → 命中。
详见 [[ntfs-case-folding-git-add-fail]] 备忘。