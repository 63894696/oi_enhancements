#!/usr/bin/env python3
"""
bench_laya.py — 对比测试:laya vs 我们 LoRA on 5 个 scenario

目的:复用 test_fixtures/ 的独立样本,跑 laya 看是否适合做 fast-path 或替换 Laya 优势场景

设计:
  - 加载 laya Router (multilingual, 适配中文 fixture)
  - 5 个 scenario 用最合适的 preset:
    log/email/disk_cleanup/perf → triage_questions()
    tempfile → guard_questions() (敏感数据 / 文件类型)
  - 映射 laya answer → 5 类 risk (safe/low/medium/high/critical)
  - 对比 fixture 'expect' 字段,算 ACC / parse_fail / 平均延迟
  - 输出 JSON 报告 + 终端对比表

用法:
  python bench_laya.py              # 全部 5 scenario
  python bench_laya.py --scenario log
  python bench_laya.py --limit 10   # 每个 scenario 只跑前 N 条(快速测)

依赖:
  pip install laya   # 422M 模型自动下载
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "test_fixtures"

# laya preset 用的 state key
SCHEMA_KEY = {
    "triage": "message",
    "email": "body",
    "guard": "prompt",
    "router": "request",
}

# laya preset 加载器
PRESETS = {
    "triage": None,  # 延迟导入
    "email": None,
    "guard": None,
    "router": None,
}

# 各 scenario 用的 preset
SCENARIO_PRESET = {
    "log": "triage",
    "email": "email",
    "disk_cleanup": "triage",
    "perf": "triage",
    "tempfile": "guard",
}

# laya answer → 5 类 risk 的映射函数
def map_to_risk(scenario: str, answers: dict[str, Any]) -> str:
    """把 laya 输出映射成 5 类 risk 等级。

    等级: safe < low < medium < high < critical
    """
    if scenario == "log":
        # 用 frustration(0-3) + is_urgent(0-1) 联合判定
        urg = answers.get("is_urgent", {}).get("noul", 0) or 0
        fru = answers.get("frustration", {}).get("score", 0) or 0
        if urg >= 0.7 and fru >= 2.0:
            return "critical"
        if urg >= 0.5 or fru >= 2.5:
            return "high"
        if fru >= 1.5:
            return "medium"
        if fru >= 0.5 or urg >= 0.2:
            return "low"
        return "safe"
    if scenario == "email":
        # urgency(0-2) + is_phishing + sensitive_data
        urg = answers.get("urgency", {}).get("score", 0) or 0
        phish = answers.get("is_phishing", {}).get("noul", 0) or 0
        if phish >= 0.7 or urg >= 1.8:
            return "critical"
        if urg >= 1.5 or phish >= 0.4:
            return "high"
        if urg >= 1.0:
            return "medium"
        if urg >= 0.4:
            return "low"
        return "safe"
    if scenario == "disk_cleanup":
        # 路径描述:frustration 越高越危险
        fru = answers.get("frustration", {}).get("score", 0) or 0
        urg = answers.get("is_urgent", {}).get("noul", 0) or 0
        if fru >= 2.5 or urg >= 0.8:
            return "critical"
        if fru >= 2.0 or urg >= 0.5:
            return "high"
        if fru >= 1.5:
            return "medium"
        if fru >= 0.5:
            return "low"
        return "safe"
    if scenario == "perf":
        # 系统告警/性能数据 → frustration
        fru = answers.get("frustration", {}).get("score", 0) or 0
        urg = answers.get("is_urgent", {}).get("noul", 0) or 0
        if fru >= 2.5 and urg >= 0.5:
            return "critical"
        if fru >= 2.0 or urg >= 0.7:
            return "high"
        if fru >= 1.5:
            return "medium"
        if fru >= 0.5:
            return "low"
        return "safe"
    if scenario == "tempfile":
        # 临时文件 / 敏感文件检测 → guard 维度
        sens = answers.get("sensitive_data", {}).get("noul", 0) or 0
        harm = answers.get("harm_severity", {}).get("score", 0) or 0
        if sens >= 0.7 or harm >= 2.5:
            return "critical"
        if sens >= 0.5 or harm >= 2.0:
            return "high"
        if sens >= 0.2 or harm >= 1.0:
            return "medium"
        if sens >= 0.05 or harm >= 0.4:
            return "low"
        return "safe"
    return "safe"


def load_fixture(scenario: str) -> list[dict]:
    """加载独立测试集(60 条/类,5 类共 60 条)。"""
    fp = FIXTURES_DIR / f"{scenario}_independent.json"
    if not fp.exists():
        raise FileNotFoundError(f"fixture not found: {fp}")
    return json.loads(fp.read_text(encoding="utf-8"))


def run_one(router, scenario: str, items: list[dict], limit: int) -> dict:
    """跑一个 scenario,返回指标。"""
    from laya import triage_questions, email_questions, guard_questions, router_questions

    preset_name = SCENARIO_PRESET[scenario]
    qs_map = {
        "triage": triage_questions(),
        "email": email_questions(),
        "guard": guard_questions(),
        "router": router_questions(),
    }
    qs = qs_map[preset_name]
    key = SCHEMA_KEY[preset_name]

    items = items[:limit] if limit else items
    correct = 0
    total = len(items)
    parse_fail = 0
    latencies: list[float] = []
    per_label = {lbl: {"total": 0, "correct": 0} for lbl in ["safe", "low", "medium", "high", "critical"]}
    errors: list[dict] = []

    for item in items:
        text = item.get("text", "")
        expect = item.get("expect") or item.get("risk_label") or "safe"
        per_label[expect]["total"] += 1

        state = {key: text}
        t0 = time.time()
        try:
            res = router.predict(state, qs)
            elapsed = time.time() - t0
            latencies.append(elapsed)
            answers = res.get("answers", {})
            if not answers:
                parse_fail += 1
                errors.append({"id": item.get("id"), "expect": expect, "got": None, "reason": "empty answers"})
                continue
            got = map_to_risk(scenario, answers)
            if got == expect:
                correct += 1
                per_label[expect]["correct"] += 1
            else:
                errors.append({"id": item.get("id"), "expect": expect, "got": got})
        except Exception as e:
            parse_fail += 1
            latencies.append(time.time() - t0)
            errors.append({"id": item.get("id"), "expect": expect, "got": None, "reason": repr(e)[:200]})

    acc = correct / total if total else 0
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p50 = sorted(latencies)[len(latencies) // 2] if latencies else 0

    per_label_acc = {
        lbl: (d["correct"] / d["total"] if d["total"] else 0)
        for lbl, d in per_label.items()
    }

    return {
        "scenario": scenario,
        "preset": preset_name,
        "total": total,
        "correct": correct,
        "parse_fail": parse_fail,
        "overall_acc": round(acc, 4),
        "per_label_acc": {k: round(v, 4) for k, v in per_label_acc.items()},
        "per_label_total": {k: d["total"] for k, d in per_label.items()},
        "avg_latency_ms": round(avg_latency * 1000, 1),
        "p50_latency_ms": round(p50 * 1000, 1),
        "errors": errors[:10],
    }


def main():
    parser = argparse.ArgumentParser(description="laya benchmark on 5 scenarios")
    parser.add_argument("--scenario", choices=list(SCENARIO_PRESET.keys()) + ["all"], default="all")
    parser.add_argument("--limit", type=int, default=0, help="每 scenario 限 N 条(0=全跑)")
    parser.add_argument("--output", default=None, help="输出 JSON 报告路径")
    parser.add_argument("--english", action="store_true", help="用 english 模型(默认 multilingual)")
    args = parser.parse_args()

    from laya import Router
    print(f"loading laya Router (default={'english' if args.english else 'multilingual'})...", flush=True)
    router = Router(default="english" if args.english else "multilingual", preload=True)
    print("router ready\n", flush=True)

    scenarios = list(SCENARIO_PRESET.keys()) if args.scenario == "all" else [args.scenario]

    results = []
    for scenario in scenarios:
        items = load_fixture(scenario)
        print(f"=== {scenario} ({len(items)} items, preset={SCENARIO_PRESET[scenario]}) ===", flush=True)
        t0 = time.time()
        result = run_one(router, scenario, items, args.limit)
        result["elapsed_total_s"] = round(time.time() - t0, 1)
        results.append(result)
        print(
            f"  ACC={result['overall_acc']:.2%}  "
            f"parse_fail={result['parse_fail']}/{result['total']}  "
            f"avg_lat={result['avg_latency_ms']:.0f}ms  "
            f"p50={result['p50_latency_ms']:.0f}ms"
        )
        # 按类展示
        for lbl in ["safe", "low", "medium", "high", "critical"]:
            tot = result["per_label_total"][lbl]
            acc = result["per_label_acc"][lbl]
            print(f"    {lbl:8s}  {acc:.2%}  ({result['per_label_total'][lbl] if False else tot} total)")
        print()

    # 总结表
    print("=" * 80)
    print(f"{'scenario':<16} {'preset':<10} {'ACC':>8} {'parse_fail':>12} {'avg_lat':>10} {'p50':>10}")
    print("-" * 80)
    for r in results:
        print(
            f"{r['scenario']:<16} {r['preset']:<10} {r['overall_acc']:>7.2%} "
            f"{r['parse_fail']:>5d}/{r['total']:<5} {r['avg_latency_ms']:>8.0f}ms {r['p50_latency_ms']:>8.0f}ms"
        )
    print("=" * 80)

    if args.output:
        Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n→ report saved to {args.output}")


if __name__ == "__main__":
    main()