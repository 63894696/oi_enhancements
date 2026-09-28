#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# confidence_teacher.py — 给训练集每条样本加 confidence teacher signal(2026-09-23)
#
# 目的:
#   - Jev 输出 calibrated confidence(soft prob),我们当前输出硬标签
#   - 仿 Jev,补出 :0.xx 后缀,让下游能做 threshold gating
#   - teacher signal 从哪来:
#     选项 A(本脚本):用 **已训 adapter 自己**(self-teacher)
#       跑一次 greedy decode,看 target 第一个 token 的 softmax prob
#       → 表示"该 adapter 对该样本的自信程度"
#     选项 B:用 base model(没有针对性,不可取)
#     选项 C:用 base + prompt only(Jev 原始方式,但需要复杂 teacher 网络)
#   - 选 A:简单、可信,直接复用现有 adapter
#
# 输入:data_*.jsonl + 现有 adapter 路径
# 输出:同路径 _conf.jsonl(JSONL 每行多 _conf 字段)
#
# 用法(aliyun T4):
#   python3 confidence_teacher.py \
#     --data /workspace/data_disk_cleanup.jsonl \
#     --base /workspace/models/Qwen3Guard-Gen-0.6B \
#     --adapter /workspace/qwen3guard-disk-cleanup/adapter \
#     --output /workspace/data_disk_cleanup_conf.jsonl
#
# 设计:
#   - 对每条样本拼 prompt + target → 喂进 model
#   - 算 target 第一行("Safety:")之后的第一个 token(label token)的 softmax 概率
#   - 三字段都跑一次(risk / jailbreak / action),各取一次 token prob
#   - 由于 0.6B 模型复读严重,teacher signal 会偏低(0.3-0.5 常见),但**相对排序有意义**
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel


# target 模板片段(跟 train_step1.PROMPT_TEMPLATE_TEMPFILE / PROMPT_TEMPLATE_SAFETY 匹配)
PROMPT_TEMPLATE_SAFETY = "user\n{text}\nassistant\n"
PROMPT_TEMPLATE_TEMPFILE = "user\n{text}\nassistant\n"


def build_target_tokens(tokenizer, risk_label: str, jailbreak_label: bool,
                       action: str | None = None) -> list[list[int]]:
    """返三个 token id 段:risk / jailbreak / action 各自的 token 序列。

    例:Safety: Critical → ['Safety', ':', ' Critical']
    每段都拼上尾部 \\n,因为训练时 target 是 "Safety: Critical\\nJailbreak: No\\n..."
    """
    jb = "Yes" if jailbreak_label else "No"
    parts = [
        f"Safety: {risk_label.capitalize()}\n",
        f"Jailbreak: {jb}\n",
    ]
    if action:
        parts.append(f"Action: {action.capitalize()}\n")
    tokenized = []
    for s in parts:
        ids = tokenizer(s, add_special_tokens=False)["input_ids"]
        tokenized.append(ids)
    return tokenized


def get_target_probs(model, tokenizer, prompt: str, target_segs: list[list[int]],
                     device: str = "cuda") -> list[float]:
    """跑一次 forward(prompt + target_seqs 拼起来),取每段第一个 token 的 softmax 概率。

    target_segs:每个段是 token id 列表,我们取该段第一个 token(段首)。
    **关键偏移**:logits[i] 预测位置 i+1 的 token。
    所以 target 第一段首 token 在序列位置 p_len,要从 logits[p_len - 1] 取。
    """
    # 把 prompt tokenize(必须 .to(device))
    prompt_ids = tokenizer(prompt, return_tensors="pt",
                           add_special_tokens=False)["input_ids"].to(device)
    p_len = prompt_ids.shape[1]

    # 把 target 拼起来
    target_ids_full = []
    for seg in target_segs:
        target_ids_full.extend(seg)
    target_ids = torch.tensor([target_ids_full],
                              device=device, dtype=torch.long)

    # 拼起来 forward
    full_ids = torch.cat([prompt_ids, target_ids], dim=1)
    with torch.inference_mode():
        out = model(full_ids)
        logits = out.logits  # [1, seq, vocab]

    probs = []
    # logits[pos] 预测 full_ids[pos + 1] 的 token
    # target 第一段首 token 在序列位置 p_len
    # 所以要从 logits[p_len - 1] 取
    cur_pos = p_len - 1
    for seg in target_segs:
        if cur_pos >= logits.shape[1]:
            probs.append(0.0)
            continue
        logit = logits[0, cur_pos, :]
        softmax = torch.softmax(logit, dim=-1)
        target_token = seg[0]   # 段首 token
        prob = float(softmax[target_token].item())
        probs.append(prob)
        cur_pos += len(seg)
    return probs


