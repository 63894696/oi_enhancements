# -*- coding: utf-8 -*-
"""bench_all.py — M3.60 综合评测入口(2026-09-23)

一键跑所有 15 adapter + Jev 真测对比,出综合报告。

8 个 scenario:
- intents / task / safety — Jev 适用,真测对比
- perf / disk_cleanup / tempfile / email / log — 结构化,独立 test set

用法:
    python bench_all.py                              # 全场景本地
    python bench_all.py --scenarios intents,task     # 指定
    python bench_all.py --no-jev                     # 跳过 Jev(省钱)
    python bench_all.py --output reports/bench_2026-09-23.json

设计原则:
- 共享 test_fixtures/{scenario}.json(已在 L4 完成 bench_*.py 改造)
- Jev 真测仅适用 intents/task/safety;其他场景无 Jev 基线,跳过
- 默认 --no-jev(本机跑零成本);用户主动开启跑真 Jev(花钱)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from test_fixtures import load_fixture, fixture_path, FIXTURES  # noqa: E402

# scenario 配置:(本地 adapter 名, Jev 适用?fixture 名, 评测入口模块)
SCENARIOS: dict = {
    "intents": {
        "fixture": "intents_jev_compat",
        "jev_compatible": True,
        "local_module": "classify_intents",
        "local_fn": "classify_intents",
        "extract_text": lambda c: c["text"],
        "call_pattern": "text_only",  # classify_intents(text), not (adapter, text)
    },
    "task": {
        "fixture": "task_jev_compat",
        "jev_compatible": True,
        "local_module": "classify_task_local",
        "local_fn": "classify_task_local",
        "extract_text": lambda c: c["text"],
        "call_pattern": "text_only",
    },
    "safety": {
        "fixture": "safety_jev_compat",
        "jev_compatible": True,
        "local_module": None,
        "local_fn": None,
        "extract_text": lambda c: c["text"],
    },
    "perf": {
        "fixture": "perf_independent",
        "jev_compatible": False,
        "local_module": "classify_perf",
        "local_fn": "classify_perf",
        # M3.61 修:fixture 有 text (JSON 字符串,过长) 和 sample (dict,推荐) 两个字段
        # 用 sample(已经解析好的 dict)避免重复 parse + 超长文本
        "extract_text": lambda c: c.get("sample") or c,
        "call_pattern": "adapter_first",  # classify_perf(adapter, sample)
    },
    "disk_cleanup": {
        "fixture": "disk_cleanup_independent",
        "jev_compatible": False,
        "local_module": "classify_disk_cleanup",
        "local_fn": "classify_one",
        "extract_text": lambda c: c["text"],
        "call_pattern": "adapter_first",
    },
    "tempfile": {
        "fixture": "tempfile_independent",
        "jev_compatible": False,
        "local_module": "classify_email",  # M3.60 暂未实现独立 tempfile classify
        "local_fn": "classify_email",
        "extract_text": lambda c: c["text"],
        "call_pattern": "adapter_first",
    },
    "email": {
        "fixture": "email_independent",
        "jev_compatible": False,
        "local_module": "classify_email",
        "local_fn": "classify_email",
        "extract_text": lambda c: c["text"],  # 留 raw text,_bench_local 用 _extract_email_args 解析
        "call_pattern": "adapter_first",
        "extract_kwargs": True,  # M3.61:email 接口特殊,要从 text 拆 sender/subject/body
    },
    "log": {
        "fixture": "log_independent",
        "jev_compatible": False,
        "local_module": "classify_log",
        "local_fn": "classify_log",
        "extract_text": lambda c: c["text"],
        "call_pattern": "adapter_first",
        "extract_kwargs": True,
        "extract_kwargs_fn": "_extract_log_kwargs",  # M3.61:classify_log(adapter, level, content)
    },
    "tempfile": {
        "fixture": "tempfile_independent",
        "jev_compatible": False,
        "local_module": "classify_tempfile",  # M3.61:写独立 classify_tempfile.py
        "local_fn": "classify_tempfile",
        "extract_text": lambda c: c["text"],
        "call_pattern": "adapter_first",
    },
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _extract_email_kwargs(text: str) -> dict:
    """M3.61:从 fixture text 拆 {sender, subject, body} 给 classify_email。

    文本格式(Gen 时统一):
        发件人: <sender>\\n主题: <subject>\\n正文摘要/内容: <body>\\n距今: ...
    """
    import re
    sender = re.search(r"发件人:\s*([^\n]+)", text)
    subject = re.search(r"主题:\s*([^\n]+)", text)
    body = re.search(
        r"(?:正文摘要|内容|正文):\s*([^\n]+(?:\n(?!\w+:)[^\n]*)*)", text)
    return {
        "sender": sender.group(1).strip() if sender else "",
        "subject": subject.group(1).strip() if subject else "",
        "body": body.group(1).strip() if body else text,
    }


def _extract_log_kwargs(text: str) -> dict:
    """M3.61:从 fixture text 拆 {source, level, content} 给 classify_log。

    文本格式:
        日志来源: <source>\\n时间戳: <ts>\\n级别: <level>\\n内容: <content>\\n问: ...
    """
    import re
    source = re.search(r"日志来源:\s*([^\n]+)", text)
    level = re.search(r"级别:\s*([^\n]+)", text)
    content = re.search(
        r"内容:\s*([^\n]+(?:\n(?!\w+:)[^\n]*)*)", text)
    return {
        "source": source.group(1).strip() if source else "",
        "level": level.group(1).strip() if level else "",
        "content": content.group(1).strip() if content else text,
    }


def _aggregate(results: list[dict]) -> dict:
    """聚合:总精度/按类精度/parse_fail/latency 分位。"""
    n = len(results)
    if n == 0:
        return {"n": 0, "accuracy": 0.0}
    matches = sum(1 for r in results if r.get("match"))
    parse_fails = sum(1 for r in results if r.get("parse_fail"))
    latencies = sorted(r["elapsed_ms"] for r in results if "elapsed_ms" in r)
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) >= 20 else (latencies[-1] if latencies else 0)
    return {
        "n": n,
        "accuracy": round(matches / n, 4),
        "parse_fail_rate": round(parse_fails / n, 4),
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
    }


def _bench_local(scenario: str, cfg: dict) -> dict:
    """跑本地 adapter 评测。

    返回:{"results": [...], "summary": {...}, "fixture_path": ..., "fixture_sha": ...}
    """
    s = SCENARIOS[scenario]
    cases = load_fixture(s["fixture"])
    print(f"\n=== [{scenario}] 加载 fixture {s['fixture']} → {len(cases)} 条 ===")
    if not s["local_module"]:
        return {
            "skipped": True,
            "reason": "无本地 adapter 模块(待 M3.60+ 训)",
            "fixture": s["fixture"],
            "n_cases": len(cases),
        }

    try:
        mod = __import__(s["local_module"])
        fn = getattr(mod, s["local_fn"])
    except Exception as e:
        return {
            "skipped": True,
            "reason": f"导入 {s['local_module']}.{s['local_fn']} 失败:{e}",
            "fixture": s["fixture"],
            "n_cases": len(cases),
        }

    from adapter_registry import get_adapter
    adapter = None
    try:
        # M3.61:perf scenario 默认用 perf_conf 而不是 perf_conf_v2(后者 tokenizer.json 触发
        # transformers json.load 内部 JSONDecodeError,但 json.loads 直接读 OK — 怀疑是
        # peft + AutoTokenizer.from_pretrained 的 race)。perf_conf 走 bench_perf_local.py 路径已验证 OK
        adapter_name = scenario if scenario != "perf" else "perf_conf"
        adapter = get_adapter(adapter_name)
    except Exception as e:
        return {
            "skipped": True,
            "reason": f"get_adapter({adapter_name!r}) 失败:{e}",
            "fixture": s["fixture"],
            "n_cases": len(cases),
        }

    results = []
    t_start = time.time()
    call_pattern = s.get("call_pattern", "adapter_first")
    extract_kwargs = s.get("extract_kwargs", False)
    for i, case in enumerate(cases, 1):
        t0 = time.time()
        try:
            inp = s["extract_text"](case)
            if extract_kwargs:
                # M3.61:email/log fixture 是结构化文本,要拆 kwargs 给对应 classify_*
                kw_fn_name = s.get("extract_kwargs_fn", "_extract_email_kwargs")
                kw_fn = globals().get(kw_fn_name, _extract_email_kwargs)
                kwargs = kw_fn(inp)
                out = fn(adapter, **kwargs)
            elif call_pattern == "text_only":
                out = fn(inp)
            else:
                out = fn(adapter, inp)
            elapsed_ms = int((time.time() - t0) * 1000)
            # M3.61 扩:不同 classify 返的 key 不同 — risk_label(默认) / risk(classify_disk_cleanup) /
            # intent(classify_intents/task) / label / choice
            actual = (out.get("risk_label") or out.get("risk") or out.get("intent")
                      or out.get("task_type") or out.get("label")
                      or out.get("choice") or "unknown")
            match = actual == case.get("expect")
            parse_fail = actual == "unknown"
            results.append({
                "i": i,
                "id": case["id"],
                "expect": case.get("expect"),
                "actual": actual,
                "match": match,
                "parse_fail": parse_fail,
                "elapsed_ms": elapsed_ms,
            })
            # M3.61:逐条 print + flush,避免大 stdout 缓冲看不到进度
            mark = "✓" if match else ("?" if parse_fail else "✗")
            print(f"  {mark} [{i}/{len(cases)}] expect={case.get('expect')} "
                  f"actual={actual} ({elapsed_ms}ms)", flush=True)
        except Exception as e:  # noqa: BLE001
            elapsed_ms = int((time.time() - t0) * 1000)
            results.append({
                "i": i,
                "id": case["id"],
                "expect": case.get("expect"),
                "actual": "error",
                "match": False,
                "parse_fail": True,
                "elapsed_ms": elapsed_ms,
                "error": f"{type(e).__name__}: {e}",
            })
            print(f"  ! [{i}/{len(cases)}] ERROR {type(e).__name__}: {e}", flush=True)

    elapsed_total = time.time() - t_start
    return {
        "fixture": s["fixture"],
        "fixture_path": str(fixture_path(s["fixture"])),
        "n_cases": len(cases),
        "results": results,
        "summary": _aggregate(results),
        "elapsed_total_s": round(elapsed_total, 2),
    }


async def _bench_jev(scenario: str, cfg: dict) -> dict:
    """跑 Jev 真测(仅 intents / task / safety)。"""
    s = SCENARIOS[scenario]
    if not s["jev_compatible"]:
        return {"skipped": True, "reason": "结构化场景,Jev 不适用"}
    cases = load_fixture(s["fixture"])
    if scenario == "intents":
        from companion_jev import ask_intent
        results = []
        t_start = time.time()
        for i, case in enumerate(cases, 1):
            t0 = time.time()
            try:
                intent = await ask_intent(case["text"], history_len=0,
                                          user_tier="free", timeout_s=2.0)
                elapsed_ms = int((time.time() - t0) * 1000)
                actual = (intent or {}).get("choice", "unknown")
                confidence = float((intent or {}).get("confidence", 0.0))
                match = actual == case["expect"]
                results.append({
                    "i": i, "id": case["id"],
                    "expect": case["expect"], "actual": actual,
                    "match": match, "confidence": confidence,
                    "elapsed_ms": elapsed_ms,
                })
            except Exception as e:  # noqa: BLE001
                elapsed_ms = int((time.time() - t0) * 1000)
                results.append({
                    "i": i, "id": case["id"],
                    "expect": case["expect"], "actual": "error",
                    "match": False, "elapsed_ms": elapsed_ms,
                    "error": f"{type(e).__name__}: {e}",
                })
        return {
            "fixture": s["fixture"],
            "n_cases": len(cases),
            "results": results,
            "summary": _aggregate(results),
            "elapsed_total_s": round(time.time() - t_start, 2),
        }
    return {"skipped": True, "reason": f"{scenario} 的 Jev 评测待接入"}


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.60 综合评测入口")
    ap.add_argument("--scenarios", default=None,
                    help="逗号分隔的 scenario 名;默认全跑")
    ap.add_argument("--no-jev", action="store_true",
                    help="跳过 Jev 真测(默认开启,省钱)")
    ap.add_argument("--output", default=None,
                    help="报告输出 JSON 文件路径")
    ap.add_argument("--scenarios-only", action="store_true",
                    help="只跑 scenario 配置合理性验证,不实际加载 adapter")
    args = ap.parse_args()

    scenarios = args.scenarios.split(",") if args.scenarios else list(SCENARIOS.keys())
    invalid = [s for s in scenarios if s not in SCENARIOS]
    if invalid:
        print(f"❌ 未知 scenario:{invalid}\n   可用:{list(SCENARIOS.keys())}")
        return 2

    print(f"=== M3.60 bench_all 综合评测 ===")
    print(f"时间:{_now()}")
    print(f"scenarios:{scenarios}")
    print(f"jev={'OFF' if args.no_jev else 'ON'}")

    report: dict = {
        "ts": _now(),
        "scenarios_listed": scenarios,
        "scenarios": {},
    }

    for scenario in scenarios:
        cfg = SCENARIOS[scenario]
        if args.scenarios_only:
            # 只验证 scenario 配置合理性,跳过实际加载
            cases = load_fixture(cfg["fixture"])
            report["scenarios"][scenario] = {
                "fixture": cfg["fixture"],
                "fixture_path": str(fixture_path(cfg["fixture"])),
                "n_cases": len(cases),
                "classes_in_fixture": sorted(set(c.get("expect") for c in cases)),
                "dry_run": True,
            }
            continue
        # 1) 本地 adapter
        local_result = _bench_local(scenario, cfg)
        report["scenarios"][scenario] = {
            "fixture": cfg["fixture"],
            "fixture_path": str(fixture_path(cfg["fixture"])),
            "local": local_result,
        }
        # 2) Jev 真测(若启用)
        if not args.no_jev and cfg["jev_compatible"]:
            try:
                jev_result = asyncio.run(_bench_jev(scenario, cfg))
                report["scenarios"][scenario]["jev"] = jev_result
                # delta
                l = local_result.get("summary", {}).get("accuracy", 0)
                j = jev_result.get("summary", {}).get("accuracy", 0)
                report["scenarios"][scenario]["delta_local_vs_jev"] = round(l - j, 4)
            except Exception as e:  # noqa: BLE001
                report["scenarios"][scenario]["jev_error"] = f"{type(e).__name__}: {e}"

    # 写报告
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n✅ 报告已写:{args.output}")
    else:
        # 简版打印
        print("\n--- 简版汇总 ---")
        for s, d in report["scenarios"].items():
            local = d.get("local", {})
            if local.get("skipped"):
                print(f"  {s}: SKIPPED ({local.get('reason', '?')})")
                continue
            summary = local.get("summary", {})
            line = f"  {s}: acc={summary.get('accuracy', 0):.2f} p50={summary.get('latency_p50_ms', 0)}ms"
            if "jev" in d:
                jev = d["jev"].get("summary", {})
                line += f" | jev_acc={jev.get('accuracy', 0):.2f}"
                if "delta_local_vs_jev" in d:
                    line += f" | Δ={d['delta_local_vs_jev']:+.2f}"
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
