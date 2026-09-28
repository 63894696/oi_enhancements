#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# bench_intents_local.py — M3.50 本地 intents adapter 评测(2026-09-23)
#
# 目的:
#   - 复用 bench_intents.py:47-107 的 50 条 TEST_CASES(对齐 Jev 评测基线)
#   - 跑本地 intents_conf adapter,出 accuracy/per-class/confusion/latency/parse_fail
#   - 与 reports/bench_intents_2026-09-22.json 对比(Jev 0.94 / p50 1147ms)
#
# 设计:
#   - 复用 classify_intents.classify_intents()(跟生产同代码路径)
#   - 不引冷启动开销,前 3 条 warm-up 不计入
#   - parse_fail = "intent" not in {chat,code,search,tool_call,roleplay}
#
# 用法:
#   python bench_intents_local.py
#   python bench_intents_local.py --output reports/bench_intents_local_$(date +%Y%m%d).json
#   python bench_intents_local.py --cases chat,code
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from classify_intents import classify_intents, VALID_INTENTS  # noqa: E402
from test_fixtures import load_fixture  # noqa: E402


# M3.60 L4 改:从 test_fixtures/intents_jev_compat.json 读,与 bench_intents 共用同一题集
TEST_CASES: list[dict] = load_fixture("intents_jev_compat")


def _aggregate(results: list[dict]) -> dict:
    """聚合:总精度/每类精度/混淆矩阵/置信度分桶/延迟分位/parse_fail。"""
    n = len(results)
    matches = sum(1 for r in results if r["match"])
    accuracy = matches / n if n else 0.0
    parse_fail = sum(1 for r in results if r["parse_fail"]) / n

    # 每类精度
    by_class: dict[str, dict] = {}
    for r in results:
        e = r["expect"]
        by_class.setdefault(e, {"total": 0, "match": 0})
        by_class[e]["total"] += 1
        if r["match"]:
            by_class[e]["match"] += 1
    for d in by_class.values():
        d["accuracy"] = round(d["match"] / d["total"], 4) if d["total"] else 0.0

    # 混淆矩阵
    classes = sorted(VALID_INTENTS | {"unknown"})
    confusion: dict[str, dict[str, int]] = {
        c: {a: 0 for a in classes} for c in classes
    }
    for r in results:
        e = r["expect"] if r["expect"] in classes else "unknown"
        a = r["actual"] if r["actual"] in classes else "unknown"
        confusion[e][a] += 1

    # 延迟分位(去掉 3 条 warm-up)
    real = [r for r in results if not r["warmup"]]
    latencies = [r["elapsed_ms"] for r in real]
    p50 = int(statistics.median(latencies)) if latencies else 0
    p95 = (sorted(latencies)[int(len(latencies) * 0.95)]
           if len(latencies) >= 20 else (max(latencies) if latencies else 0))

    # 置信度分桶(<0.5 / 0.5-0.8 / >=0.8)
    buckets = {"<0.5": 0, "0.5-0.8": 0, ">=0.8": 0}
    for r in real:
        c = r["action_conf"]
        if c < 0.5:
            buckets["<0.5"] += 1
        elif c < 0.8:
            buckets["0.5-0.8"] += 1
        else:
            buckets[">=0.8"] += 1

    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n": n,
        "accuracy": round(accuracy, 4),
        "parse_fail_rate": round(parse_fail, 4),
        "by_class": by_class,
        "confusion_matrix": confusion,
        "latency_ms": {
            "p50": p50, "p95": int(p95),
            "max": max(latencies) if latencies else 0,
            "min": min(latencies) if latencies else 0,
        },
        "confidence_buckets": buckets,
        "wrong_samples": [
            {"text": r["text"][:30], "expect": r["expect"],
             "actual": r["actual"], "action_conf": r["action_conf"]}
            for r in results if not r["match"]
        ],
        "details": results,
    }


def run_bench(cases: list[dict]) -> dict:
    """跑 50 条测试,出聚合报告。"""
    print(f"=== M3.50 本地 intents adapter 评测 {len(cases)} 条 ===")
    results: list[dict] = []
    for i, case in enumerate(cases, 1):
        text = case["text"]
        warmup = i <= 3
        t0 = time.time()
        try:
            out = classify_intents(text, use_conf=True)
        except Exception as e:  # noqa: BLE001
            out = {"intent": "unknown", "action_conf": 0.0,
                   "risk_label": "unknown", "jailbreak_label": False,
                   "raw": "", "tokens": 0, "error": str(e)}
        elapsed_ms = int((time.time() - t0) * 1000)
        actual = out.get("intent", "unknown")
        match = (actual == case["expect"])
        parse_fail = actual not in VALID_INTENTS
        results.append({
            "i": i,
            "expect": case["expect"],
            "actual": actual,
            "match": match,
            "parse_fail": parse_fail,
            "action_conf": out.get("action_conf", 0.0),
            "elapsed_ms": elapsed_ms,
            "warmup": warmup,
            "text": text,
            "raw": out.get("raw", "")[:80],
        })
        sym = "✓" if match else ("?" if parse_fail else "✗")
        warm = " [warmup]" if warmup else ""
        print(f"  {sym} [{i:>2}/{len(cases)}] expect={case['expect']:<10} "
              f"actual={actual:<10} conf={out.get('action_conf', 0):.2f} "
              f"({elapsed_ms}ms){warm} \"{text[:24]}...\"")

    return _aggregate(results)


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.50 本地 intents 评测")
    ap.add_argument("--cases", default=None,
                    help="只跑某 expect(逗号分隔),如 chat,code")
    ap.add_argument("--output", default=None,
                    help="报告输出文件(默认打印到 stdout)")
    args = ap.parse_args()

    cases = TEST_CASES
    if args.cases:
        wanted = set(s.strip() for s in args.cases.split(","))
        cases = [c for c in TEST_CASES if c["expect"] in wanted]
        if not cases:
            print(f"❌ 无匹配 expect: {args.cases}")
            return 2

    report = run_bench(cases)

    print()
    print("=" * 60)
    print(f"M3.50 intents_local 评测汇总 ({report['n']} 条)")
    print("=" * 60)
    print(f"整体精度:        {report['accuracy']*100:.1f}%")
    print(f"parse_fail:      {report['parse_fail_rate']*100:.1f}%")
    print(f"延迟 p50/p95:    {report['latency_ms']['p50']}ms / "
          f"{report['latency_ms']['p95']}ms")
    print()
    print("每类精度:")
    for c, d in report["by_class"].items():
        print(f"  {c:<10}  {d['match']}/{d['total']}  "
              f"({d['accuracy']*100:.0f}%)")
    print()
    print("对比 Jev 基线(bench_intents_2026-09-22.json):")
    print("  Jev:   accuracy=0.94  p50=1147ms")
    print(f"  本地:   accuracy={report['accuracy']:.2f}  "
          f"p50={report['latency_ms']['p50']}ms")
    print()
    print("置信度分桶:")
    for b, n in report["confidence_buckets"].items():
        print(f"  {b:<10} {n}")
    if report["wrong_samples"]:
        print()
        print(f"误判 {len(report['wrong_samples'])} 条样本:")
        for s in report["wrong_samples"]:
            print(f"  - \"{s['text']}\" expect={s['expect']} → "
                  f"actual={s['actual']} conf={s['action_conf']:.2f}")

    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"\n✅ 报告: {args.output}")
    else:
        print("\n--- JSON brief ---")
        brief = {k: v for k, v in report.items() if k != "details"}
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())