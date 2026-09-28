---
name: prisIr-companion-m365-agent-jev-compare
description: 对比研究 malevrigns/agent-jev 项目(2026-09-23),看能否复用省事
metadata:
  type: project
---

# M3.65 AgentJev 对比研究(2026-09-23)

## 为什么做这件事
**用户原话**:"https://github.com/malevrigns/agent-jev 看看这个项目,对⽐我们和这个区别,看看能否省点事?"

AgentJev 在 GitHub 2 天内 261 stars + 22 forks,显著高于同类项目。它和我们 companion_jev 都是"小模型系统一决策"主题,值得对比能不能借鉴/复用。

## AgentJev 架构核心(关键洞察)

| 维度 | AgentJev | companion_jev(我们) |
|------|----------|---------------------|
| **Backbone** | Qwen3-0.6B(去 LM head) | Qwen3Guard-Gen-0.6B + LoRA(完整 LM head) |
| **Head** | permutation-equivariant CandidateSetEncoder(2-layer Transformer) | LM head + 生成式文本 + post-parse |
| **输出** | 直接 logits + softmax,**0 token decode** | 40 token 生成 + regex parse |
| **Primitives** | Boolean / Choice / Score(三类) | safety / intent / task / stage_outcome(8 类自定 schema) |
| **Context** | 2048 token | 512 token |
| **共享 prefix** | v1 未做(planned v2),shared prefix cache 测得 298ms vs 609ms | N/A |
| **性能指标**(typed-decisions 2k 题) | Top-1 79.25% / Brier 0.0448 / ECE 0.169 / Soft CE 0.8494 | (各自 scenario 32%-88% / Brier 0.04-0.36) |
| **延迟** | 50ms 一次 forward | 5-10s(40 token decode) |

## 关键架构差异 — 为什么他们 parse_fail 0% / 我们 6-50%

- **AgentJev**:classification head → 一次 forward → softmax 出概率分布
  - 不依赖 LM 文本生成,不存在 parse 失败
  - calibrated prob 直接出 logits,不需要 confidence_teacher 训练
- **companion_jev**:LoRA 在 LM head 上 → 生成" Safety: X:0.95\nJailbreak: Y..." 文本 → regex 解析
  - 复读 / 截断 / 顺序错乱都 → parse_fail
  - confidence_teacher.py 必须再训一遍才能给出 calibrated prob

## 能否复用 / 省事?

| 选项 | 评估 | 决定 |
|------|------|------|
| 直接换 base 用 AgentJev weights 替 Qwen3Guard | ❌ 装不下 risk+action 双标签 | 不做 |
| **用 AgentJev weights 作 base,加 classification head 训我们的多分类** | ⚠️ 可行;长期 M3.66+ 值得做 | 记入规划 |
| **借 HTTP service 架构**(8149 端口 + JSON state + questions) | ✅ 我们 companion_jev 已经类似形态 | 已对齐 |
| **借 calibration 直出 logits**(放弃 confidence_teacher 训练) | ✅ 可大幅简化 | M3.66+ 候选 |
| **借 routing.py + routing_runtime.py** | ✅ routing 正是我们 M3.56 缺的能力 | M3.66+ 候选 |
| **借 synth.py 数据生成** | ✅ 我们的 data_prep_*.py 路径类似 | 借鉴扩展模式 |

## 短期行动(M3.62 进行,M3.66+ 规划)

1. **M3.62 perf_conf_v3 继续训完**(已是 LM head 路径,不能半路切换)
2. **M3.63 Jev 真测对比** 仍走现有 path(Jev 是 Qwen3Guard 闭源服务)
3. **M3.64 critical balanced 重训** 同 LM head 路径
4. **M3.66+ 规划**:评估是否值得重训一个 classification-head 版替代我们 15 个 LoRA
   - 收益:parse_fail 0%,latency 100x,calibration 强 5-10x,一个 weights 通用多 scenario
   - 成本:重训 15 个 scenario × ~10min = ~2.5h,改 train_step1.py / classify_*.py 全部
   - **判定 M3.66+ 视 perf_conf_v3 ACC 决定**:若 v3 ACC > 75%,说明 LM head 还能抢救,不值得切换;若仍 < 50%,则切 classification head

## 关键参考

- `https://github.com/malevrigns/agent-jev` — 项目主页
- `agentjev/model.py` — CandidateSetEncoder 实现(permutation-equivariant,2-layer)
- `agentjev/routing.py` + `routing_runtime.py` — M3.56 任务路由可借鉴
- `agentjev/synth.py` — 数据生成模式(我们 data_prep_*.py 类似)
- `agentjev/train.py` — 训练循环
- HuggingFace `aimeigaoshou/agent-jev` weights
- `companion/adapter_registry.py` — 我们的 LM head + LoRA 路径
- `companion/confidence_teacher.py` — 我们的 calibration 训练(可被 AgentJev logits 直出替代)

## 决策

**当前不切换**(LM head + LoRA 路径有 15 个已训 adapter,perf_conf_v3 训完会填满),但记入 M3.66+ 路线图,**视 perf_conf_v3 ACC 是否突破 70% 决定**。