---
name: prisIr-skills-workbench-docs
description: Skills 工作台配置文档 + ship 总览 文档 ship(2026-09-28)
metadata:
  type: project
---

# Skills 工作台文档 ship(2026-09-28)

承接 [[prisIr-skills-workbench-phase-7]] 后用户决策:
> 「很好,那么还要写配置说明文档吗?还有 tool_use 协议适配层到位了没有?」
> → 「直接写」

## ship 文件

### `docs/prisir-skills-workbench-config.md`(主文档,~280 行)
覆盖 9 章节:
1. **是什么 / 为什么** — 核心思路 + 与 [[prisIr-skills-workbench-phase-7]] 决策呼应
2. **配置总览** — 11 开关 × 2 入口(env 主面板 vs 源码 companion)
3. **风险等级** — L0/L1/L2/L3 行为矩阵 + `auto_execute_l1_threshold=2` 详解
4. **fail-open 降级** — 6 降级点 + 设计取舍(用户问题让工作台承担,工作台不能让用户承担)
5. **token 经济性** — 12882c / 7993c / 7281c / 0 三档对比 + 何时用哪档
6. **灰度切换** — 协议决策树 + 3 mode + 完全退化方法
7. **调试 / 日志** — `desc.tool_use` 日志 + 队列上限 + replan 失败排查
8. **风险登记** — 5 风险 × 缓解
9. **关联阅读** — shipped 总览 + 架构设计 + memory

### `docs/prisir-skills-workbench-shipped.md`(总览文档,~200 行)
覆盖:
- **8 阶段时间线** — 12 commit hash + 测试数 + 一句话标题
- **各阶段文件清单** — 每阶段 ship 的所有文件 + 用途
- **累计测试 148** — 拆解到各 phase 通过率
- **复用设施 0 行修改** — 7 个老设施复用方式
- **8 阶段关键决策回顾** — 每阶段 1 句话设计取舍

## 关键设计决策

### 两层配置入口(11 开关)
**主面板**用 env vars — 改完 `setx` 重启终端即可,不动源码。
**语伴 companion**用源码 dict — 改完重启语伴,不留 env 文件污染系统。
**默认值在 `integration.py:DEFAULT_*`** — 用户 0 配置就能跑通,显式 override 才生效。

### fail-open 全程
工作台设计的**核心哲学**:工作台是"加速器",主对话是"必须跑"。工作台 fail 时**静默降级**,不打断用户对话。
例:
- replan LLM 失败 → 进 Phase 1 registry 由主对话 LLM 自己挑 skill
- tool_use adapter 解析失败 → `calls=[]` loop 自然结束
- 队列满 32 上限 → 丢最老的,推新的(日志 warn)

### token 经济性表(关键数据)
| 模式 | 字符 | token | 节省 |
|------|------|-------|------|
| 老 system 全 capability | 12000 | 17143 | 基准 |
| standard 紧凑 | **7993** | 11418 | **-38%** |
| ultra 紧凑 | 7281 | 10401 | -43.5% |
| 禁用索引 | 0 | 0 | -100% |

**关键洞察**:69 项索引虽占 8000c,但 LLM 不需再学 EXEC 语法(省 500c/turn 教学),且 capability 数量线性扩展时 system prompt **不再 O(N) 增长**(结构性收益)。

## 风险登记
1. **replan LLM 误判频弹卡** — 默认开 + L1+ 都走 replan → mitigation `auto_execute_l1_threshold=2`(≤2 自动执行)
2. **tool_use 协议碎片化** — 不同 LLM 厂商协议差异 → 隔离在 `tool_use/` adapter 层,新增协议加 `_parse_xxx.py`
3. **69 项 + 长对话 history 超窗口** — 主面板已有 `_apply_compact` 兜底;索引满了需关

**How to apply:**
- 用户问「工作台怎么配 / 怎么关 / 怎么省 token」→ 答 `docs/prisir-skills-workbench-config.md`
- 用户问「ship 历史 / commit hash」→ 答 `docs/prisir-skills-workbench-shipped.md`
- 用户问「token 经济性 / 节省比」→ 答配置文档 §5
- 用户问「灰度切换 / 协议选择」→ 答配置文档 §6
- 用户想完全退化回 v2.3 → 配置文档 §6.3