---
name: prisIr-companion-m366-classification-head-strategy-a-closed
description: M3.66 L7-A 策略 A 三组对比闭环 + 决策(2026-09-23)
metadata:
  type: project
---

# M3.66 L7-A 策略 A 三组对比闭环(2026-09-23)

## 做了什么

在 aliyun T4 启动官方 AgentJev HTTP server (`127.0.0.1:18765`),用同一份 perf 50 条 TEST_CASES 跑 zero-shot 推理。

### 启动过程
- **格式转换**:`model.safetensors` (2.4GB) → `agentjev_v1.pt` (torch dict) 因为 engine.py 期望 `bundle['state_dict']`
- **第一次启动 KeyError**:`bundle['state_dict']` 缺失 → 修法如上
- **API 字段名**:文档里 `text` 实际叫 `question` (来自 contract.py)
- **smoke test** 通过(单条返回 distribution 5 类概率)
- **bench 50 条** retry 3 次兜底,但 server 端正常,无重试触发

### 输出
- `companion/reports/bench_perf_agentjev_official.json` — 50/50 全部 ok
- `companion/bench_perf_agentjev_official.py` — bench harness(可重跑)

## 三组对比结果(perf 50 条 TEST_CASES)

| 模型 | ACC | parse_fail | p50 latency | total | critical ACC | medium ACC |
|------|-----|-----------|-------------|-------|-------------|-----------|
| **perf_conf_v3 (LoRA, M3.62 production)** | **78%** | 0% | 7.5s | 375s | 7/10 (70%) | 10/10 (100%) |
| **official AgentJev zero-shot** | **40%** | n/a | 200ms | **10.1s** | 8/10 (**80%**) | 10/10 (100%) |
| **perf_ch_v1 (我们训 AgentJev head, 60 steps)** | 20% | n/a | 2.0s | 98s | 0/10 (0%) | 0/10 (0%) |

## 关键发现

### 1. official AgentJev ACC 40%(远低于 paper 79%)
**为什么 paper 79% 我们 40%?**
- paper 测的是 **Typed Decisions**(agent workflow 决策:测试是否通过/下一步选什么)— 完全契合训练分布
- 我们测的是 **本地性能快照 5 类风险**(perf schema)— **分布外 zero-shot**
- 官方 head 在分布外表现明显下降(40%)但仍**优于我们训的 20%** — 我们训得太少(60 steps / 100 样本)

### 2. official AgentJev 偏向 medium/critical(双峰偏好)
- safe 2/10(20%)
- low 0/10(0%) ← 完全不预测 low
- medium 10/10(100%)
- high 0/10(0%) ← 完全不预测 high
- critical 8/10(80%)
- **预测分布**:36/50 = 72% 是 medium/critical
- **原因**:官方训练数据中,risk question family 的"unknown risk" gold distribution 偏 medium/safe,但 5 类分布会被"max risk" 处理压到 critical — 不完全是我们 perf schema 的语义

### 3. official AgentJev latency 200ms/条(快得超出预期)
- 50 条只用 10.1s — shared_prefix + TreeEncoder 优化大幅生效
- 单条 wall_ms 范围约 180-250ms(来自 server usage)
- 比 LoRA 7.5s 快 **37 倍**
- 但 ACC 40% 远低于 LoRA 78%

### 4. critical 漏报不同
- **perf_conf_v3 LoRA**:critical 7/10(70%) — 漏报 case 41/47(boot_s 30/45 但 bugcheck < 2)
- **official AgentJev**:critical 8/10(80%) — 漏报 case 41/47 同样被误判为 medium,但 top_prob 0.34-0.46 比 LoRA 高(更稳)
- **共性**:两边都漏报 boot_s ≤ 60 + bugcheck < 2 的临界场景 — **结构化 5-类风险分类在抽象边界仍需场景专项训练**

## 决策

### 不切换 — LoRA (perf_conf_v3) 仍是 production
**理由**:
- 78% ACC vs 40% ACC → 绝对差距 38pp,业务上不可接受
- LoRA parse_fail = 0%(M3.62 已修),calibration 良好
- 7.5s latency 本场景可接受(perf 5 分钟采一次,不是热路径)

### official AgentJev 路径 **保留为 fallback 候选**,不进入 production
**理由**:
- 40% ACC 不够 production,但 latency 0.2s 极快 + parse_fail 永远 0
- **适用场景**:
  1. **Jev API 断网** → official AgentJev server 本地启动作 fallback(虽然 ACC 低但永远可用)
  2. **per-scenario 无 LoRA 时**(比如新增 scenario,LoRA 还没训)— official AgentJev 提供 baseline 兜底
  3. **research-only benchmark 基准** — 永远不替代 LoRA,但提供 reference

### 我们训的 perf_ch_v1(20%) 不足
- 60 steps + 100 样本完全不够
- **L3 scale-up 决策**:**先搁置**
  - 投入:每个 head 训 ~30 min × 8 scenario = 4 小时 + 数据集整理 8 × 500 = 4000 条
  - 收益:即使训好 ACC 50-70%,仍不如 LoRA 78%
  - 风险:AgentJev 路径的 medium 偏好是 head 结构性问题,扩样本不一定能解决
  - **结论**:M3.66 L3 暂停,等未来 perf/disk_cleanup 等 scenario 需要更复杂时再考虑

### 完整三组对比信息**保留**作为 reference
- 三组都跑了同一份 TEST_CASES → 直接可比的 ACC/per-class/confusion
- 后续若 LoRA 退化,可重新跑 official AgentJev 对比验证

## 关键文件改动

| 文件 | 改动 |
|------|------|
| `companion/bench_perf_agentjev_official.py` | **新建** ~120 行 HTTP 调用 bench harness |
| `companion/reports/bench_perf_agentjev_official.json` | **新增** 50 条 zero-shot 报告 |
| `/workspace/agent-jev-src/agentjev_v1.pt` | **aliyun** safetensors → torch dict 转换(2.4GB) |
| `/workspace/agent-jev-output/official_server.log` | **aliyun** server 日志 |

## 关键参考

- `memory/prisIr-companion-m366-classification-head-l2-closed.md` — L2 demo 闭环(perf_ch_v1 20%)
- `memory/prisIr-companion-m366-classification-head-survey-2026-09-23.md` — 立项调研
- `memory/prisIr-companion-m362-perf-conf-v3-closed.md` — perf_conf_v3 LoRA baseline
- `memory/prisIr-companion-m365-agent-jev-compare.md` — 立项 AgentJev 对比研究
- `/workspace/agent-jev-repo/jev_service/contract.py` — HTTP API schema
- `/workspace/agent-jev-repo/README.md` — paper Eval 79.25% on Typed Decisions(分布外)