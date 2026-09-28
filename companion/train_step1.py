#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# train_step1.py — M3.45/M3.46 自训小模型兜底训练脚本(2026-09-23)
#
# 目的:
#   - 在 ap-southeast-1 T4 GPU 实例上微调 Qwen3Guard-Gen-0.6B
#   - 支持两个 schema:
#     schema=safety    — Step 1 风险分类(text + risk_label + jailbreak_label)
#     schema=tempfile  — Step 2 文件清理(text + risk_label + _action)
#   - 输出: ./outputs/<name>/{adapter, merged}/ + 可选 GGUF 量化
#   - LoRA(r=16) + 4bit 加载,T4 16GB 足够跑(主进程 < 6GB,grad/optim offload)
#
# 用法(GPU 实例内):
#   cd /workspace/companion
#   python train_step1.py --data data_step1.jsonl --schema safety --epochs 3
#   python train_step1.py --data data_tempfile.jsonl --schema tempfile --epochs 4
#
# 设计依据:
#   - Qwen3Guard-Gen 是生成式分类器(输出 "Safety: Safe/Low/Medium/High/Critical"
#     或 "Jailbreak: Yes/No" + 内容片段)。微调要让模型学会:
#     1. 短文本也照规范格式输出
#     2. 风险档位 fine-grained 区分
#     3. 中英双语 jailbreak 短语识别
#     4. (tempfile)action 字段给出"delete/review/keep"建议
#   - 训练 prompt 模板:
#     safety   :Qwen3Guard 原生 "user\n{text}\nassistant\n"
#     tempfile :ChatML 风格 "<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n"
#   - target = "Safety: <label>\nJailbreak: <yes/no>[ + \nAction: <a>]"(< 80 token)
#
# 实测(2026-09-22):300 条 × 3 epoch × T4 16GB ≈ 20s + 启动 ≈ 90s 总
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# ---- CLI ----
ap = argparse.ArgumentParser(
    description="微调 Qwen3Guard-Gen-0.6B(支持 safety/tempfile 双 schema)")
ap.add_argument("--data", default="data_step1.jsonl",
                help="训练 JSONL 路径")
ap.add_argument("--schema", default="safety",
                choices=["safety", "tempfile", "disk_cleanup", "email", "log",
                         "intents", "perf", "task", "goal_gate"],
                help="数据 schema:safety(M3.45 Step1) / tempfile(M3.46 Step2) / "
                     "disk_cleanup(M3.47 Step 1) / email(M3.48) / log(M3.49) / "
                     "intents(M3.50 聊天意图分类) / "
                     "perf(M3.51 本地性能快照风险分类) / "
                     "task(M3.58 6 类任务分类 code_call/code_qa/...) / "
                     "goal_gate(M3.83 goal_state 4 头 LoRA)")
ap.add_argument("--base-model",
                default="Qwen/Qwen3Guard-Gen-0.6B",
                help="HF 模型 ID 或本地路径")
ap.add_argument("--output", default="outputs/qwen3guard-finetuned",
                help="输出目录(adapter/ merged/ 子目录)")
ap.add_argument("--epochs", type=int, default=3)
ap.add_argument("--batch", type=int, default=4)
ap.add_argument("--lr", type=float, default=2e-4)
ap.add_argument("--max-len", type=int, default=512)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--quant-gguf", action="store_true",
                help="训练完导出 GGUF Q4_K_M 给 llama.cpp 跑")
args = ap.parse_args()


# ---- Prompt templates(分 schema)----
# Step 1 safety:Qwen3Guard 原生 user/assistant 双标签
# **M3.49 L6 修复(2026-09-23)**:Qwen3Guard-Gen-0.6B 用 ChatML。
# 旧版本用裸 user\n{text}\nassistant\n 没 <|im_start|>/<|im_end|> token,
# 模型学不到生成顺序会乱套(实测 log base 输出
# `Jailbreak: No\nAction: Review\nSafety: Medium` 而非预期 Safety→Jailbreak→Action)。
# 改用手写 ChatML token,训练 + 推理两侧都对齐。
PROMPT_TEMPLATE_SAFETY = (
    "<|im_start|>user\n"
    "{text}<|im_end|>\n"
    "<|im_start|>assistant\n"
)

# Step 2 tempfile:ChatML 风格 + action 字段,输出 3 行
PROMPT_TEMPLATE_TEMPFILE = (
    "<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n"
)


def build_target(risk_label: str, jailbreak_label: bool,
                 action: str | None = None,
                 conf: dict | None = None) -> str:
    """目标输出文本。Step 1 双标签 / Step 2 + action。**M3.45.1: 加 confidence 后缀**。

    conf (可选):{"risk": 0.94, "jailbreak": 0.99, "action": 0.92} 等
    若提供,target 输出 "Safety: Medium:0.94" 形式(对齐 Jev 的 calibrated prob)。
    """
    jb = "Yes" if jailbreak_label else "No"
    risk_str = risk_label.capitalize()
    jb_str = jb
    if conf:
        risk_str += f":{conf.get('risk', 1.0):.4f}"
        jb_str += f":{conf.get('jailbreak', 1.0):.4f}"
    base = f"Safety: {risk_str}\nJailbreak: {jb_str}"
    if action:
        act_str = action.capitalize()
        if conf and conf.get("action") is not None:
            act_str += f":{conf['action']:.4f}"
        base += f"\nAction: {act_str}"
    # M3.49 fix: ChatML 结尾 token 让模型学何时停
    return base + "<|im_end|>"


