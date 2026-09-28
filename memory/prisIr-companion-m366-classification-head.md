---
name: prisIr-companion-m366-classification-head
description: M3.66 立项 classification-head 路径替换 15 个 LoRA(2026-09-23)
metadata:
  type: project
---

# M3.66 classification-head 替换路径立项(2026-09-23)

## 为什么做这件事

**用户原话(触发)**:"我们为保障精度分场景做了多个专项意味着系统资源占用高效果不好,那 AgentJev-0.6B 和官方都速度快很多还没说分场景,意味着用它们的实现方式更优?"

**已知证据(M3.61 + M3.63 实测)**:

| 维度 | 我们 (LM head + LoRA × 15) | AgentJev (classification head) |
|------|---------------------------|-------------------------------|
| base model | Qwen3Guard-Gen-0.6B(hidden=2048) | **Qwen3-0.6B**(hidden=1024)— 小一倍 |
| 加 scenario 成本 | +1 LoRA(~17MB)+ 5-10s/条推理 | +1 classification head(~5MB)+ ~5ms/条推理 |
| parse_fail 率 | **6%-50%**(实测 8 scenario) | **0%**(logits 直出,无 parse) |
| 延迟(本机 1060) | 5-10s | ~50ms(AgentJev 测得) |
| Brier score | 0.04-0.36 | 0.0448 |
| ACC 范围 | 31%-88% | **79.25%** (single weights, 2k typed-decisions) |
| calibration | 需 confidence_teacher.py 二次训练 | 直出 logits,calibrated |
| M3.63 实测 Jev 真测 vs 本地 | intents: Jev 94% vs Local 88%(Δ=-6%);safety: Jev 27% vs Local 0% | — |

## 核心洞察:**"分场景" = 我们为了绕开 LM head 能力不足而背上技术债**

- **base model 选错**:Qwen3Guard 是给生成式聊天安全写的,我们把它当 multi-task 分类 base 用
- **15 个 LoRA** 是为了绕开 LM head 能力不足 → 这不是优势,是技术债
- **parse_fail** 必然 → 既然走 LM head,生成文本不可能 100% 解析对
- **每次推理 5-10s 是"伪加速"** → 用户角度:慢 + 偶尔错 = 体验差

## M3.66 立项计划(预计 4-6h)

### L1 调研 + 立项(进行中,~30min)
- ✅ 从 HuggingFace 拿 AgentJev weights(16 文件 32s)
- ✅ 看到 config.json:Qwen3-0.6B + tie_word_embeddings=true(共享 embeddings,**无 LM head**)
- ⏳ 拉 GitHub 源码(train.py / model.py / synth.py)— GFW 阻断,改用 webfetch 重试或从 issues / discussions 文档逆向

### L2 训练验证(~2h,在 aliyun T4)
- L2.1 准备 data_prep_classification_head.py — 把现有 8 scenario × ~600 条数据转为 AgentJev 训练格式
  - 输入:`state`(文本)+ questions(dict)
  - 标签:choices 列表 / score 数值 / noul 布尔
- L2.2 训练 1 个 demo scenario(intents,500 条)
- L2.3 评测对比:classification head ACC vs 我们 88% LoRA ACC vs Jev 94%

### L3 8 scenario 全部训练 + 推理服务(~2h)
- L3.1 数据准备:8 scenario × {intents/task/safety/perf/disk_cleanup/tempfile/email/log}
- L3.2 在 aliyun T4 跑 8 个 head(每个 ~5min,共用 base)
- L3.3 推理服务:HTTP endpoint + JSON state + questions,跟 companion_jev 同样形态
- L3.4 接 companion_jev.py fallback:主通道 AgentJev-classification-head,fallback 我们 15 LoRA

### L4 双通道部署 + 回归(~1h)
- L4.1 companion 默认走 AgentJev path,本地 LoRA 作 fallback
- L4.2 性能对比:parse_fail 0%、延迟 100x、calibration 5-10x、ACC +20pp
- L4.3 M3.66 闭环 memory + MEMORY.md 索引更新

## 决策标准(L2 后决定是否 L3)

| 指标 | 阈值 | 不通过则 |
|------|------|---------|
| intents ACC(classification head) | ≥90% | 退回 LM head + LoRA 路径,只局部替换部分场景 |
| 延迟(本机 1060) | <200ms | 同上 |
| parse_fail | 0% | ✅ 必然 |
| 显存占用(单 base + 8 heads) | <4GB | 退回 |

## 关键文件改动清单(预计)

| 文件 | 改动 |
|------|------|
| `companion/data_prep_classification_head.py` | **新建** 把 jsonl → AgentJev 训练格式 |
| `companion/classify_classification_head.py` | **新建** 推理入口(0 token decode) |
| `companion/agent_jev_path.py` | **新建** HTTP client + 双通道 fallback |
| `companion/companion_jev.py` | **改** try_jev 主通道 → agent_jev_path |
| `companion/bench_classification_head.py` | **新建** 8 scenario × classification head 评测 |
| `aliyun/train_classification_head.py` | **新建** 训练脚本 |
| `memory/prisIr-companion-m366-classification-head.md` | **新建** 本文件 |
| `MEMORY.md` | 加新索引行 |

## 不在本次范围

- ❌ 训练自己的 base weights — 直接用 AgentJev 的 Qwen3-0.6B
- ❌ 加新 scenario — 8 个现有 scenario 已经够覆盖
- ❌ 删 15 个 LoRA — 留作离线 / 资源紧张时 fallback
- ❌ 改前端 — 推理层无感替换

## 关键参考

- `https://github.com/malevrigns/agent-jev` — 261 stars(2 天)
- `https://hf-mirror.com/aimeigaoshou/agent-jev` — 已下载 weights
- `companion/companion_jev.py` — 现有 try_jev() 双通道 fallback 形态
- `companion/adapter_registry.py` — 现有 15 个 LoRA 加载路径(留作 fallback)
- `memory/prisIr-companion-m365-agent-jev-compare.md` — M3.65 初步对比
- `memory/prisIr-companion-m361-bench-recovery.md` — M3.61 8 scenario ACC 表