#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_8heads_vs_unified.py — M3.66 L3-A 8 head vs unified head 对比(2026-09-24)

- 8 head:每个 scenario 单独 head(已有 *_ch_v3/adapter/*_ch_head.pt)
- unified head:1 个 head 跨 8 scenario(unified_ch_v3/adapter/unified_ch_head.pt)
- 跑同一组 test fixtures,对比 ACC/latency/parse_fail

fixture schema(M3.60):
  - disk_cleanup/tempfile/email/log_independent → truth='risk_label', input='text'
  - perf_independent → truth='expect', input='text'
  - intents/task/safety_jev_compat → truth='expect', input='text'

输出:reports/bench_8heads_vs_unified_2026-09-24.json
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from test_fixtures import load_fixture

# (scene, fixture_name, truth_field)
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

BACKBONE = "D:/prisir-train-assets/models/Qwen3Guard-Gen-0.6B"
HEADS_DIR = "D:/prisir-train-assets/trained"


def load_scene_head(scene: str):
    """加载 8 个独立 head 之一。"""
    from agentjev_runtime import AgentJevRuntime
    head_path = f"{HEADS_DIR}/{scene}_ch_v3/adapter/{scene}_ch_head.pt"
    if not Path(head_path).exists():
        return None
    return AgentJevRuntime.load(BACKBONE, head_path, device="cpu")


def bench_one(model_kind: str, scene: str, fixture_name: str, truth_key: str, rt) -> dict:
    """跑单个 scenario bench。"""
    cases = load_fixture(fixture_name)
    n = len(cases)
    correct = 0
    parse_fails = 0
    latencies = []
    per_class_correct = {}
    per_class_total = {}

    for tc in cases:
        truth = tc.get(truth_key)
        if not truth:
            continue
        per_class_total[truth] = per_class_total.get(truth, 0) + 1
        try:
            # fixture text 已是 state text,直接用
            state = tc.get("text", "")
            if model_kind == "unified":
                from agentjev_runtime import infer_scenario
                out = infer_scenario(scene, state)
            else:
                from agentjev_runtime import SCENARIO_QUESTIONS, SCENARIO_CANDIDATES
                out = rt.infer_one(state, SCENARIO_QUESTIONS[scene],
                                   SCENARIO_CANDIDATES[scene])
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

    latencies.sort()
    return {
        "scene": scene,
        "model": model_kind,
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0,
        "parse_fail_rate": round(parse_fails / n, 4) if n else 0,
        "per_class": {k: {"correct": per_class_correct.get(k, 0), "total": v,
                          "accuracy": round(per_class_correct.get(k, 0) / v, 4) if v else 0}
                       for k, v in per_class_total.items()},
        "latency_ms": {"p50": latencies[len(latencies)//2] if latencies else 0,
                       "min": latencies[0] if latencies else 0,
                       "max": latencies[-1] if latencies else 0},
    }


def main() -> int:
    results = []

    # 先跑 8 head baseline
    print("=== 8 head (per-scenario) bench ===\n", file=sys.stderr)
    rts = {}
    for scene, fixture_name, truth_key in SCENARIOS:
        rt = load_scene_head(scene)
        rts[scene] = rt
        if rt is None:
            print(f"  ❌ {scene}: head file missing", file=sys.stderr)
            results.append({"scene": scene, "model": "8head", "error": "head missing"})
            continue
        r = bench_one("8head", scene, fixture_name, truth_key, rt)
        results.append(r)
        if "error" not in r:
            print(f"  ✅ {scene}: ACC={r['accuracy']} ({r['correct']}/{r['n']}) "
                  f"p50={r['latency_ms']['p50']:.0f}ms", file=sys.stderr)
        else:
            print(f"  ❌ {scene}: {r['error']}", file=sys.stderr)

    # unified head(如果已下载)
    unified_path = f"{HEADS_DIR}/unified_ch_v3/adapter/unified_ch_head.pt"
    if Path(unified_path).exists():
        print("\n=== unified head (1 model 跨 8 scenario) bench ===\n", file=sys.stderr)
        for scene, fixture_name, truth_key in SCENARIOS:
            r = bench_one("unified", scene, fixture_name, truth_key, None)
            results.append(r)
            if "error" not in r:
                print(f"  ✅ {scene}: ACC={r['accuracy']} ({r['correct']}/{r['n']}) "
                      f"p50={r['latency_ms']['p50']:.0f}ms", file=sys.stderr)
            else:
                print(f"  ❌ {scene}: {r['error']}", file=sys.stderr)
    else:
        print(f"\n  ⚠️ unified head 未下载: {unified_path}", file=sys.stderr)
        print("  等 unified 训练完成后跑 _download_unified.sh + 重新 bench", file=sys.stderr)

    out_path = _HERE / "reports" / "bench_8heads_vs_unified_2026-09-24.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n报告: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())