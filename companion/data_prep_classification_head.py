#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_prep_classification_head.py — M3.66 L2 数据准备(2026-09-23 v2)

把现有 8 scenario jsonl 数据 → AgentJev 训练格式(synth.py schema)。

输入 jsonl (companion/data_prep_*.py 输出):
  {"text": "...", "risk_label": "high", "_action": "alert"}

输出 jsonl (AgentJev):
  {"id": "...", "env": "synthetic",
   "state": "[STATE] user_msg",
   "questions": [{"text": "intent/risk/task 指令",
                  "candidates": ["chat","code",...],
                  "gold": {"distribution": [0.05,0.9,0.02,0.02,0.01]},
                  "supervision": "known_distribution",
                  "weight": 1.0}]}

AgentJev 期望(data.py docstring):
  - state 字段必须是 string,自动前缀 [STATE]
  - questions[].text 自由文本指令
  - questions[].candidates 字符串列表
  - questions[].gold.distribution OR counts
  - questions[].supervision in SUP_CODES(known_distribution / binomial_counts 等)
  - questions[].weight 0-1

Usage:
  python data_prep_classification_head.py \\
      --in data/data_intents.jsonl \\
      --out data/data_intents_ch.jsonl \\
      --schema intents \\
      --limit 500
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


# Schema 配置:每 scenario 对应一套 candidate labels + 字段抽取
SCHEMAS: dict = {
    "intents": {
        # candidates 是 5 个意图 label(M3.50 老 jsonl 用 _intent 字段)
        "candidates": ["chat", "code", "search", "tool_call", "roleplay"],
        "label_field": "_intent",      # M3.50 老 data_intents.jsonl 字段名
        "question_text": "用户消息意图分类(chat/code/search/tool_call/roleplay)",
        # 锐化:正确标签 0.95,其它各 0.05/N,模拟 "deterministic"
        "sharpness": 0.95,
    },
    "task": {
        "candidates": ["code_call", "code_qa", "creative", "long", "fast", "general"],
        "label_field": "_intent",      # M3.58 老 data_task.jsonl 字段名(同 _intent)
        "question_text": "用户消息任务分类(code_call/code_qa/creative/long/fast/general)",
        "sharpness": 0.95,
    },
    "disk_cleanup": {
        "candidates": ["safe", "low", "medium", "high", "critical"],
        "label_field": "risk_label",
        "question_text": "Windows 系统文件清理风险等级(safe/low/medium/high/critical)",
        "sharpness": 0.95,
    },
    "tempfile": {
        "candidates": ["safe", "low", "medium", "high", "critical"],
        "label_field": "risk_label",
        "question_text": "临时文件清理风险等级(safe/low/medium/high/critical)",
        "sharpness": 0.95,
    },
    "email": {
        "candidates": ["safe", "low", "medium", "high", "critical"],
        "label_field": "risk_label",
        "question_text": "邮件风险等级(safe/low/medium/high/critical)",
        "sharpness": 0.95,
    },
    "log": {
        "candidates": ["safe", "low", "medium", "high", "critical"],
        "label_field": "risk_label",
        "question_text": "日志条目风险等级(safe/low/medium/high/critical)",
        "sharpness": 0.95,
    },
    "perf": {
        "candidates": ["safe", "low", "medium", "high", "critical"],
        "label_field": "risk_label",
        "question_text": "本地性能采样风险等级(safe/low/medium/high/critical)",
        "sharpness": 0.95,
    },
    "safety": {
        # safety 是二元:safe / unsafe(jailbreak)
        "candidates": ["safe", "unsafe"],
        "label_field": "jailbreak_label",  # True → unsafe, False → safe
        "question_text": "用户消息是否包含越狱或提示注入攻击(safe/unsafe)",
        "sharpness": 0.95,
    },
}


def make_distribution(label: str, candidates: list[str], sharpness: float) -> list[float]:
    """已知正确标签 distribution:[sharpness,其它各 (1-sharpness)/(N-1)]。"""
    n = len(candidates)
    rest = (1.0 - sharpness) / max(1, n - 1)
    out = [rest] * n
    if label in candidates:
        out[candidates.index(label)] = sharpness
    return out


def convert_sample(schema: str, sample: dict, idx: int) -> dict | None:
    """把 jsonl 一行转 AgentJev 训练格式(state + questions + gold)。"""
    cfg = SCHEMAS[schema]
    text = sample.get("text") or sample.get("user_msg", "")
    if not text:
        return None

    label = sample.get(cfg["label_field"])
    # safety: 布尔值 → safe / unsafe 字符串映射
    if schema == "safety":
        if label is True:
            label = "unsafe"
        elif label is False:
            label = "safe"
        else:
            return None
    if not label or label not in cfg["candidates"]:
        return None

    # state: 完整文本(自动加 [STATE] 前缀由 collate 处理)
    state = text[:4000]  # 截断到合理长度

    # 单一 question + 5 candidates
    questions = [{
        "text": cfg["question_text"],
        "candidates": cfg["candidates"],
        "gold": {"distribution": make_distribution(
            label, cfg["candidates"], cfg["sharpness"])},
        "supervision": "known_distribution",
        "weight": 1.0,
    }]

    return {
        "id": f"{schema}-{idx:06d}",
        "env": "synthetic",
        "source": schema,
        "state": state,
        "questions": questions,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--schema", required=True, choices=list(SCHEMAS.keys()))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    out = []
    skipped = 0
    with open(args.inp, encoding="utf-8") as f:
        for idx, raw_line in enumerate(f):
            line = raw_line.strip()
            if not line:
                continue
            s = json.loads(line)
            conv = convert_sample(args.schema, s, idx)
            if conv:
                out.append(conv)
            else:
                skipped += 1
            if args.limit and len(out) >= args.limit:
                break

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in out:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"✅ {args.schema}: {len(out)} samples → {args.out} (skipped: {skipped})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())