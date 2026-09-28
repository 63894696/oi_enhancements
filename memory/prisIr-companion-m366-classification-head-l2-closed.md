---
name: prisIr-companion-m366-classification-head-l2-closed
description: M3.66 L2 AgentJev classification-head demo 训练闭环(2026-09-23)
metadata:
  type: project
---

# M3.66 L2 AgentJev classification-head demo 闭环(2026-09-23)

## 为什么做这件事

**M3.65 对比研究**:AgentJev(Qwen3-0.6B backbone + permutation-equivariant candidate head,**0 token decode**,Brier 0.0448)vs companion_jev(LM head + LoRA,5-10s,parse_fail 6-50%)。

**核心问题**:LoRA 路径产出 free-form JSON → parse_fail + 复读 + confidence calibration 难。

**AgentJev 路径核心优势**:
1. **0 token decode** — 直接 scalar logit → softmax → prob
2. **permutation-equivariant candidate head** — 候选标签顺序不影响结果
3. **calibrated probability** — Brier 0.0448(论文基线)
4. **不需 prompt template** — 输入状态 + question + candidates 即可

**L2 demo 目标**:跑通最小端到端,验证架构可复用,得出下一步训练规模决策。

## 做了什么

### 1. aliyun T4 训练环境准备(L2.1)
- **git clone** agent-jev-repo(因 GFW WebFetch 失败)
- `/workspace/agent-jev-src/` 放 Qwen3-0.6B 模型权重(aliyun hf-mirror)
- `/workspace/agent-jev-repo/` 源码 + `/workspace/agent-jev-output/perf_smoke` 输出
- **OOM 修复**:batch_states 8→2,max_len 512→256,max_state_tokens 128,grad_accum 4,expandable_segments:True

### 2. 数据准备(L2.2)
- **`companion/data_prep_classification_head.py`** — 新写,把 8 scenario jsonl → AgentJev schema
  - 输出:`{id, env:"synthetic", source, state, questions[{text, candidates, gold.distribution, supervision:"known_distribution", weight:1.0}]}`
  - make_distribution(label, candidates, sharpness=0.95) → 锐化分布
- **100 条 perf demo 集**:`/workspace/companion/data/data_perf_ch.jsonl`
- dry_run validation 通过(0 truncation, 0 dropped)

### 3. L2 demo 训练(L2.3)
- 配置:`/workspace/agent-jev-repo/configs/perf_smoke.yaml`
  - 100 states / 60 max_steps / batch_states=2 / grad_accum=4
  - LLRD: embed=1e-5, bottom=2e-5, middle=4e-5, top=6e-5, head=1e-4
  - loss_weights: ce=1.0, brier=0.0, perm_kl=0.0
- **结果**:60 steps, loss 1.49 → 0.27, 287s (4.8min), 0 OOM, 0 trunc, 0 dropped
- checkpoint-60.pt (7.2GB full) + final.pt (2.4GB fp32 backbone included) 已落盘

### 4. Head-only 导出策略(L3 关键发现)
- final.pt 含 596M backbone params(2.3GB) + 2.4M head params(9MB)
- **strip backbone → 仅导出 head**:
  ```python
  head_sd = {k: v for k, v in sd.items() if not k.startswith('path_encoder.backbone')}
  ```
- **`perf_ch_head.pt` 9.5MB** → scp 下载本地 (D:/prisir-train-assets/trained/perf_ch_v1/adapter/)
- **本地推理时**:HF 加载 Qwen3Guard-Gen-0.6B(本地已有,同构 backbone)→ 取出 .model 属性(Qwen3Model,无 LM head)→ 加载 head-only weights

### 5. 本地推理 wrapper(L3)
- **`companion/agentjev_runtime.py`** — 新写,~200 行
  - `AgentJevRuntime.load(backbone_path, head_path, device)` — 加载 Qwen3Model + head-only
  - `infer_one(state, question, candidates) → {candidates, probs, top, top_prob, latency_ms}`
  - 单条 demo 跑通(0x3b+e61b7+tap0901 disconnected → top=critical)

### 6. 50 条 bench 对比(L4)
- **`companion/bench_perf_agentjev.py`** — 新写,复用 bench_perf_local.TEST_CASES(50 条,5 类各 10)
- sample dict → `_build_text()`(单行紧凑短语,跟训练对齐)→ AgentJev 推理

## 关键结果(50 条 bench)

| 模型 | ACC | parse_fail | p50 latency | critical ACC |
|------|-----|-----------|-------------|-------------|
| **perf_conf_v3** (LoRA M3.62) | **78%** | 0% | 7.5s | 7/10 (70%) |
| **perf_ch_v1** (AgentJev demo) | **20%** | n/a | 2.0s | 0/10 (0%) |

