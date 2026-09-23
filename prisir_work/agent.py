# -*- coding: utf-8 -*-
"""LLM 驱动自主采集 v0.1.0 (P2.5+17d, 2026-09-23)

对 wigolo agent 工具的 Prisir 对应:
  - LLM 每步决策:search / fetch / extract / stop,带 args + reason
  - 累积 findings(URL → brief summary)供后续决策参考
  - 无 LLM 时:模板决策(search → fetch top 1 → extract 字段)
  - 早停:LLM 返 stop / max_steps 耗尽

设计原则:失败降级、绝不抛。LLM 任何异常 → 走 no-llm 模板链。
零外部依赖。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _step(name: str, ok: bool = True, **extra) -> dict:
    out = {"step": name, "ok": ok, "duration_ms": 0}
    out.update(extra)
    return out


def _parse_llm_decision(raw: str) -> dict | None:
    """从 LLM 输出解析决策 JSON。"""
    try:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "action" in obj:
                return obj
    except Exception:
        pass
    return None


def _build_decision_prompt(query: str, findings: list[dict], step: int,
                            max_steps: int) -> str:
    findings_text = "\n".join(
        f"- {f.get('url','?')[:60]:60s} | {f.get('summary','')[:120]}"
        for f in findings[-5:]
    ) or "(no findings yet)"
    return (
        f"You are a web research agent. Step {step}/{max_steps}.\n"
        f"Goal: gather information to answer: \"{query}\".\n\n"
        f"Findings so far ({len(findings)}):\n{findings_text}\n\n"
        "Decide the next action. Return ONLY a JSON object:\n"
        '{"action": "search"|"fetch"|"extract"|"stop", '
        '"args": { ...action-specific }, "reason": "..."}\n'
        "- search: args = {\"query\": \"...\"}\n"
        "- fetch: args = {\"url\": \"...\"}\n"
        "- extract: args = {\"url\": \"...\", \"schema\": [\"field1\", \"field2\"]}\n"
        "- stop: args = {\"answer\": \"final concise answer\"}\n\n"
        "Be concise. Stop as soon as you have enough info."
    )


def _template_decision(query: str, findings: list[dict], step: int) -> dict:
    """无 LLM 时的模板决策链:search(若没搜过)→ fetch top 1(若有 URL)→ stop。"""
    fetched_urls = {f.get("url") for f in findings if f.get("action") == "fetch"}
    if step == 0:
        return {"action": "search", "args": {"query": query},
                "reason": "template: initial search"}
    if not fetched_urls and findings:
        # 拿 search 结果的第一条 URL 去 fetch
        for f in reversed(findings):
            if f.get("action") == "search" and f.get("urls"):
                top = f["urls"][0]
                return {"action": "fetch", "args": {"url": top.get("url", "")},
                        "reason": "template: fetch top search result"}
        return {"action": "stop", "args": {"answer": "no URL to fetch"},
                "reason": "template: nothing more to do"}
    if len(findings) < 4 and step < 5:
        return {"action": "search",
                "args": {"query": f"{query} (additional context)"},
                "reason": "template: gather more"}
    return {"action": "stop",
            "args": {"answer": " | ".join(
                f.get("summary", "")[:80] for f in findings[-3:]
            ) or "(no findings)"},
            "reason": "template: max steps or enough findings"}


def _summarize_search(result: list[dict]) -> str:
    if not result:
        return "no results"
    return f"{len(result)} URLs, top: {result[0].get('title','')[:60]}"


def _summarize_fetch(result: dict) -> str:
    if not result.get("ok"):
        return "fetch failed"
    return f"{len(result.get('content',''))} chars fetched"


def _summarize_extract(result: dict) -> str:
    if not result.get("ok"):
        return "extract failed"
    data = result.get("data") or {}
    keys = ", ".join(f"{k}={str(v)[:40]}" for k, v in data.items() if v is not None)
    return f"extracted: {keys}" if keys else "extract: empty"


def agent(query: str, *, max_steps: int = 6, llm_call: Callable | None = None,
          timeout: float = 10.0) -> dict:
    """LLM 驱动多步自主采集。

    Args:
        query: 研究目标
        max_steps: 最大决策步数(默认 6)
        llm_call: 可选 LLM 函数(prompt: str) -> str;None → 模板决策
        timeout: 单次 HTTP 超时

    Returns:
    {
        'ok': True,
        'query': str,
        'answer': str,             # LLM stop 时给的 final answer,或模板拼凑
        'findings': [{step, action, args, result_summary, url?}],
        'steps': [...],
        'mode': 'llm' | 'template',
        'warnings': list[str]
    }
    """
    warnings: list[str] = []
    steps_log: list[dict] = []
    findings: list[dict] = []
    final_answer = ""
    mode = "llm" if llm_call is not None else "template"

    if not query or not query.strip():
        return {"ok": False, "query": query, "answer": "", "findings": [],
                "steps": [], "mode": mode, "warnings": ["empty_query"]}

    for step in range(max_steps):
        t0 = _now_ms()

        # ── 1. 决策 ──
        if llm_call is not None:
            prompt = _build_decision_prompt(query, findings, step, max_steps)
            decision = None
            try:
                raw = llm_call(prompt)
                decision = _parse_llm_decision(raw)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"llm_failed_step_{step}:{type(e).__name__}")
            if decision is None:
                # LLM 输出解析失败 → 降级模板
                decision = _template_decision(query, findings, step)
                mode = "template_fallback"
        else:
            decision = _template_decision(query, findings, step)

        action = decision.get("action", "stop")
        args = decision.get("args") or {}
        reason = decision.get("reason", "")

        # ── 2. 执行 ──
        result: Any = None
        result_summary = ""
        url_for_finding = None

        if action == "search":
            try:
                from prisir_work import web_search as _ws
                q = (args.get("query") or query).strip()
                # 优先用 mock provider(测试/E2E 注入);没有 mock 才走默认全 provider。
                # 默认 provider(ddg/baidu/bing_public)在无外网环境会超时 8s × N,
                # 太慢,通过 env PRISIR_WEB_SEARCH_PROVIDERS 限定名单。
                import os
                prov_env = os.environ.get("PRISIR_WEB_SEARCH_PROVIDERS", "").strip()
                providers = [p.strip() for p in prov_env.split(",") if p.strip()] or None
                result = _ws.search(q, limit=5, providers=providers,
                                    timeout=min(4.0, timeout))
                result_summary = _summarize_search(result or [])
                # 把 top URLs 写进 finding 供后续决策
                finding_urls = [{"url": r.get("url", ""), "title": r.get("title", "")}
                               for r in (result or [])[:5]]
            except Exception as e:  # noqa: BLE001
                warnings.append(f"search_failed_step_{step}:{type(e).__name__}")
                finding_urls = []

            findings.append({
                "step": step,
                "action": "search",
                "args": args,
                "reason": reason,
                "result_summary": result_summary,
                "urls": finding_urls,
            })

        elif action == "fetch":
            url = (args.get("url") or "").strip()
            url_for_finding = url
            if not url:
                result_summary = "missing url"
                warnings.append(f"fetch_missing_url_step_{step}")
            else:
                try:
                    from prisir_work import web_fetch as _wf
                    result = _wf.fetch(url, options={"timeout": timeout})
                    result_summary = _summarize_fetch(result or {})
                except Exception as e:  # noqa: BLE001
                    warnings.append(f"fetch_failed_step_{step}:{type(e).__name__}")
                    result_summary = "fetch error"
            findings.append({
                "step": step,
                "action": "fetch",
                "args": args,
                "reason": reason,
                "url": url,
                "result_summary": result_summary,
            })

        elif action == "extract":
            url = (args.get("url") or "").strip()
            url_for_finding = url
            schema = args.get("schema") or []
            if not url:
                result_summary = "missing url"
                warnings.append(f"extract_missing_url_step_{step}")
            else:
                try:
                    from prisir_work import extract as _ex
                    result = _ex.extract(url, schema, timeout=timeout,
                                         use_fetch=True)
                    result_summary = _summarize_extract(result or {})
                except Exception as e:  # noqa: BLE001
                    warnings.append(f"extract_failed_step_{step}:{type(e).__name__}")
                    result_summary = "extract error"
            findings.append({
                "step": step,
                "action": "extract",
                "args": args,
                "reason": reason,
                "url": url,
                "result_summary": result_summary,
            })

        elif action == "stop":
            final_answer = (args.get("answer") or "").strip()
            findings.append({
                "step": step,
                "action": "stop",
                "args": args,
                "reason": reason,
                "result_summary": f"final: {final_answer[:120]}",
            })
            steps_log.append(_step("stop", ok=True, step=step,
                                   duration_ms=_now_ms() - t0))
            break

        else:
            warnings.append(f"unknown_action_step_{step}:{action}")
            findings.append({
                "step": step, "action": action, "args": args, "reason": reason,
                "result_summary": "unknown action (skipped)",
            })

        steps_log.append(_step(action, ok=True, step=step,
                               duration_ms=_now_ms() - t0))

    # 没明确 stop 时,模板拼 final answer
    if not final_answer:
        final_answer = " | ".join(
            f.get("result_summary", "") for f in findings[-3:]
        ) or "(no findings)"

    return {
        "ok": True,
        "query": query,
        "answer": final_answer,
        "findings": findings,
        "steps": steps_log,
        "mode": mode,
        "warnings": warnings,
    }


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) < 2:
        print('usage: python -m prisir_work.agent <query> [--max-steps N]')
        raise SystemExit(2)
    _q = _sys.argv[1]
    _m = int(_sys.argv[_sys.argv.index("--max-steps") + 1]) if "--max-steps" in _sys.argv else 6
    print(json.dumps(agent(_q, max_steps=_m), ensure_ascii=False, indent=2))