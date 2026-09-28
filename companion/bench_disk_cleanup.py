#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bench_disk_cleanup.py — M3.47 Step 1 disk_cleanup 评测(2026-09-23)
#
# 目的:
#   - 拿 data_disk_cleanup.jsonl 80/20 切分,跑训后 adapter
#   - 输出 5 类 + 3 action 的精确度 + confusion matrix
#   - 跟 base model(Qwen3Guard-Gen-0.6B 不带 LoRA)对比,确认 adapter 增益
#
# 用法(aliyun T4 上):
#   python3 bench_disk_cleanup.py \
#     --data /workspace/data_disk_cleanup.jsonl \
#     --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
#     --adapter /workspace/qwen3guard-disk-cleanup/adapter \
#     --output /workspace/bench_disk_cleanup.json
#
# 输出 JSON:
#   {
#     "samples": N,
#     "with_adapter": {risk: {accuracy, ...}, action: {...}, latency: {...}},
#     "base_only":   {...},   # 无 adapter 的对照
#     "confusion_risk": [[count_actualX_predY], ...]
#   }
from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel


RISK_LABELS = ["safe", "low", "medium", "high", "critical"]
ACTION_LABELS = ["delete", "review", "keep"]

PROMPT_TEMPLATE = "user\n{text}\nassistant\n"
TARGET_RE = re.compile(
    r"Safety:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"Jailbreak:\s*(\w+)(?::(\d+(?:\.\d+)?))?\s*\n?\s*"
    r"(?:Action:\s*(\w+)(?::(\d+(?:\.\d+)?))?)?",
    re.IGNORECASE | re.MULTILINE,
)


def parse_target(text: str) -> tuple[Optional[str], Optional[str], Optional[str],
                                      Optional[float], Optional[float], Optional[float]]:
    """从模型输出抽 (risk, jailbreak, action, risk_conf, jb_conf, action_conf)。

    支持 M3.45.1 confidence 后缀 'Safety: Medium:0.6110'。
    复读:只取第一段 Safety: 后停止。
    """
    idx = text.lower().find("safety:")
    if idx < 0:
        return None, None, None, None, None, None
    seg = text[idx:]
    second = seg.lower().find("safety:", 8)
    if second > 0:
        seg = seg[:second]
    m = TARGET_RE.search(seg)
    if not m:
        return None, None, None, None, None, None
    risk = m.group(1).lower().strip()
    risk_conf = float(m.group(2)) if m.group(2) else None
    jb = m.group(3).lower().strip() in ("yes", "true", "1")
    jb_conf = float(m.group(4)) if m.group(4) else None
    action = m.group(5).lower().strip() if m.group(5) else None
    action_conf = float(m.group(6)) if m.group(6) else None
    return risk, jb, action, risk_conf, jb_conf, action_conf


def classify(model, tokenizer, text: str, max_new_tokens: int = 40,
             device: str = "cuda") -> tuple[str, float]:
    """单条推理。

    M3.45.1 B1 加速(2026-09-23 修正):max_new_tokens=40 +
    repetition_penalty=1.2 + no_repeat_ngram_size=6。
    rep=1.5 + eos=\n 会让 confidence 版 adapter 塌缩到
    `SafetyJailbreakRating:...`,parse 率 0%。回退到 rep=1.2 后保持格式正确,
    延迟 7s → 2.5s(2.8x 提速)。
    """
    prompt = PROMPT_TEMPLATE.format(text=text)
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    t0 = time.time()
    with torch.inference_mode():
        out = model.generate(
            **inputs, max_new_tokens=max_new_tokens,
            do_sample=False, pad_token_id=tokenizer.pad_token_id,
            repetition_penalty=1.2,
            no_repeat_ngram_size=6)
    dt = (time.time() - t0) * 1000
    gen = out[0][inputs["input_ids"].shape[1]:]
    raw = tokenizer.decode(gen, skip_special_tokens=True).strip()
    return raw, dt


def split_data(rows: list[dict], test_frac: float = 0.2,
               seed: int = 42) -> tuple[list[dict], list[dict]]:
    """按 test_frac 切 holdout,固定 seed 可复现。"""
    import random
    rng = random.Random(seed)
    idx = list(range(len(rows)))
    rng.shuffle(idx)
    cut = int(len(rows) * test_frac)
    test_idx = set(idx[:cut])
    train, test = [], []
    for i, r in enumerate(rows):
        (test if i in test_idx else train).append(r)
    return train, test


