#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_all_agentjev.py — M3.66 L3 unified AgentJev head 综合评测(2026-09-24)

1 个 unified head(覆盖 8 scenario 18 candidates),跑 8 个 scenario 的 test fixtures:
  - 复用 test_fixtures (M3.60)
  - 通过各 classify_*.py 模块的 _build_text 把 sample dict → state text
  - agentjev_runtime.infer_scenario(scene, state) → top + prob
  - 统计 ACC/per-class/latency

输出:reports/bench_all_agentjev_2026-09-24.json
对比:reports/bench_all_2026-09-22.json (LoRA v3 bench)
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# 复用 M3.60 test_fixtures
from test_fixtures import load_fixture

# 8 scenario fixture + classify_*.py 模块(_build_text) + truth_key
SCENARIOS = [
    # (scene_name, fixture_name, classify_module, truth_key)
    ("disk_cleanup", "disk_cleanup_independent", "classify_disk_cleanup", "risk"),
    ("tempfile",     "tempfile_independent",     "classify_tempfile",    "risk"),
    ("email",        "email_independent",        "classify_email",       "risk"),
    ("log",          "log_independent",          "classify_log",         "risk"),
    ("perf",         "perf_independent",         "classify_perf",        "risk"),
    ("intents",      "intents_jev_compat",       "classify_intents",     "_intent"),
    ("task",         "task_jev_compat",          "classify_task",        "_intent"),
    ("safety",       "safety_jev_compat",        "classify_safety",      "label"),
]


def run_one(scene: str, fixture_name: str, classify_module: str, truth_key: str) -> dict:
    """跑单个 scenario 的 bench(unified head + per-scenario candidates)。"""
    print(f"\n=== {scene} ({fixture_name}) ===", file=sys.stderr)

    # 加载 fixture
    cases = load_fixture(fixture_name)
    n = len(cases)

    # 加载 classify_*.py 取 _build_text
    try:
        mod = __import__(classify_module)
        build_text = getattr(mod, "_build_text", None)
        if build_text is None:
            return {"scene": scene, "error": f"{classify_module} 缺 _build_text"}
    except ImportError as e:
        return {"scene": scene, "error": f"无法 import {classify_module}: {e}"}

    # 加载 unified head
    try:
        from agentjev_runtime import infer_scenario
    except Exception as e:
        return {"scene": scene, "error": f"加载 unified head 失败: {e}"}

    correct = 0
    per_class_correct = {}
    per_class_total = {}
    confusion = {}
    latencies = []
    parse_fails = 0
    t_total = time.time()

    for i, tc in enumerate(cases, 1):
        truth = tc.get(truth_key) or tc.get("risk") or tc.get("intent") \
                or tc.get("task_type") or tc.get("label")
        if not truth:
            continue
        per_class_total[truth] = per_class_total.get(truth, 0) + 1

        try:
            sample = tc.get("sample") or tc.get("text") or tc
            state = build_text(sample)
            out = infer_scenario(scene, state)
            pred = out["top"]
            latencies.append(out["latency_ms"])
        except Exception as e:
            parse_fails += 1
            pred = "<error>"
            latencies.append(0.0)

        if pred == truth:
            correct += 1
            per_class_correct[truth] = per_class_correct.get(truth, 0) + 1
        else:
            confusion[(truth, pred)] = confusion.get((truth, pred), 0) + 1

    total_s = time.time() - t_total
    latencies.sort()
    return {
        "scene": scene,
        "fixture": fixture_name,
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0,
        "parse_fail_rate": round(parse_fails / n, 4) if n else 0,
        "per_class": {k: {"correct": per_class_correct.get(k, 0), "total": v,
                          "accuracy": round(per_class_correct.get(k, 0) / v, 4) if v else 0}
                       for k, v in per_class_total.items()},
        "confusion": {f"{t}->{p}": c for (t, p), c in confusion.items()},
        "latency_ms": {"p50": latencies[len(latencies)//2] if latencies else 0,
                       "min": latencies[0] if latencies else 0,
                       "max": latencies[-1] if latencies else 0,
                       "total_s": round(total_s, 1)},
    }


def main() -> int:
    reports = []
    for scene, fixture_name, classify_module, truth_key in SCENARIOS:
        try:
            r = run_one(scene, fixture_name, classify_module, truth_key)
            reports.append(r)
            if "error" in r:
                print(f"  ❌ {scene}: {r['error']}", file=sys.stderr)
            else:
                print(f"  ✅ {scene}: ACC={r['accuracy']} ({r['correct']}/{r['n']}) "
                      f"p50={r['latency_ms']['p50']:.0f}ms "
                      f"parse_fail={r['parse_fail_rate']*100:.0f}%",
                      file=sys.stderr)
        except Exception as e:
            reports.append({"scene": scene, "error": str(e)})
            print(f"  ❌ {scene}: 异常 {e}", file=sys.stderr)

    valid = [r for r in reports if "error" not in r]
    summary = {
        "model": "unified AgentJev classification-head (跨 8 scenario, 单 model)",
        "n_scenes": len(reports),
        "n_success": len(valid),
        "avg_accuracy": round(
            sum(r["accuracy"] for r in valid) / max(1, len(valid)), 4),
        "scenes": reports,
    }
    print(f"\n=== 汇总 ===", file=sys.stderr)
    print(f"  成功 {summary['n_success']}/{summary['n_scenes']} scenario", file=sys.stderr)
    print(f"  平均 ACC = {summary['avg_accuracy']*100:.1f}%", file=sys.stderr)

    out_path = _HERE / "reports" / "bench_all_agentjev_2026-09-24.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"  报告: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())