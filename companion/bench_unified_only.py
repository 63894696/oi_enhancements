#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_unified_only.py — 只跑 unified head bench(2026-09-24)

复用 bench_8heads_vs_unified 的 SCENARIOS + bench_one,但跳过 8 head 加载
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from test_fixtures import load_fixture

SCENARIOS = [
    ("disk_cleanup", "disk_cleanup_independent", "risk_label"),
    ("tempfile",     "tempfile_independent",     "risk_label"),
    ("email",        "email_independent",        "risk_label"),
    ("log",          "log_independent",          "risk_label"),
    ("perf",         "perf_independent",         "expect"),
    ("intents",      "intents_jev_compat",       "expect"),
    ("task",         "task_jev_compat",          "expect"),
    ("safety",       "safety_jev_compat",        "expect"),
]


def main() -> int:
    print("=== unified head bench (skip 8 head) ===\n", file=sys.stderr)
    from agentjev_runtime import infer_scenario

    results = []
    for scene, fixture_name, truth_key in SCENARIOS:
        cases = load_fixture(fixture_name)
        n = len(cases)
        correct = 0
        parse_fails = 0
        latencies = []
        per_class_correct = {}
        per_class_total = {}
        t0 = time.time()

        for tc in cases:
            truth = tc.get(truth_key)
            if not truth:
                continue
            per_class_total[truth] = per_class_total.get(truth, 0) + 1
            try:
                state = tc.get("text", "")
                out = infer_scenario(scene, state)
                pred = out["top"]
                latencies.append(out["latency_ms"])
            except Exception as e:
                parse_fails += 1
                pred = "<error>"
                latencies.append(0.0)
                print(f"    [{scene}] err: {e}", file=sys.stderr)
            if pred == truth:
                correct += 1
                per_class_correct[truth] = per_class_correct.get(truth, 0) + 1

        total_s = time.time() - t0
        latencies.sort()
        r = {
            "scene": scene,
            "model": "unified",
            "n": n,
            "correct": correct,
            "accuracy": round(correct / n, 4) if n else 0,
            "parse_fail_rate": round(parse_fails / n, 4) if n else 0,
            "per_class": {k: {"correct": per_class_correct.get(k, 0), "total": v,
                              "accuracy": round(per_class_correct.get(k, 0) / v, 4) if v else 0}
                           for k, v in per_class_total.items()},
            "latency_ms": {"p50": latencies[len(latencies)//2] if latencies else 0,
                           "min": latencies[0] if latencies else 0,
                           "max": latencies[-1] if latencies else 0,
                           "total_s": round(total_s, 1)},
        }
        results.append(r)
        print(f"  ✅ {scene}: ACC={r['accuracy']} ({r['correct']}/{r['n']}) "
              f"p50={r['latency_ms']['p50']:.0f}ms "
              f"parse_fail={r['parse_fail_rate']*100:.0f}% "
              f"total={r['latency_ms']['total_s']:.0f}s", file=sys.stderr)

    valid = [r for r in results if "error" not in r]
    avg = round(sum(r["accuracy"] for r in valid) / max(1, len(valid)), 4)
    summary = {
        "model": "unified AgentJev classification-head (跨 8 scenario, 单 model)",
        "n_scenes": len(results),
        "avg_accuracy": avg,
        "scenes": results,
    }
    print(f"\n=== unified 汇总 ===", file=sys.stderr)
    print(f"  平均 ACC = {avg*100:.1f}% ({len(valid)}/{len(results)} scenario)", file=sys.stderr)

    out_path = _HERE / "reports" / "bench_unified_2026-09-24.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"  报告: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())