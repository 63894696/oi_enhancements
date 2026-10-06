# -*- coding: utf-8 -*-
"""多步研究门面 v0.1.0 (P2.5+16d, 2026-09-23)

plan → search ×N → fetch top URLs → LLM 合成,带 [n] 编号引用。
对齐 Perplexity/Comet 范式(参考 memory/search-design-perplexity-tabbit-recon)。

设计原则: 任何环节失败/超时都降级,warnings 透出,绝不抛。
零外部依赖(仅 stdlib);LLM 可选(默认 None → 简单拼接 snippets)。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _step(name: str, ok: bool = True, **extra) -> dict:
    out = {"step": name, "ok": ok, "duration_ms": 0}
    out.update(extra)
    return out


def plan_queries(query: str, *, llm_call: Callable | None = None) -> list[str]:
    """生成 2-4 个搜索子查询。

    llm_call 存在 → 调它(传 prompt 让 LLM 返 2-4 个查询,以 JSON 数组字符串)。
    llm_call 不存在 → 简单模板拼接(原 query + 3 个变体)。

    任何异常吞掉,降级到模板拼接。
    """
    base = (query or "").strip()
    if not base:
        return []

    # LLM 优先
    if llm_call is not None:
        try:
            prompt = (
                f"Generate 2-4 different search queries to research: \"{base}\".\n"
                "Return ONLY a JSON array of strings, no other text.\n"
                "Example: [\"q1\", \"q2\", \"q3\"]"
            )
            raw = llm_call(prompt)
            import json, re
            # 容错提取 JSON 数组
            m = re.search(r"\[.*?\]", raw, re.DOTALL)
            if m:
                arr = json.loads(m.group(0))
                if isinstance(arr, list) and all(isinstance(s, str) for s in arr) and arr:
                    return arr[:4]
        except Exception as e:  # noqa: BLE001
            log.warning("plan_queries LLM failed, fallback to template: %s", e)

    # 模板拼接(降级)
    out = [base]
    if len(base) > 5:
        out.append(base + " 对比 vs 比较")
    out.append(base + " 最新 2026")
    out.append(base + " 是什么 what is")
    # 去重保序
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:4]


def _search_one(q: str, limit: int, timeout: float) -> list[dict]:
    try:
        from prisir_work import web_search as _ws
        return _ws.search(q, limit=limit, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        log.warning("search failed for q=%r: %s", q, e)
        return []


# ---------------------------------------------------------------------------
# P3j T20-D: Agent-Reach 集成(query 含 reach 类站点关键词 → 优先走 reach.search)
# ---------------------------------------------------------------------------

# 关键词 → (reach platform id, 是否作为 search provider)
# 单次研究只取首个命中平台(避免一次研究跑 4 个平台拖慢)
_REACH_RULES: list[tuple[str, str]] = [
    ("小红书", "xhs"),
    ("xhs", "xhs"),
    ("b站", "bilibili-search"),
    ("bilibili", "bilibili-search"),
    ("v2ex", "v2ex"),
    ("github", "github"),
    ("youtube", "youtube-subtitle"),
    ("字幕", "bilibili-subtitle"),
    ("rss", "rss"),
]


def _detect_reach_intent(query: str) -> tuple[str, str] | None:
    """从 query 里检测 reach 站点意图 → (platform, sub_query) or None。

    单次研究只取首个命中(用户原话「不要一次跑多个平台拖慢响应」)。
    """
    base = (query or "").strip().lower()
    if not base:
        return None
    for kw, plat in _REACH_RULES:
        if kw in base:
            sub = base.replace(kw, "").strip() or base
            return (plat, sub)
    return None


def _reach_search_one(platform: str, query: str, limit: int = 5,
                      timeout: float = 15.0) -> list[dict]:
    """调 agent_reach_bridge.search → 统一异常。失败返 []."""
    try:
        from prisir_work import agent_reach_bridge as _arb
        r = _arb.search(platform, query, limit=limit, timeout=timeout)
        if not r.get("ok"):
            log.warning("reach search %s failed: %s", platform, r.get("error"))
            return []
        out: list[dict] = []
        for item in r.get("results", []) or []:
            out.append({
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "sources": [f"reach:{platform}"],
            })
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("reach search %s raised %s: %s", platform, type(e).__name__, e)
        return []


def _jina_search_one(query: str, limit: int = 5,
                     timeout: float = 15.0) -> list[dict]:
    """P3j T20-I: 用 jina s.jina.ai 全文搜索(高质,query→top N)。失败返 []."""
    import os
    # 只在用户配了 JINA_API_KEY 或自部署 URL 时启用
    if not (os.environ.get("JINA_API_KEY", "").strip()
            or os.environ.get("JINA_SEARCH_URL", "").strip()):
        return []
    try:
        from prisir_work import web_fetch_jina as _jina
        items = _jina.jina_search(query, limit=limit,
                                  options={"timeout": timeout})
        out: list[dict] = []
        for it in items or []:
            url = it.get("url", "")
            if not url:
                continue
            out.append({
                "url": url,
                "title": it.get("title", "") or url,
                "snippet": (it.get("snippet") or it.get("content", "")[:300]),
                "sources": ["jina_search"],
            })
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("jina_search failed for %r: %s", query, e)
        return []


def _fetch_one(url: str, timeout: float) -> dict | None:
    try:
        from prisir_work import web_fetch as _wf
        result = _wf.fetch(url, options={"timeout": timeout})
        if result.get("ok") and result.get("content"):
            return result
        return None
    except Exception as e:  # noqa: BLE001
        log.warning("fetch failed for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# P3j T21-A: feedparser 集成(query 里直接出现 feed URL → 抓 feed 插队)
# ---------------------------------------------------------------------------


def _detect_feedparser_intent(query: str) -> str | None:
    """从 query 里挑出第一个 feed URL(没有 → None)。

    单次研究只取首个(同 reach / gh)。
    """
    base = (query or "").strip()
    if not base:
        return None
    urls = re.findall(r"https?://[^\s<>\"'\\)]+", base)
    feed_tokens = (".xml", ".rss", "/feed", "/atom", "/rss",
                   "?alt=rss", "?format=rss", "?output=rss",
                   "?type=rss", "/feed.json", "/index.xml")
    for u in urls:
        ul = u.lower()
        if any(tok in ul for tok in feed_tokens):
            return u
    return None


def _feedparser_fetch_one(url: str, timeout: float = 15.0,
                          max_items: int = 20) -> dict | None:
    """调 web_fetch_feedparser.feedparser_fetch → fetch 记录(给 sources 用)。

    失败返 None(同 _fetch_one)。
    """
    try:
        from prisir_work import web_fetch_feedparser as _fp
        r = _fp.feedparser_fetch(url, options={"timeout": timeout,
                                                "max_items": max_items})
        if not r.get("meta", {}).get("ok"):
            log.warning("feedparser_fetch failed: %s",
                        r.get("meta", {}).get("error"))
            return None
        return {
            "content": r.get("content", ""),
            "fetcher": "feedparser",
            "feed_meta": r.get("meta", {}),
        }
    except Exception as e:  # noqa: BLE001
        log.warning("feedparser_fetch raised %s: %s", type(e).__name__, e)
        return None


def _build_prompt(query: str, sources: list[dict]) -> str:
    parts = [
        "你是一个研究助手。基于以下素材回答用户问题。",
        "要求:",
        "- 答案必须有事实依据,引用素材编号 [1][2][3]",
        "- 不要编造信息,素材没提就说「素材未提及」",
        "- 简短(中文 ≤ 500 字 / 英文 ≤ 300 词)",
        "",
        f"用户问题: {query}",
        "",
        "素材:",
    ]
    for s in sources:
        parts.append(f"[{s['n']}] {s.get('title', '')} ({s['url']})")
        parts.append(s.get("snippet", "")[:600])
        parts.append("")
    parts.append("回答:")
    return "\n".join(parts)


def research(query: str, *, max_steps: int = 4, max_urls: int = 8,
             timeout: float = 30.0,
             llm_call: Callable | None = None) -> dict:
    """多步研究门面。完整返回 dict(永 raise;失败降级返 ok=True + warnings)。"""
    warnings: list[str] = []
    steps: list[dict] = []
    sources: list[dict] = []

    query = (query or "").strip()
    if not query:
        return {"ok": False, "query": "", "answer": "", "sources": [],
                "citations": [], "warnings": ["empty_query"], "steps": [],
                "plan": []}

    # ── step 1: plan ──
    t0 = _now_ms()
    try:
        plan = plan_queries(query, llm_call=llm_call)
        if not plan:
            plan = [query]
    except Exception as e:  # noqa: BLE001
        warnings.append("plan_failed")
        plan = [query]
    steps.append(_step("plan", ok=True, queries=len(plan), duration_ms=_now_ms() - t0))

    # ── step 2: search (并发) ──
    t0 = _now_ms()
    search_results: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(plan))) as pool:
            futures = {pool.submit(_search_one, q, 5, timeout): q for q in plan}
            for fut in as_completed(futures, timeout=timeout):
                for r in fut.result() or []:
                    search_results.append(r)
    except Exception as e:  # noqa: BLE001
        warnings.append("search_failed")

    # ── step 2a: P3j T20-I jina 全文搜索(JINA_API_KEY / JINA_SEARCH_URL 配了才生效) ──
    # 每个 plan query 并发跑一遍 jina,前面插队(去重时它会盖后到的 web_search 同 URL)
    jina_results: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(plan))) as pool:
            futures = {pool.submit(_jina_search_one, q, 5, timeout): q for q in plan}
            for fut in as_completed(futures, timeout=timeout):
                for r in fut.result() or []:
                    jina_results.append(r)
        if jina_results:
            warnings.append(f"jina_used:{len(jina_results)}")
            merged = []
            seen = set()
            for r in (jina_results + search_results):
                u = r.get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    merged.append(r)
                elif not u:
                    merged.append(r)
            search_results = merged
    except Exception as e:  # noqa: BLE001
        warnings.append(f"jina_failed:{type(e).__name__}")

    # ── step 2b: P3j T20-D reach 集成(query 含 reach 类站点 → 优先 reach.search) ──
    reach_intent = _detect_reach_intent(query)
    reach_results: list[dict] = []
    if reach_intent is not None:
        plat, sub = reach_intent
        try:
            reach_results = _reach_search_one(plat, sub, limit=5, timeout=min(timeout, 15.0))
            if reach_results:
                warnings.append(f"reach_used:{plat}")
                # reach 结果优先插入(去重时它会盖后到的 web_search 同 URL)
                merged = []
                seen = set()
                for r in (reach_results + search_results):
                    u = r.get("url", "")
                    if u and u not in seen:
                        seen.add(u)
                        merged.append(r)
                    elif not u:
                        merged.append(r)
                search_results = merged
            else:
                warnings.append(f"reach_empty:{plat}")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"reach_failed:{plat}:{type(e).__name__}")

    # ── step 2c: P3j T21-A feedparser 集成(query 里直接出现 feed URL → 抓) ──
    feed_url = _detect_feedparser_intent(query)
    feed_results: list[dict] = []
    if feed_url:
        try:
            payload = _feedparser_fetch_one(feed_url,
                                            timeout=min(timeout, 15.0),
                                            max_items=20)
            if payload:
                feed_meta = payload.get("feed_meta", {}) or {}
                feed_results = [{
                    "url": feed_url,
                    "title": (feed_meta.get("feed_title", "Feed")
                              or "Feed"),
                    "snippet": (payload.get("content", "")[:600]),
                    "sources": ["feedparser"],
                    "item_count": feed_meta.get("item_count", 0),
                }]
                warnings.append(f"feedparser_used:{feed_meta.get('item_count', 0)}")
                # feed 结果插入到最前面(整条优先于 web_search 散条目)
                merged = []
                seen = set()
                for r in (feed_results + search_results):
                    u = r.get("url", "")
                    if u and u not in seen:
                        seen.add(u)
                        merged.append(r)
                    elif not u:
                        merged.append(r)
                search_results = merged
            else:
                warnings.append("feedparser_empty")
        except Exception as e:  # noqa: BLE001
            warnings.append(f"feedparser_failed:{type(e).__name__}")

    # 去重(URL)
    seen_urls, uniq_results = set(), []
    for r in search_results:
        u = r.get("url", "")
        if u and u not in seen_urls:
            seen_urls.add(u)
            uniq_results.append(r)

    # 评分排序(已在 web_search 排过,这里只截断)
    top_results = uniq_results[:max_urls]
    reach_count = sum(1 for r in uniq_results
                      if r.get("sources") and any(s.startswith("reach:") for s in r["sources"]))
    steps.append(_step("search", ok=True, queries=len(plan),
                       results=len(uniq_results),
                       top_kept=len(top_results),
                       reach_hits=reach_count,
                       duration_ms=_now_ms() - t0))

    # ── step 3: fetch top URLs (并发) ──
    t0 = _now_ms()
    fetched: list[tuple[dict, dict | None]] = [(r, None) for r in top_results]

    def _do_fetch(idx_url):
        idx, url = idx_url
        return idx, _fetch_one(url, min(8.0, timeout))

    try:
        with ThreadPoolExecutor(max_workers=min(8, len(top_results))) as pool:
            for idx, payload in pool.map(_do_fetch, enumerate([r["url"] for r in top_results])):
                if payload:
                    fetched[idx] = (fetched[idx][0], payload)
                else:
                    warnings.append(f"fetch_failed:{top_results[idx]['url'][:60]}")
    except Exception as e:  # noqa: BLE001
        warnings.append("fetch_batch_failed")

    # 组装 sources
    for n, (r, payload) in enumerate(fetched, start=1):
        if not payload:
            continue
        sources.append({
            "n": n,
            "url": r["url"],
            "title": r.get("title", ""),
            "snippet": (payload.get("content", "")[:600] if payload else r.get("snippet", "")),
            "fetched_at": payload.get("fetched_at", ""),
            "fetcher": payload.get("fetcher", ""),
        })
    steps.append(_step("fetch", ok=True, urls=len(top_results),
                       fetched=sum(1 for _, p in fetched if p),
                       duration_ms=_now_ms() - t0))

    # ── step 4: synthesize ──
    t0 = _now_ms()
    answer = ""
    model_used = "mock"
    if sources:
        prompt = _build_prompt(query, sources)
        if llm_call is not None:
            try:
                answer = llm_call(prompt)
                model_used = "user_llm"
            except Exception as e:  # noqa: BLE001
                warnings.append(f"synthesize_failed:{type(e).__name__}")
        if not answer:
            # 降级:拼接 snippets
            answer = "LLM 合成失败,以下是原始素材:\n\n"
            for s in sources:
                answer += f"[{s['n']}] {s['title']} ({s['url']})\n{s['snippet']}\n\n"
    else:
        answer = "无任何可用素材。search 阶段未返结果,或所有 fetch 失败。"
        warnings.append("no_sources")

    steps.append(_step("synthesize", ok=True, model=model_used,
                       answer_chars=len(answer),
                       duration_ms=_now_ms() - t0))

    citations = [{"n": s["n"], "url": s["url"]} for s in sources]
    return {
        "ok": True,
        "query": query,
        "plan": plan,
        "sources": sources,
        "answer": answer,
        "citations": citations,
        "warnings": warnings,
        "steps": steps,
    }


if __name__ == "__main__":
    import sys, json
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    print(json.dumps(research(q), ensure_ascii=False, indent=2))