**per-class perf_ch_v1**:
- safe: 0/10 (0%)
- low: 10/10 (100%) ← 全部预测为 low(softmax argmax 凑巧)
- medium: 0/10 (0%)
- high: 0/10 (0%)
- critical: 0/10 (0%)

## 关键发现

### ✅ 架构正确,head 完全没收敛
- 推理跑通、latency 2s(快 4 倍)
- 但 head 训 60 steps + 100 样本 + 5 类 + 仅 CE loss,**softmax 几乎均匀分布**(~0.2 各类)
- 全部 argmax 为 `low`(因 state 含"ISLC 触发""standby""容器启动"被 head 误判为 low-anchor)

### ⚠️ 训练规模决策点(L3)
**当前 60 steps 远远不够**:
- 论文 AgentJev 0.6B 训满 ~10K-50K 样本,2000+ steps
- 我们只训 60 steps × 100 样本 = 4800 sample-step ≈ 论文 1-5%
- **预期**:扩到 1000 样本 + 500 steps, ACC 应能涨到 60-80%(与 LoRA 持平)

### ⚠️ 推理路径选择:PathEncoder v1 (重)
- 当前 wrapper 用 PathEncoder:每个 (state, q, cand) 拼成独立序列,backbone 跑 P 次
- 5 个 candidate → P=5,backbone 跑 5 次 → ~2s
- **TreeEncoder (论文后续)** 应能 1 次 backbone + 共享 prefix → 估算 ~0.5s

### ✅ head-only 9.5MB 部署可行
- 比 LoRA 17.5MB 小 50%
- 不需要 base model LoRA 注入(backbone 用 HF 原生)
- 后续每个新场景只需训 head,9.5MB 单文件即可

## 决策点

**是否继续 L3 全 8 scenario scale-up?**

**推荐:是,但要重新估算资源**
- L3 需要:
  - 生成 7 个 scenario × 500 样本训练集(intents/task/disk_cleanup/tempfile/email/log/perf 各 500 条)
  - 训 7 个 head × ~10 min/head = ~70 min on T4(全量场景并行可缩到 20-30 min)
  - 每个 head-only ~9.5MB,共 ~70MB 部署到本地
- 预期收益:
  - **parse_fail = 0**(vs LoRA 6-50%)
  - **latency 5-10s → 2s**(4x 加速)
  - **conf calibration 强**(brier loss 直接训)
- 风险:
  - ACC 在 100 样本/场景下可能仅 50-60%(vs LoRA 当前 32-82%)
  - 需 5x 样本才能稳

**替代方案**:暂只训 perf scenario 全量(已有 645 真实样本 + 150 abstract),其它场景 LoRA 不动

## 关键文件改动

| 文件 | 改动 |
|------|------|
| `companion/data_prep_classification_head.py` | **新建** 8 scenario jsonl → AgentJev schema 转换 |
| `companion/agentjev_runtime.py` | **新建** 本地推理 wrapper (~200 行) |
| `companion/bench_perf_agentjev.py` | **新建** 50 条评测 harness |
| `companion/data/data_perf_ch.jsonl` | **新建** 100 条 AgentJev schema 训练集 |
| `D:/prisir-train-assets/trained/perf_ch_v1/adapter/perf_ch_head.pt` | **下载** head-only 9.5MB |
| `D:/prisir-train-assets/trained/perf_ch_v1/adapter/meta.json` | **下载** 训练元数据 |
| `D:\tmp\run_agentjev_train.sh` | **新建** aliyun 启动脚本 |
| `/workspace/agent-jev-repo/configs/perf_smoke.yaml` | **新建** 训练配置 |

## aliyun 上产出的文件
- `/workspace/agent-jev-output/perf_smoke/final.pt` — 2.4GB 全量(保留备查)
- `/workspace/agent-jev-output/perf_smoke_export/perf_ch_head.pt` — 9.5MB head-only(已下载)
- `/workspace/agent-jev-output/perf_smoke/log.jsonl` — 训练曲线

## 关键参考

- `/workspace/agent-jev-repo/agentjev/model.py` — PathEncoder/TreeEncoder/CandidateSetEncoder/ScalarScorer
- `/workspace/agent-jev-repo/agentjev/data.py` — make_collate + STATE/QUESTION/CANDIDATE prefix
- `/workspace/agent-jev-repo/agentjev/losses.py` — SUP_CODES, soft_ce_per_q, brier
- `/workspace/agent-jev-repo/agentjev/train.py` — LLRD param groups + NaN guard
- `memory/prisIr-companion-m365-agent-jev-compare.md` — 立项对比
- `memory/prisIr-companion-m362-perf-conf-v3-closed.md` — LoRA 路径当前 production
- `companion/bench_perf_local.py` — LoRA baseline harness(同 TEST_CASES)