def load_dataset(data_path: str, tokenizer, max_len: int,
                 schema: str = "safety") -> list[dict]:
    """读 JSONL + tokenize + 构造 labels(masked prompt,只答 loss)。

    schema=safety      :Step 1,字段 text + risk_label + jailbreak_label
    schema=tempfile    :Step 2,字段 text + risk_label + _action
    schema=disk_cleanup:M3.47,字段 text + risk_label + _action
    schema=email       :M3.48,字段 text + risk_label + _action
                         (delete/archive/reply,语义同 tempfile)
    """
    samples = []
    tmpl = (PROMPT_TEMPLATE_SAFETY if schema == "safety"
            else PROMPT_TEMPLATE_TEMPFILE)
    with open(data_path, "r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            text = obj["text"].strip()
            # tempfile / disk_cleanup / email / log / intents / perf 都带 action
            has_action = schema in ("tempfile", "disk_cleanup", "email", "log", "intents", "perf", "task")
            # M3.45.1: 若样本有 _conf 字段(confidence_teacher.py 产出),
            # target 加 confidence 后缀(Jev 风格)
            conf = obj.get("_conf")
            target = build_target(
                obj["risk_label"],
                bool(obj.get("jailbreak_label", False)),
                action=obj.get("_action") if has_action else None,
                conf=conf,
            )
            prompt = tmpl.format(text=text)
            full = prompt + target + "\n"

            # tokenize 整段
            full_ids = tokenizer(full, truncation=True,
                                 max_length=max_len, add_special_tokens=False
                                 )["input_ids"]
            prompt_ids = tokenizer(prompt, truncation=True,
                                   max_length=max_len,
                                   add_special_tokens=False)["input_ids"]
            # mask prompt 部分(loss 只算 target)
            labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids):]
            # 长度对齐(防止 tokenizer 二次切分对不上)
            if len(labels) != len(full_ids):
                labels = full_ids[:]   # 退化:全部算 loss(数据量小无所谓)
            samples.append({
                "input_ids": full_ids,
                "labels": labels,
                "attention_mask": [1] * len(full_ids),
            })
    return samples


def collate_fn(batch: list[dict], pad_id: int) -> dict:
    """pad 到 batch 内最长。"""
    import torch
    max_len = max(len(s["input_ids"]) for s in batch)
    for s in batch:
        pad = max_len - len(s["input_ids"])
        s["input_ids"] += [pad_id] * pad
        s["labels"] += [-100] * pad
        s["attention_mask"] += [0] * pad
    return {
        "input_ids": torch.tensor([s["input_ids"] for s in batch],
                                  dtype=torch.long),
        "labels": torch.tensor([s["labels"] for s in batch],
                               dtype=torch.long),
        "attention_mask": torch.tensor([s["attention_mask"] for s in batch],
                                        dtype=torch.long),
    }


def main() -> int:
    import torch
    from transformers import (
        AutoTokenizer,
        AutoModelForCausalLM,
        TrainingArguments,
        Trainer,
        BitsAndBytesConfig,
    )

    print(f"[1/5] 加载 tokenizer: {args.base_model}")
    tok = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    print(f"[2/5] 加载模型(4bit)...")
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb,
        device_map="auto",
        trust_remote_code=True,
    )
    model.config.use_cache = False   # 训练时禁用

    # LoRA 包装
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    model = prepare_model_for_kbit_training(model)
    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        bias="none", task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    print(f"[3/5] 加载数据集: {args.data} (schema={args.schema})")
    train_data = load_dataset(args.data, tok, args.max_len, args.schema)
    print(f"  + {len(train_data)} 训练样本")
    if not train_data:
        print("ERROR: 数据集为空,先跑 data_prep_*.py")
        return 1

    print(f"[4/5] 训练: epochs={args.epochs} batch={args.batch}")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    # transformers 5.x:warmup_steps 配 cosine 即可(cosine 自带 warmup)
    targs = TrainingArguments(
        output_dir=str(out),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=2,
        learning_rate=args.lr,
        warmup_steps=10,
        lr_scheduler_type="cosine",
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=2,
        bf16=False,                  # T4 不支持 bf16
        fp16=True,
        optim="paged_adamw_8bit",     # 4bit 训练必须用 paged
        report_to=[],
        seed=args.seed,
    )

    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=train_data,
        data_collator=lambda b: collate_fn(b, tok.pad_token_id),
    )
    trainer.train()

    # 保存 LoRA adapter(小,几 MB)
    print("[5/5] 保存 LoRA adapter")
    model.save_pretrained(out / "adapter")
    tok.save_pretrained(out / "adapter")

    # 合并 adapter 回 base model + save(完整 HF 格式,几 GB)
    print("[5/5] merge adapter + save full model")
    merged = model.merge_and_unload()
    merged.save_pretrained(out / "merged", safe_serialization=True)
    tok.save_pretrained(out / "merged")

    # 可选:导出 GGUF 给 llama.cpp 跑
    if args.quant_gguf:
        try:
            print("[5/5] 转 GGUF Q4_K_M ...")
            os.system(f"python llama.cpp/convert_hf_to_gguf.py "
                      f"{out}/merged --outfile {out}/merged-q4.gguf "
                      f"--quantize Q4_K_M")
        except Exception as e:  # noqa: BLE001
            print(f"  gguf 转换失败(可忽略,先跑 HF 推理): {e}")

    print(f"\n✅ 训练完成: {out}/merged/")
    print(f"  size: {(out / 'merged').stat().st_size / 1e9:.1f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())