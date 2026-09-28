#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_llm_real.py — M3.68 LLM 真测 intents/task/safety(2026-09-24)

目的:
  - 用 OpenRouter 上的通用 LLM(GPT-4o-mini / Claude Haiku / Gemini Flash)真测
  - 同 fixtures + 同 questions schema + 同 metric
  - 输出每 model × scenario 的 ACC + latency + 价

对比:
  - bench_jev_real.py 的 Jev 7B 数据(已有)
  - bench_all.py 的本地 LoRA 数据(已有)
  - 本脚本的 LLM 真测数据(本次新增)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from test_fixtures import load_fixture  # noqa: E402


# 复用 bench_jev_real.py 的 JEV_QUESTIONS(同 schema,LLM 也能消费 dict 结构化)
sys.path.insert(0, str(_HERE))
from bench_jev_real import JEV_QUESTIONS, SCENARIOS  # noqa: E402


# 模型候选(OpenRouter 路由)
MODEL_CANDIDATES = [
    ("openai/gpt-4o-mini",          "$0.15/M",  "便宜 + 强"),
    ("anthropic/claude-3.5-haiku",  "$0.80/M",  "中价 + 强"),
    ("google/gemini-2.0-flash-exp", "免费",     "快速免费"),
    ("meta-llama/llama-3.1-8b-instruct", "$0.06/M", "便宜开源"),
]


def _build_messages(state: dict, questions: dict) -> list[dict]:
    """Jev questions dict → OpenAI 兼容 messages"""
    user_text = state.get("user_msg", "")
    q_desc = []
    for qid, q in questions.items():
        instr = q.get("instructions", "")
        if q.get("type") == "choice":
            crit = q.get("criteria", {})
            opts = "\n".join(f"- {k}: {v}" for k, v in crit.items())
            q_desc.append(f"Q[{qid}] (choice): {instr}\n选项:\n{opts}")
        elif q.get("type") == "score":
            crit = q.get("criteria", [])
            q_desc.append(f"Q[{qid}] (score 1-5): {instr}\n等级:{crit}")
        elif q.get("type") == "noul":
            q_desc.append(f"Q[{qid}] (yes/no): {instr}")
    q_text = "\n\n".join(q_desc)
    sys_prompt = (
        "你是分类助手。严格按用户消息回答以下问题,用 JSON 输出。"
        "格式:{\"" + "\",\"".join(questions.keys()) + "\": ...}"
        "- choice: 选最匹配的 option key\n"
        "- score: 返回等级 label\n"
        "- noul: 返回 {\"yes\": true/false, \"probability\": 0-1}\n"
        "只输出 JSON,不要解释。"
    )
    user = f"用户消息:\n\"\"\"\n{user_text}\n\"\"\"\n\n问题:\n{q_text}"
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user",   "content": user},
    ]


def _extract_pred(scenario: str, content: str) -> str:
    """LLM 输出 → 预测 label"""
    if not content:
        return "empty"
    content = content.strip()
    # 尝试直接 parse JSON
    s = SCENARIOS[scenario]
    try:
        # 有时 LLM 包在 ```json ... ``` 里
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        obj = json.loads(content)
    except Exception:
        # 兜底:在 content 里找关键词
        return "parse_fail"

    q = next(iter(questions_view[scenario].keys()))  # 第一题
    val = obj.get(q, {})
    if isinstance(val, dict):
        if "choice" in val:   return val["choice"]
        if "score" in val:    return val["score"]
        if "yes" in val:      return "unsafe" if val.get("yes") else "safe"
        if "value" in val:    return str(val["value"])
    if isinstance(val, str):
        return val
    return "unknown"


# cache questions per scenario(避免在 _extract_pred 内每次构造)
questions_view = {}


async def _llm_call(model: str, messages: list[dict], api_key: str,
                    timeout_s: float = 30.0) -> tuple[str, int]:
    """OpenRouter 调一次 → (content, latency_ms)"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/prisir/companion",
        "X-Title":       "prisIr-companion-bench",
    }
    payload = {
        "model":    model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens":  200,
    }
    t0 = time.time()
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            data = await resp.json()
            latency_ms = int((time.time() - t0) * 1000)
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}: {data}")
            content = (data["choices"][0]["message"].get("content") or "").strip()
            return content, latency_ms


async def _bench_scenario(model: str, scenario: str, cases: list[dict],
                          api_key: str) -> dict:
    s = SCENARIOS[scenario]
    questions = JEV_QUESTIONS[scenario]
    questions_view[scenario] = questions
    extract = s["extract_jev"]
    results = []
    t_start = time.time()
    for i, case in enumerate(cases, 1):
        state = {"user_msg": case["text"]}
        messages = _build_messages(state, questions)
        try:
            content, latency_ms = await _llm_call(model, messages, api_key)
            actual = _extract_pred(scenario, content)
            ok = (actual == case.get("expect"))
        except Exception as e:
            actual = "error"
            ok = False
            latency_ms = 0
            print(f"  ! [{i}] {type(e).__name__}: {str(e)[:80]}", flush=True)
        results.append({
            "i": i, "id": case["id"], "expect": case.get("expect"),
            "actual": actual, "match": ok, "elapsed_ms": latency_ms,
        })
        mark = "✓" if ok else "✗"
        print(f"  {mark} [{i}/{len(cases)}] expect={case.get('expect')} "
              f"actual={actual} ({latency_ms}ms)", flush=True)
        await asyncio.sleep(0.2)  # 限速

    n = len(results)
    matches = sum(1 for r in results if r.get("match"))
    latencies = sorted(r["elapsed_ms"] for r in results if r["elapsed_ms"] > 0)
    return {
        "n": n,
        "accuracy": round(matches / n, 4) if n else 0,
        "latency_p50_ms": latencies[len(latencies)//2] if latencies else 0,
        "latency_avg_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "elapsed_total_s": round(time.time() - t_start, 2),
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="M3.68 LLM 真测 intents/task/safety")
    ap.add_argument("--scenarios", default="intents,task,safety")
    ap.add_argument("--model",     default="openai/gpt-4o-mini")
    ap.add_argument("--limit",     type=int, default=0)
    ap.add_argument("--output",    default="reports/bench_llm_real_2026-09-24.json")
    args = ap.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("❌ OPENROUTER_API_KEY 未设")
        return 1

    scenarios = args.scenarios.split(",")
    print(f"=== model={args.model} scenarios={scenarios} ===", flush=True)
    summary = {"model": args.model, "scenarios": {}}
    for s in scenarios:
        cases = load_fixture(SCENARIOS[s]["fixture"])
        if args.limit > 0:
            cases = cases[:args.limit]
        print(f"\n--- [{s}] {len(cases)} cases ---", flush=True)
        result = asyncio.run(_bench_scenario(args.model, s, cases, api_key))
        summary["scenarios"][s] = result
        print(f"  >>> {s}: ACC={result['accuracy']*100:.1f}% "
              f"({result.get('n', 0) - sum(1 for r in result['results'] if not r['match'])}/{result['n']}) "
              f"p50={result['latency_p50_ms']}ms", flush=True)

    out_path = _HERE / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n报告: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
