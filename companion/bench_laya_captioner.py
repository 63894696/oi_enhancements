#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_laya_captioner.py — M3.81 laya zero-shot 评测 4 分类 captioner(2026-09-25)

目的:
  - 评测 laya zero-shot 在 4 分类 caption 上的 ACC
  - 输出 per-label + 混淆矩阵 + latency 报告
  - 供 vision_query race_impl 决定阈值(conf threshold)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


SCREEN_QS = {
    "screen_type": {
        "type": "choice",
        "instructions": "Classify the screen content described in `desc`.",
        "criteria": {
            "app": "a desktop application window with menus/toolbars (browser, file manager, IDE overall framework)",
            "focused": "a focused text input, terminal command prompt, or search box in progress",
            "code": "source code, technical text content (programming, scripts, markup)",
            "dialog": "a modal dialog, alert, popup, or system message overlay",
        },
    }
}

LABELS = ["app", "focused", "code", "dialog"]


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.81 laya captioner bench")
    ap.add_argument("--input", default="data_vision_caption.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--output", default="reports/bench_vision_caption_laya.json")
    args = ap.parse_args()

    samples = [json.loads(l) for l in Path(args.input).read_text(encoding="utf-8").splitlines()]
    if args.limit:
        samples = samples[:args.limit]
    print(f"loaded {len(samples)} samples from {args.input}")

    from laya import Router
    print("loading laya Router (multilingual)...", flush=True)
    router = Router(default="multilingual", preload=True)
    print("router ready\n", flush=True)

    agree = disagree = parse_fail = 0
    per_label = {l: {"total": 0, "correct": 0} for l in LABELS}
    confusion: dict[str, dict[str, int]] = {e: {g: 0 for g in LABELS} for e in LABELS}
    latencies: list[float] = []
    errors: list[dict] = []

    t0 = time.time()
    for i, s in enumerate(samples, 1):
        expect = s["risk_label"]
        per_label[expect]["total"] += 1
        try:
            t1 = time.time()
            res = router.predict({"desc": s["text"]}, SCREEN_QS)
            latencies.append((time.time() - t1) * 1000)
            st = res.get("answers", {}).get("screen_type", {})
            choice = st.get("choice")
            conf = st.get("answer_confidence") or 0
            if not choice or choice not in LABELS:
                parse_fail += 1
                errors.append({"text": s["text"][:50], "expect": expect, "got": None})
                continue
            confusion[expect][choice] += 1
            if choice == expect:
                agree += 1
                per_label[expect]["correct"] += 1
            else:
                disagree += 1
                errors.append({"text": s["text"][:50], "expect": expect, "got": choice,
                               "conf": round(conf, 3)})
        except Exception as e:
            parse_fail += 1
            errors.append({"text": s["text"][:50], "expect": expect, "error": str(e)[:100]})

        if i % 100 == 0:
            elapsed = time.time() - t0
            print(f"  {i}/{len(samples)} ({elapsed:.1f}s)", flush=True)

    total = len(samples)
    acc = agree / total if total else 0
    avg_lat = sum(latencies) / len(latencies) if latencies else 0
    p50 = sorted(latencies)[len(latencies) // 2] if latencies else 0

    print()
    print("=" * 60)
    print(f"ACC = {agree}/{total} = {acc:.1%}")
    print(f"parse_fail = {parse_fail}")
    print(f"avg_lat = {avg_lat:.0f}ms  p50 = {p50:.0f}ms")
    print()
    print("per-label ACC:")
    for lbl, d in per_label.items():
        a = d["correct"] / d["total"] if d["total"] else 0
        print(f"  {lbl:8s}  {a:6.1%}  ({d['correct']}/{d['total']})")
    print()
    print("confusion matrix (expect -> got):")
    print(f"  {'':10s} {' '.join(f'{l:9s}' for l in LABELS)}")
    for e in LABELS:
        row = "  ".join(f"{confusion[e][g]:9d}" for g in LABELS)
        print(f"  {e:10s} {row}")

    if errors:
        print(f"\nfirst {min(5, len(errors))} errors:")
        for e in errors[:5]:
            print(f"  {e}")

    report = {
        "input": args.input,
        "total": total,
        "agree": agree,
        "disagree": disagree,
        "parse_fail": parse_fail,
        "acc": round(acc, 4),
        "avg_latency_ms": round(avg_lat, 1),
        "p50_latency_ms": round(p50, 1),
        "per_label_acc": {lbl: round(d["correct"] / d["total"], 4) if d["total"] else 0
                          for lbl, d in per_label.items()},
        "per_label_total": {lbl: d["total"] for lbl, d in per_label.items()},
        "confusion_matrix": confusion,
        "errors_sample": errors[:10],
        "elapsed_s": round(time.time() - t0, 1),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())