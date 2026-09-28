#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_laya_deadlock.py — M3.82 laya zero-shot 4 分类 deadlock bench (rev3)

目的:
  - deadlock_detect.py 当前 3 条硬规则,本脚本用 laya.triage_questions() 5 维
    决策 → 4 分类(stalled / zero_progress / slow / ok)
  - 跑 eval 样本,算 ACC / parse_fail / latency
  - 输出 JSON 报告,作为 race_impl 切换阈值(conf≥0.7)的依据

设计:
  - 用 triage_questions():intent / is_urgent / frustration / refund_requested / churn_risk
  - 实测 signal:stalled frustration 1.5+ urgency 0.1+; zp urgency 0.2+; slow 中等
  - 5 维 → label 映射:
      frustration ≥ 1.5 + urgency ≥ 0.1 → stalled
      urgency ≥ 0.2 + frustration ≥ 1.0 → zero_progress
      frustration ≥ 1.3 + churn_risk ≥ 0.3 → slow
      其它                              → ok

用法:
  python bench_laya_deadlock.py --limit 50
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_DATA_DIR = _HERE / "data"

# 把 companion/ 加入 sys.path(laya 装在那里)
_COMPANION = Path("C:/Users/Administrator/oi_enhancements/companion").resolve()
if str(_COMPANION) not in sys.path:
    sys.path.insert(0, str(_COMPANION))


# ============================================================
# 5 维 laya answer → 4 类 deadlock 标签
# ============================================================

# triage_questions() 维度:
#   intent       : choice
#   is_urgent    : noul 0-1
#   frustration  : score 0-3
#   refund_requested: noul 0-1
#   churn_risk   : noul 0-1


def _answers_to_label(answers: dict[str, Any]) -> tuple[str, float]:
    """把 laya.triage_questions() 的 5 维输出映射成 4 类 deadlock。

    决策:
      - frustration ≥ 1.5 + (urgency ≥ 0.1 OR churn_risk ≥ 0.2)  → stalled
      - urgency ≥ 0.2 + frustration ≥ 1.0                         → zero_progress
      - frustration ≥ 1.3 + churn_risk ≥ 0.3                      → slow
      - 其它                                                      → ok

    返回: (label, confidence)
    """
    intent = answers.get("intent", {}).get("choice") or ""
    urg = answers.get("is_urgent", {}).get("noul", 0.0) or 0.0
    fru = answers.get("frustration", {}).get("score", 0.0) or 0.0
    refund = answers.get("refund_requested", {}).get("noul", 0.0) or 0.0
    churn = answers.get("churn_risk", {}).get("noul", 0.0) or 0.0

    # stalled:高 frustration + 紧迫(长时间无进展,用户已感知)
    if fru >= 1.5 and (urg >= 0.1 or churn >= 0.2):
        return "stalled", min(0.9, 0.5 + fru * 0.2)
    if fru >= 1.8:
        return "stalled", min(0.85, 0.45 + fru * 0.2)

    # zero_progress:中等 urgency + frustration(无 file_ops 但在 LLM 转)
    if urg >= 0.2 and fru >= 1.0:
        return "zero_progress", min(0.85, 0.4 + urg * 0.5 + (fru - 1.0) * 0.2)
    if urg >= 0.25 and fru >= 0.9:
        return "zero_progress", min(0.8, 0.4 + urg * 0.4)

    # slow:frustration 中等 + churn 高(用户开始不满但还没挂起)
    if fru >= 1.3 and churn >= 0.3:
        return "slow", min(0.8, 0.4 + fru * 0.15)
    if fru >= 1.2 and fru < 1.5 and urg < 0.2:
        return "slow", min(0.75, 0.35 + fru * 0.15)

    # ok
    return "ok", max(0.4, min(0.8, 0.5 + (1.5 - fru) * 0.2))


# ============================================================
# 数据加载
# ============================================================

def load_eval_data(limit: int = 0) -> list[dict]:
    fp = _DATA_DIR / "data_deadlock_eval.jsonl"
    items = []
    with fp.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items[:limit] if limit else items


# ============================================================
# 跑 laya 评测
# ============================================================

def run_bench(router, qs, items: list[dict]) -> dict:
    correct = 0
    total = len(items)
    parse_fail = 0
    latencies: list[float] = []
    per_label = {lbl: {"total": 0, "correct": 0} for lbl in
                 ("stalled", "zero_progress", "slow", "ok")}
    errors: list[dict] = []

    for item in items:
        text = item.get("text", "")
        expect = item.get("label")
        per_label[expect]["total"] += 1

        state = {"message": text}
        t0 = time.time()
        try:
            res = router.predict(state, qs)
            elapsed = time.time() - t0
            latencies.append(elapsed)
            answers = res.get("answers", {})
            if not answers:
                parse_fail += 1
                errors.append({"task_id": item.get("task_id"), "expect": expect,
                               "got": None, "reason": "empty answers"})
                continue
            got, conf = _answers_to_label(answers)
            if got == expect:
                correct += 1
                per_label[expect]["correct"] += 1
            else:
                errors.append({"task_id": item.get("task_id"), "expect": expect,
                               "got": got, "conf": round(conf, 3)})
        except Exception as e:  # noqa: BLE001
            parse_fail += 1
            latencies.append(time.time() - t0)
            errors.append({"task_id": item.get("task_id"), "expect": expect,
                           "got": None, "reason": repr(e)[:200]})

    acc = correct / total if total else 0
    avg_lat = sum(latencies) / len(latencies) if latencies else 0
    p50 = sorted(latencies)[len(latencies) // 2] if latencies else 0

    per_label_acc = {lbl: (d["correct"] / d["total"] if d["total"] else 0)
                     for lbl, d in per_label.items()}

    return {
        "total": total,
        "correct": correct,
        "parse_fail": parse_fail,
        "overall_acc": round(acc, 4),
        "per_label_acc": {k: round(v, 4) for k, v in per_label_acc.items()},
        "per_label_total": {lbl: d["total"] for lbl, d in per_label.items()},
        "avg_latency_ms": round(avg_lat * 1000, 1),
        "p50_latency_ms": round(p50 * 1000, 1),
        "errors": errors[:15],
    }


def main():
    parser = argparse.ArgumentParser(description="laya 4 分类 deadlock bench (router_questions)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    from laya import Router, triage_questions
    print("loading laya Router (multilingual)...", flush=True)
    router = Router(default="multilingual", preload=True)
    qs = triage_questions()
    print("router ready\n", flush=True)

    items = load_eval_data(args.limit)
    print(f"=== deadlock 4-class bench ({len(items)} items, triage_questions) ===", flush=True)
    t0 = time.time()
    result = run_bench(router, qs, items)
    result["elapsed_total_s"] = round(time.time() - t0, 1)

    print(f"  ACC={result['overall_acc']:.2%}  "
          f"parse_fail={result['parse_fail']}/{result['total']}  "
          f"avg_lat={result['avg_latency_ms']:.0f}ms  "
          f"p50={result['p50_latency_ms']:.0f}ms")
    for lbl in ("stalled", "zero_progress", "slow", "ok"):
        tot = result["per_label_total"][lbl]
        acc = result["per_label_acc"][lbl]
        print(f"    {lbl:14s}  {acc:.2%}  ({tot} total)")

    if args.output:
        out_p = Path(args.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        print(f"\n→ report saved to {out_p}")


if __name__ == "__main__":
    raise SystemExit(main())