def main() -> int:
    ap = argparse.ArgumentParser(
        description="给训练集每条样本加 confidence teacher signal")
    ap.add_argument("--data", required=True, help="输入 JSONL")
    ap.add_argument("--base", required=True, help="base model 路径")
    ap.add_argument("--adapter", required=True, help="adapter 路径")
    ap.add_argument("--output", required=True, help="输出 JSONL 路径")
    ap.add_argument("--schema", choices=["safety", "tempfile", "disk_cleanup",
                                          "email", "log", "intents", "perf", "task"],
                    default="tempfile")
    ap.add_argument("--limit", type=int, default=None,
                    help="可选,只处理前 N 条(调试)")
    args = ap.parse_args()

    # 加载
    print(f"[1/3] 加载 {args.base} + adapter {args.adapter}")
    tok = AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    base = AutoModelForCausalLM.from_pretrained(
        args.base, trust_remote_code=True,
        torch_dtype=torch.float16, device_map="auto")
    model = PeftModel.from_pretrained(base, args.adapter)
    model.eval()

    # 读数据
    rows = []
    with open(args.data, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if args.limit:
        rows = rows[:args.limit]
    print(f"[2/3] 跑 {len(rows)} 条 teacher signal")

    # 处理每条
    tmpl = (PROMPT_TEMPLATE_SAFETY if args.schema == "safety"
            else PROMPT_TEMPLATE_TEMPFILE)
    has_action = args.schema in ("tempfile", "disk_cleanup", "email", "log", "intents", "perf", "task")

    out_path = Path(args.output)
    n_done = 0
    risk_probs, jb_probs, action_probs = [], [], []

    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            prompt = tmpl.format(text=r["text"])
            action = r.get("_action") if has_action else None
            target_segs = build_target_tokens(
                tok, r["risk_label"],
                bool(r.get("jailbreak_label", False)),
                action=action)
            probs = get_target_probs(model, tok, prompt, target_segs)
            # probs = [risk_prob, jb_prob, (action_prob 可选)]
            conf = {
                "risk": round(probs[0], 4),
                "jailbreak": round(probs[1], 4),
            }
            if has_action and len(probs) >= 3:
                conf["action"] = round(probs[2], 4)
                action_probs.append(probs[2])
            risk_probs.append(probs[0])
            jb_probs.append(probs[1])

            r["_conf"] = conf
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n_done += 1
            if n_done % 50 == 0:
                print(f"  + {n_done}/{len(rows)}")

    # 摘要
    def stats(xs):
        if not xs:
            return {}
        xs = sorted(xs)
        n = len(xs)
        return {
            "min": round(xs[0], 4),
            "p25": round(xs[n // 4], 4),
            "p50": round(xs[n // 2], 4),
            "p75": round(xs[3 * n // 4], 4),
            "max": round(xs[-1], 4),
            "mean": round(sum(xs) / n, 4),
        }

    summary = {
        "n": n_done,
        "schema": args.schema,
        "risk_conf": stats(risk_probs),
        "jailbreak_conf": stats(jb_probs),
        "action_conf": stats(action_probs),
    }
    print(f"\n[3/3] ✅ 写盘 {out_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())