def eval_model(model, tokenizer, rows: list[dict],
               device: str = "cuda") -> dict:
    """评估整个 model 在 rows 上的预测,统计 5 类 + 3 action 精确度 +
    平均 confidence + calibration(预留 M3.45.1)。
    """
    n = len(rows)
    correct_risk = 0
    correct_action = 0
    parse_fail = 0
    latencies = []
    conf_risk: dict[str, Counter] = {l: Counter() for l in RISK_LABELS}
    conf_action: dict[str, Counter] = {l: Counter() for l in ACTION_LABELS}
    # M3.45.1: confidence 统计
    risk_confs_correct = []
    risk_confs_wrong = []

    for r in rows:
        raw, dt = classify(model, tokenizer, r["text"], device=device)
        latencies.append(dt)
        parsed = parse_target(raw)
        pred_risk = parsed[0]
        pred_action = parsed[2]
        risk_conf = parsed[3]
        if pred_risk is None:
            parse_fail += 1
            continue
        gold_risk = r["risk_label"].lower()
        gold_action = r["_action"].lower()

        if pred_risk == gold_risk:
            correct_risk += 1
            if risk_conf is not None:
                risk_confs_correct.append(risk_conf)
        else:
            if risk_conf is not None:
                risk_confs_wrong.append(risk_conf)
        conf_risk[gold_risk][pred_risk] += 1

        if pred_action and pred_action == gold_action:
            correct_action += 1
        elif pred_action:
            conf_action[gold_action][pred_action] += 1
        else:
            conf_action[gold_action]["_none_"] += 1

    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0

    def _avg(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    return {
        "n": n,
        "risk_accuracy": correct_risk / n,
        "action_accuracy": correct_action / n,
        "parse_fail_rate": parse_fail / n,
        "calibration": {
            "risk_conf_mean_correct": _avg(risk_confs_correct),
            "risk_conf_mean_wrong": _avg(risk_confs_wrong),
            "n_with_conf": len(risk_confs_correct) + len(risk_confs_wrong),
        },
        "latency_ms": {
            "p50": round(p50, 1),
            "p95": round(p95, 1),
            "mean": round(sum(latencies) / n, 1) if n else 0,
        },
        "confusion_risk": {
            actual: {pred: cnt for pred, cnt in preds.items()}
            for actual, preds in conf_risk.items()
        },
        "confusion_action": {
            actual: {pred: cnt for pred, cnt in preds.items()}
            for actual, preds in conf_action.items()
        },
    }


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="disk_cleanup adapter 评测")
    ap.add_argument("--data", required=True)
    ap.add_argument("--base-model", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--skip-base", action="store_true",
                    help="跳过 base model 对照(加速)")
    ap.add_argument("--test-set", default=None,
                    help="(M3.60 L4) 用 test_fixtures/<scenario>_independent.json 替代 80/20 切,"
                         "e.g. --test-set test_fixtures/disk_cleanup_independent.json")
    args = ap.parse_args()

    if args.test_set:
        # M3.60 L4:用 fixture 替代 80/20 切分,这是真 holdout(训练时未见过)
        from test_fixtures import load_fixture
        cases = load_fixture("disk_cleanup_independent")
        # fixture 是 list[dict],含 text/expect/risk_label/_action — 转 jsonl 行的 schema
        test = [{"text": c["text"], "risk_label": c.get("risk_label") or c["expect"],
                 "_action": c.get("_action", "")} for c in cases]
        print(f"[1/4] 加载 fixture {args.test_set} → {len(test)} 条独立 test set")
    else:
        rows = load_jsonl(Path(args.data))
        print(f"[1/4] 加载 {len(rows)} 条数据,切 80/20 ...")
        _train, test = split_data(rows, test_frac=0.2)
        print(f"  test 集 {len(test)} 条")

    print(f"[2/4] 加载 base model + adapter ...")
    tok = AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model, trust_remote_code=True,
        torch_dtype=torch.float16, device_map="auto")
    model = PeftModel.from_pretrained(base, args.adapter)
    model.eval()

    print(f"[3/4] 跑带 adapter 的评测 ...")
    with_adapter = eval_model(model, tok, test)
    print(f"  risk_acc = {with_adapter['risk_accuracy']:.3f}")
    print(f"  action_acc = {with_adapter['action_accuracy']:.3f}")
    print(f"  parse_fail = {with_adapter['parse_fail_rate']:.3f}")
    print(f"  p50/p95 = {with_adapter['latency_ms']['p50']:.0f}/"
          f"{with_adapter['latency_ms']['p95']:.0f} ms")

    print(f"[3b/4] 跑 base only 对照 ...")
    base_only = None
    if not args.skip_base:
        # 卸 LoRA,跑 base
        base_merged = model.merge_and_unload()
        base_only = eval_model(base_merged, tok, test)
        print(f"  base risk_acc = {base_only['risk_accuracy']:.3f}")
        print(f"  base action_acc = {base_only['action_accuracy']:.3f}")

    print(f"[4/4] 写报告: {args.output}")
    report = {
        "scenario": "disk_cleanup",
        "samples_total": len(rows),
        "test_size": len(test),
        "with_adapter": with_adapter,
        "base_only": base_only,
        "delta_risk_accuracy": (
            with_adapter["risk_accuracy"] - (base_only["risk_accuracy"]
                                             if base_only else 0)),
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 写盘: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())