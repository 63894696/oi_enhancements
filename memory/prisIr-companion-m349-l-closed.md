---
name: prisIr-companion-m349-l-closed
description: M3.49 日志分类闭环 — L6 ChatML 修复后 parse_fail 0.97→0.50,risk_acc 0.02→0.40
metadata:
  type: project
---

# M3.49 日志分类闭环(2026-09-23)

## 完整流程

L1: `data_prep_log.py` — 5 类(critical/high/medium/low/safe)+ 4 处置(alert/keep/review/drop) + 6 数据源(nginx/postgresql/kernel/python/systemd/docker) — **490 行**

L2: 训 log base adapter — 4 epochs,248 steps,~4min,loss 收敛到 0.0004(过拟合)

L3: 训 log_conf adapter — confidence_teacher 走 log base,生成 data_log_conf.jsonl,再训 — ~4min

L4: 注册 + 写 classify_log.py — adapter_registry.py 加 log+log_conf

L5: bench 暴露灾难性 parse_fail=0.969,calibration delta=-0.02(倒挂)

L6: 改 train_step1.py prompt 模板加 `<|im_start|>/<|im_end|>` ChatML token,重训 log+log_conf
- 原:`user\n{text}\nassistant\n`(裸 user/assistant 双标签)
- 新:`<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n`

L7: 评测 — **parse_fail 0.969 → 0.500,risk_acc 0.020 → 0.398,action_acc 0.010 → 0.286,calibration delta +0.163**

## v1 vs v2 对比

| 指标 | v1(裸 user\n) | v2(ChatML) | 变化 |
|------|--------|--------|------|
| parse_fail | 0.969 | 0.500 | -48% |
| risk_acc | 0.020 | 0.398 | **20x** |
| action_acc | 0.010 | 0.286 | **28x** |
| cal delta | -0.02(倒挂) | +0.163(正确) | ✅ |
| 输出格式 | `Jailbreak → Action → Safety`(乱) | `Safety → Jailbreak → Action`(对) | ✅ |

## 关键修复点

### 1. train_step1.py PROMPT_TEMPLATE_SAFETY / PROMPT_TEMPLATE_TEMPFILE

```python
PROMPT_TEMPLATE_SAFETY = (
    "<|im_start|>user\n"
    "{text}<|im_end|>\n"
    "<|im_start|>assistant\n"
)
PROMPT_TEMPLATE_TEMPFILE = (
    "<|im_start|>user\n{text}<|im_end|>\n"
    "<|im_start|>assistant\n"
)
```

### 2. build_target() 末尾

```python
base += f"\nAction: {act_str}"
# M3.49 L6: ChatML end token
return base + "<|im_end|>"
```

## 残留问题

- parse_fail 仍 50%(49/98 样本不能 parse)
- 模型偏向 High/Medium,几乎不预测 Critical
- 后续优化:
  - 数据平衡:critical 类样本显式扩充
  - 推理:eos_token 加 `` 让模型学会停
  - 重训全部 6 个 adapter(旧 6 个用裸 user\n,新 9 个用 ChatML)— 预估 30min

## 关键文件

- `companion/data_prep_log.py` — 训练数据生成器
- `companion/classify_log.py` — 生产推理入口(模板用了 ChatML)
- `companion/train_step1.py` — patched with ChatML
- `companion/adapter_registry.py` — 注册 log + log_conf(改描述为 ChatML v2)
- `run_train_log_v2.sh` / `run_train_log_conf_v2.sh` — aliyun 训练脚本
- `wait_log_v2_pipeline.py` — wait → teacher → train → bench orchestrator
- `bench_log.py` — 评测
- `/workspace/qwen3guard-log/adapter/` — log base v2(已下载到本地)
- `/workspace/qwen3guard-log-conf/adapter/` — log_conf v2(已下载到本地)

## aliyun 状态

- 实例 ap-southeast-1 继续保留(用户指示"先不用关实例")
- T4 GPU 空闲,等下个训练目标

## 相关 memory

- [[prisIr-companion-m349-step1-closed]] — data_prep_log L1 闭环
- [[prisIr-companion-m349-confidence-teacher]] — confidence teacher 设计
- [[prisIr-companion-m349-l6-chatml]] — L6 ChatML 修复(根因)
- [[prisIr-companion-m349-tempfile-conf-closed]] — 同类实验性参考

**Why:** 完整闭环日志分类 adapter,从数据 → 训练 → 评测 → 注册 → 推理,踩了 ChatML 模板这个大坑。
**How to apply:** 以后给 Qwen3Guard 加新场景,直接继承 ChatML 模板;bench parse_fail > 30% 时第一反应检查 prompt 格式。