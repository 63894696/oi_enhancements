"""oiagent_status 工具 — 读 trace 日志,输出 prisiragent-team-lead 工作健康度

复用:
- ~/.claude/oiagent_harness_training/*.jsonl (team_lead_tools.py 写)
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

TRACE_DIR = Path.home() / ".claude" / "oiagent_harness_training"


def _err(stage: str, exc: Exception) -> str:
    return json.dumps(
        {"ok": False, "error": str(exc), "stage": stage, "traceback": str(exc.__traceback__)[:500] if exc.__traceback__ else ""},
        ensure_ascii=False,
    )


def _read_all_traces(days: int = 7) -> list[dict]:
    """读最近 N 天的 trace jsonl"""
    if not TRACE_DIR.exists():
        return []
    out = []
    for f in sorted(TRACE_DIR.glob("*.jsonl")):
        # 文件名格式 YYYY-MM-DD.jsonl,按文件名逆序拿最近 N 天
        try:
            file_date = datetime.strptime(f.stem, "%Y-%m-%d")
        except ValueError:
            continue
        if (datetime.now() - file_date).days > days:
            continue
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _summarize(traces: list[dict]) -> dict:
    """汇总 trace 数据"""
    if not traces:
        return {"total": 0, "note": "no traces in window"}

    by_event: Counter = Counter()
    by_intent: Counter = Counter()
    by_agent: Counter = Counter()
    by_pool: Counter = Counter()
    rule_hit_count = 0
    race_winners: Counter = Counter()
    # 模型调用(token 估算 — 用 race.elapsed_ms 反推)
    model_call_count: Counter = Counter()  # model_id → 调用次数
    model_elapsed_total: Counter = Counter()  # model_id → 总 ms
    by_hour: Counter = Counter()  # hour-of-day → count(时间分布)

    for t in traces:
        by_event[t.get("event", "?")] += 1
        intent = t.get("intent")
        if intent:
            by_intent[intent] += 1
        agent = t.get("agent")
        if agent:
            by_agent[agent] += 1
        pool = t.get("pool")
        if pool:
            by_pool[pool] += 1
        if t.get("rule_hit"):
            rule_hit_count += 1
        winner = (t.get("winner") or {}).get("model")
        if winner:
            race_winners[winner] += 1
            model_call_count[winner] += 1
            elapsed = (t.get("winner") or {}).get("elapsed_ms", 0)
            model_elapsed_total[winner] += elapsed
        # race 事件本身也算一次调用(发起 race)
        if t.get("event") == "race":
            for m in t.get("models", []):
                model_call_count[m] += 1
        # 时间分布
        iso = t.get("iso", "")
        if len(iso) >= 13:
            by_hour[iso[11:13]] += 1

    # token 估算:粗略 — 用 elapsed_ms × 50 tokens/sec(实际差距大,但够看出趋势)
    # 注:这是估算,不是真实计费
    model_token_estimate: dict[str, int] = {}
    for m, ms in model_elapsed_total.items():
        # 模型响应时间越长,token 越多(粗略线性)
        est_tokens = int(ms * 0.05)  # 50 tokens/sec 平均
        model_token_estimate[m] = est_tokens

    total = len(traces)
    by_event_d = dict(by_event.most_common())
    by_intent_d = dict(by_intent.most_common())
    by_agent_d = dict(by_agent.most_common())
    by_pool_d = dict(by_pool.most_common())
    race_winners_d = dict(race_winners.most_common())
    model_calls_d = dict(model_call_count.most_common())
    by_hour_d = dict(sorted(by_hour.items()))

    # 最近 10 次事件(轻量)
    latest = traces[-10:][::-1]
    latest_compact = [
        {
            "ts": t.get("iso"),
            "event": t.get("event"),
            "intent": t.get("intent"),
            "agent": t.get("agent"),
            "pool": t.get("pool"),
            "task_preview": (t.get("task") or "")[:50],
        }
        for t in latest
    ]

    return {
        "total": total,
        "rule_hit_rate": round(rule_hit_count / total, 3) if total else 0,
        "by_event": by_event_d,
        "by_intent": by_intent_d,
        "by_agent": by_agent_d,
        "by_pool": by_pool_d,
        "race_winners": race_winners_d,
        "model_calls": model_calls_d,
        "model_elapsed_ms": dict(model_elapsed_total),
        "model_token_estimate": model_token_estimate,
        "token_estimate_total": sum(model_token_estimate.values()),
        "by_hour": by_hour_d,
        "latest": latest_compact,
    }


def oiagent_status_impl(days: int = 7) -> str:
    """读最近 N 天 trace,输出 prisiragent-team-lead 工作健康度"""
    try:
        days = max(1, min(days, 90))  # 1-90 天,防止爆扫
        traces = _read_all_traces(days=days)
        summary = _summarize(traces)
        return json.dumps(
            {
                "ok": True,
                "window_days": days,
                "trace_dir": str(TRACE_DIR),
                **summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return _err("oiagent_status", e)


# ── Dynamic Registry Exports ─────────────────────────
TOOL_DEFS = [
    {
        "name": "oiagent_status",
        "description": (
            "读 ~/.claude/oiagent_harness_training/*.jsonl,"
            "输出 prisiragent-team-lead 派单工作健康度:"
            "{total events, by_event/intent/agent/pool, rule_hit_rate, race_winners, latest 10}"
            "默认看最近 7 天"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "看最近多少天(1-90,默认 7)",
                    "minimum": 1,
                    "maximum": 90,
                },
            },
            "required": [],
        },
    },
]


HANDLERS = {
    "oiagent_status": oiagent_status_impl,
}


# ============================================================
# Standalone CLI
# ============================================================
def _cli():
    import argparse

    p = argparse.ArgumentParser(description="oiagent_status — 看派单工作健康度")
    p.add_argument("--days", type=int, default=7, help="最近 N 天(1-90)")
    args = p.parse_args()
    print(oiagent_status_impl(args.days))


if __name__ == "__main__":
    _cli()