# 免费模型能力摸底 · 题库设计 v1（2026-11-01）

> 目标：为 Prisir「监督者-工作者」模式建第一张表——哪些平台的免费模型可以当 worker，哪些只能兜底，哪些不可用。
> 方法完全对齐 babelspan 已建立的评审方法论（dim_meta 单一来源 / 四锚点 / verbatim 证据 / 配对统计判定），不另起炉灶。
> 项目：`D:\Projects\prisir-model-lab`（待建）。

---

## 0. 摸底要回答的问题

对每个候选免费模型，回答三行：

| 问题 | 判定方式 |
|---|---|
| worker 能不能用？（中文质量/指令遵循/工具调用/长文） | L1 快速筛选 |
| 论文解读 / 书籍评分这类专业任务行不行？ | L2 交叉审核 |
| 分档结论 | 可用 / 仅兜底 / 不可用 |

## 1. 复用 babelspan 的现成资产（已核实现状）

| 资产 | 位置 | 复用方式 |
|---|---|---|
| 维度单一来源 | `apps/scoreboard/data/dim_meta.json`（31 维，按体裁分家族） | L2 评分卡直接引用，不复制 |
| 四锚点取样 | 派单协议 D10：开篇/~25%/~75%/收束 | L2 长文任务的取样法 |
| 证据规则 | D14：verbatim quote + locus + why_score ≥150 字 | L2 评分卡的证据要求 |
| 覆盖四档 | D13：discover/board/close/top | L2 任务分级 |
| **统计判定规则** | `2026-10-31-pair-rubric-round1.md`：\|Δ\|≤2 且 SD≤2 → 判"不可并入" | **直接照搬** |
| **噪声基线** | 单套 rubric 运行噪声 SD≈2.4（非虚构重跑实测）；差值噪声 SD≈√2×2.4≈3.4 | L2 样本量设计的依据 |
| **重跑教训** | "每套只跑一次测不出小效应，n=12 时 SD 估计有 ±20% 误差" | **L1/L2 一律每模型每任务 ≥2 次重跑** |
| 超限处理 | pair-rubric 的 `too_long` 标记（不误导） | runner 的错误分类 |
| 27B 实测文档 | `docs/research/2026-10-31-27b-*.md` 系列 | supervisor 能力基线 + 出题参考 |

## 2. 题库结构（两层）

### L1 快速筛选（每模型约 40 次调用，成本≈零）

四类任务 × 每类 5 题 × 2 次重跑。**全部客观判定**（关键词/JSON schema/位置回忆），零 LLM 裁判成本：

| 任务类 | 测什么（对应 worker 硬门槛） | 判定 |
|---|---|---|
| 中文质量 | 翻译 2 题 + 摘要 1 题 + 文风改写 2 题 | 关键词命中率 + 禁用词检查 |
| 指令遵循 | 结构化输出、约束组合、拒绝指令、列表格式 | 程序判定（schema 校验）+ 正则 |
| 工具调用 | 函数选择、参数填充、多步顺序、权限守卫 | 程序判定（解析 function/arguments JSON） |
| 长文 | 8k 长文 needle 位置回忆、跨段定位 | 精确匹配 |

**分档规则**：四类全过 → `可用`；只过 1-2 类 → `仅兜底`；全挂 → `不可用`。

**首跑发现（2026-11-01）**：免费模型**单函数调用可以、多步编排不行**（多步题大量空输出）→ worker 只能执行单步，编排必须留在 supervisor/executor 层。这是直接改 gateway 设计的数据。

### L2 交叉审核（**复用 aihot 注入法 bench，不是新发明**）

你们的真实工作流（论文解读栏目）：

1. 用户在"专门窗口"选模型 + 思考挡位（当前：WorkBuddy 浏览器 + Gemini Argon High）
2. 模型（我）自由选择全站筛选搜录的论文，做精细化解读
3. **调用 OpenRouter key 选其它厂商模型复审** —— 所以建了审核模型能力矩阵
4. 现状瓶颈：浏览器操作受限，用户手动从 comet 存 PDF（`C:\Users\Administrator\Downloads`）→ 慢；正在做 Electron 薄壳浏览器重构（2026-10-31 已决策：抄 ego lite 的 Space/一次性脚本/语义快照三件事，不做 Chromium 内核）

**L2 = 你们已有的 `aihot-审核模型能力对照表` 方法论**：

- **注入法**：在已发布稿注入已知错误（只改归属/方向/条件/因果/机制，不改数字），看审核模型能否指出被改处。**不看通过率**（永远 pass 的审核方通过率 100%）
- 题库：20 条注入（8 类）+ 4 篇 clean（测误报）；判定分「拦截」（high/medium 且命中特征词）与「发现」（仅 watch/归错节）
- 复跑：`ops/note-pilot/bench_reviewers.py --jobs 10 --markdown`（走 `commentary_review.review()` 真实链路）
- 已有结论：glm-5.3 拦截 13/28 零误报；qwen3.8-flash 发现 19/28；kimi 已淘汰

**prisir-model-lab 的 `audit/` 只做两件事**：① 定义 case 格式契约（已写 `inject_cases.yaml`）② 接一个把 bench 结果并入分档表的聚合器。题库本体以 aihot 的 `bench_cases.py` 为准。

## 3. 出题原则（防污染 + 诚实）

1. 题目不进 git 公开仓的知名评测集原题；每题标注"为什么测这个"
2. 敏感内容沿用 babelspan D20 黑名单思路：命中即跳过，不评分
3. runner 记录：模型/平台/日期/耗时/重跑次数/错误类型（`too_long` / `rate_limited` / `bad_json` / `refused`）
4. 免费额度会变 → 表带 `checked_at` 日期，每月复查一次（对齐 FreeToken 清单的"定期复核"做法）

## 4. 项目骨架（prisir-model-lab）

```
D:\Projects\prisir-model-lab\
├── tasks\
│   ├── l1_zh.yaml  l1_instruction.yaml  l1_tools.yaml  l1_long.yaml
│   └── l2\  (babelspan 语料引用 + 论文解读题)
├── runner.py        # provider 适配（复用 fastlane 的 providers/ 约定）
├── judge.py         # L1 程序判定 + L2 卡生成
├── arbitrate.py     # 27B 仲裁（走 Tailscale 上的 27B 端点）
├── verdict.py       # 分档规则（|Δ|≤2 & SD≤2 → 不可并入 等）
└── results\
    ├── capability_table.md   # 产出物：任务类型 × 模型 分档表（带 checked_at）
    └── raw\*.jsonl           # 原始记录（附统计）
```

## 5. 第一轮候选清单（2026-09/10 额度，跑前复核）

L1 全跑：智谱 GLM / 硅基流动（各平台主力免费模型）/ 百炼 qwen-flash 系 / 豆包 lite / 混元 Lite / Gemini Flash / Groq 免费层 / Cerebras / OpenRouter 免费模型 / Kilo Code 免费清单里的国产模型。
L2 只跑 L1 得 `可用` 的（省钱 + 少一次重跑）。

## 6. 与监督者-工作者的接口

摸底表产出的分档 → 写进 gateway 路由组配置：

```yaml
route:
  worker_translation: [siliconflow/glm-4-flash, zhipu/glm-4-flash]   # L1 可用档
  worker_fallback:   [qwen-flash-lite]                                # 仅兜底档
  supervisor:        [27b@tailscale]                                  # 不变
```

---

*方法论出处：babelspan 的 dim_meta.json / 派单协议 D10-D25 / docs/research 2026-10-31 系列。*
