#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_perf_agentjev_official.py — M3.66 L7-A 官方 AgentJev server zero-shot 对比(2026-09-23)

aliyun 官方 server:127.0.0.1:18765
- 复用 bench_perf_local.py 的 TEST_CASES(50 条,5 类各 10)
- sample dict → _build_text → state text → HTTP POST /api/evaluate
- 提取 value (top choice) 与 truth 比对

对比三组:
  - perf_conf_v3 (LoRA): 78% ACC / 7.5s / 50 条
  - perf_ch_v1 (我们训): 20% ACC / 2.0s / 50 条
  - official AgentJev: ? ACC / ~2.3s/条 / 50 条(本脚本输出)
"""
from __future__ import annotations
import json, sys, time, urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from bench_perf_local import TEST_CASES  # 复用 50 条
from classify_perf import _build_text

SERVER = "http://127.0.0.1:18765"
CANDIDATES = ["safe", "low", "medium", "high", "critical"]
QUESTION = "本地性能采样风险等级(从 safe 到 critical 5 类,选最合适的)"


def call_jev(state_text: str, candidates: list[str], retries: int = 3) -> dict:
    """单条 HTTP POST → 解析 distribution → 返回 {top, top_prob, dist, wall_ms}"""
    payload = {
        "requests": [{
            "id": "single",
            "state": state_text,
            "questions": [{
                "id": "q1",
                "type": "choice",
                "question": QUESTION,
                "options": candidates,  # 数组格式
            }],
        }],
    }
    body_text = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                f"{SERVER}/api/evaluate",
                data=body_text,
                headers={"Content-Type": "application/json"},
            )
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            wall_ms = (time.time() - t0) * 1000.0
            answer = body["results"][0]["answers"][0]
            dist = answer["distribution"]
            keys = list(dist.keys())
            probs = [dist[k] for k in keys]
            value_idx = int(answer["value"])
            return {
                "top": candidates[value_idx],
                "top_prob": answer["top_probability"],
                "dist": dict(zip(candidates, probs)),
                "wall_ms": round(wall_ms, 1),
                "server_wall_ms": body["usage"]["wall_ms"],
                "candidates": candidates,
                "ok": True,
            }
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt)  # 退避
    return {
        "top": "medium", "top_prob": 0.0, "dist": {},
        "wall_ms": 0.0, "server_wall_ms": 0.0,
        "candidates": candidates, "ok": False, "error": str(last_err),
    }


def main() -> int:
    n = len(TEST_CASES)
    correct = 0
    ok_count = 0
    per_class_correct = {}
    per_class_total = {}
    per_class_ok = {}
    confusion = {}
    records = []
    t_total = time.time()
    for i, tc in enumerate(TEST_CASES, 1):
        truth = tc["risk"]
        per_class_total[truth] = per_class_total.get(truth, 0) + 1
        try:
            state = _build_text(tc["sample"])
            out = call_jev(state, CANDIDATES)
            pred = out["top"]
            if out.get("ok"):
                ok_count += 1
        except Exception as e:
            print(f"[official_jev] case {i} error: {e}", file=sys.stderr)
            pred = "medium"
            out = {"top_prob": 0.0, "dist": {}, "wall_ms": 0.0, "server_wall_ms": 0.0}
        if pred == truth:
            correct += 1
            per_class_correct[truth] = per_class_correct.get(truth, 0) + 1
        if out.get("ok"):
            per_class_ok[truth] = per_class_ok.get(truth, 0) + 1
        else:
            confusion[(truth, pred)] = confusion.get((truth, pred), 0) + 1
        records.append({
            "i": i, "truth": truth, "pred": pred, "ok": out.get("ok", False),
            "top_prob": out["top_prob"], "dist": out["dist"],
            "wall_ms": out["wall_ms"], "server_wall_ms": out["server_wall_ms"],
            "error": out.get("error"),
        })
        if i % 5 == 0:
            print(f"  [{i}/{n}] acc={correct/i:.3f} ok={ok_count}/{i}", file=sys.stderr)

    total_s = time.time() - t_total
    report = {
        "model": "official AgentJev zero-shot (HTTP 127.0.0.1:18765)",
        "n": n, "correct": correct, "ok_count": ok_count,
        "accuracy_all": round(correct / n, 4),
        "accuracy_real": round(correct / ok_count, 4) if ok_count else 0.0,
        "per_class": {k: {"correct": per_class_correct.get(k, 0), "total": v,
                          "ok": per_class_ok.get(k, 0),
                          "accuracy": round(per_class_correct.get(k, 0) / v, 4) if v else 0}
                       for k, v in per_class_total.items()},
        "confusion": {f"{t}->{p}": c for (t, p), c in confusion.items()},
        "total_s": round(total_s, 1),
        "records": records,
    }
    out_path = _HERE / "reports" / "bench_perf_agentjev_official.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[official_jev] ok={ok_count}/{n}", file=sys.stderr)
    print(f"[official_jev] accuracy_all={report['accuracy_all']:.4f} "
          f"accuracy_real={report['accuracy_real']:.4f} "
          f"total={total_s:.1f}s", file=sys.stderr)
    for cls, m in report["per_class"].items():
        print(f"  {cls:8s}: {m['correct']}/{m['total']} ({m['accuracy']*100:.0f}%) "
              f"[real={m['ok']}/{m['total']}]", file=sys.stderr)
    print(f"[official_jev] written: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())