---
name: prisIr-companion-m349-l6-chatml
description: M3.49 L6 ChatML 模板重训 — log_conf parse_fail 0.97 → 期望 < 0.20
metadata:
  type: project
---

# M3.49 L6 ChatML 模板修复(2026-09-23)

## 背景

M3.49 L3 训练 log_conf adapter 后,bench 显示:
- risk_acc=0.020 / action_acc=0.010 / **parse_fail=0.969**
- 推理输出顺序乱:`Jailbreak: No\nAction: Review\nSafety: Medium`(期望 `Safety → Jailbreak → Action`)

## 根因

`train_step1.py` 用了手写的 `user\n{text}\nassistant\n` 格式,**没用 Qwen3Guard-Gen-0.6B 的 ChatML token** (``)。

- 模型训练时见不到 `<|im_start|>/<|im_end|>` 边界
- 0.6B 模型 + 4 epochs + 490 行小数据集 → 极端过拟合(loss → 0.0004)
- 推理时输出顺序偏离训练时见到过的 target 段顺序,变成 `Jailbreak → Action → Safety`
- **不只 log adapter 影响** — 探针显示 disk_cleanup base 也输出错顺序,只是 conf 版 bench 数字没炸出来

## 修复

改 `train_step1.py`:
- `PROMPT_TEMPLATE_SAFETY` 和 `PROMPT_TEMPLATE_TEMPFILE` 都加 `<|im_start|>user\n` + `<|im_end|>\n<|im_start|>assistant\n`
- `build_target` 末尾加 `<|im_end|>` token

文件是 CRLF,直接用 Edit 工具改不动,改用 Python bytes 脚本 `patch_chatml.py` / `patch_tempfile.py` / `patch_return.py`。

## 重训结果(待 L7 bench)

- log base v2:4 epochs / 248 steps / ~4min
- log_conf v2:同 base 4 epochs(用 confidence_teacher 重生成 _conf 数据)
- bench_log.py 跑同一 test split,期望 parse_fail 0.97 → < 0.20

## 影响

✅ 后续 train_step1.py 训练的所有新 adapter 会自动用 ChatML 模板。
⚠ 已训的 6 个旧 adapter(safety/tempfile/disk_cleanup/email + 各自的 _conf)推理模板还是旧的 — 但因 training 用了同样的旧模板,inference 一致,所以**继续能用**。
🔜 长期可考虑重训全部 adapter 用 ChatML 提升质量(预估 30min)。

## 关键文件

- `companion/train_step1.py` — patched
- `run_train_log_v2.sh` / `run_train_log_conf_v2.sh` — 新训练脚本(aliyun 上)
- `wait_log_v2_pipeline.py` — 等 base → confidence_teacher → conf → bench 全自动 orchestrator

## 相关 memory

- [[prisIr-companion-m349-step1-closed]] — log base 第一版(parse_fail 0.97)
- [[prisIr-companion-m349-confidence-teacher]] — confidence teacher 设计
- [[prisIr-companion-m349-l-closed]] — M3.49 总闭环(L7 后补)

**Why:** log adapter 全部 parse 失败,改 prompt 模板对齐 ChatML 是最少改动方案。
**How to apply:** 以后 train_step1.py 加新 schema 时,直接继承 ChatML 模板;bench parse_fail > 50% 时第一反应检查 prompt 格式。