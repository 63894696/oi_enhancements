#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_perf_agentjev.py — M3.66 L4 AgentJev classification-head 评测(2026-09-23)

复用 bench_perf_local.py 的 TEST_CASES(50 条,5 类各 10):
  - sample dict → _build_text → state text
  - AgentJevRuntime.infer_one(state, question, candidates) → top candidate
  - 统计 ACC/per-class/latency

对比:
  - perf_conf_v3 (LoRA 路径):  ACC 78%, p50 7.5s
  - perf_ch_v1 (AgentJev demo): ACC ?, p50 ?s
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from bench_perf_local import TEST_CASES  # 复用 50 条
from classify_perf import _build_text

CANDIDATES = ["safe", "low", "medium", "high", "critical"]
QUESTION = "本地性能采样风险等级(safe/low/medium/high/critical)"


def main() -> int:
    print("[bench_perf_ch] loading AgentJev runtime...", file=sys.stderr)
    from agentjev_runtime import load_perf_ch_v1
    rt = load_perf_ch_v1(device="cpu")  # 本地 CPU 推理
    print(f"[bench_perf_ch] loaded, device={rt.device}", file=sys.stderr)

    n = len(TEST_CASES)
    correct = 0
    per_class_correct = {}
    per_class_total = {}
    confusion = {}
    latencies = []
    records = []
    t_total = time.time()
    for i, tc in enumerate(TEST_CASES, 1):
        truth = tc["risk"]
        per_class_total[truth] = per_class_total.get(truth, 0) + 1
        try:
            state = _build_text(tc["sample"])
            out = rt.infer_one(state, QUESTION, CANDIDATES)
            pred = out["top"]
            latencies.append(out["latency_ms"])
        except Exception as e:
            print(f"[bench_perf_ch] case {i} error: {e}", file=sys.stderr)
            pred = "medium"
            latencies.append(0.0)
        if pred == truth:
            correct += 1
            per_class_correct[truth] = per_class_correct.get(truth, 0) + 1
        else:
            confusion[(truth, pred)] = confusion.get((truth, pred), 0) + 1
        records.append({"i": i, "truth": truth, "pred": pred,
                        "top_prob": out["top_prob"], "latency_ms": out["latency_ms"]})
        if i % 10 == 0:
            print(f"  [{i}/{n}] acc={correct/i:.3f}", file=sys.stderr)

    total_s = time.time() - t_total
    latencies.sort()
    report = {
        "model": "perf_ch_v1 (AgentJev classification-head)",
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4),
        "per_class": {k: {"correct": per_class_correct.get(k, 0), "total": v,
                          "accuracy": round(per_class_correct.get(k, 0) / v, 4) if v else 0}
                       for k, v in per_class_total.items()},
        "confusion": {f"{t}->{p}": c for (t, p), c in confusion.items()},
        "latency_ms": {"p50": latencies[len(latencies)//2],
                      "min": latencies[0], "max": latencies[-1],
                      "total_s": round(total_s, 1)},
        "records": records,
    }
    out_path = _HERE / "reports" / "bench_perf_agentjev.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[bench_perf_ch] accuracy={report['accuracy']} ({correct}/{n})",
          file=sys.stderr)
    print(f"[bench_perf_ch] p50={report['latency_ms']['p50']:.0f}ms "
          f"total={report['latency_ms']['total_s']:.1f}s", file=sys.stderr)
    for cls, m in report["per_class"].items():
        print(f"  {cls:8s}: {m['correct']}/{m['total']} ({m['accuracy']*100:.0f}%)",
              file=sys.stderr)
    print(f"[bench_perf_ch] written: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())