# -*- coding: utf-8 -*-
"""exa_bridge.py — Exa MCP 语义搜索直接调(2026-09-26 ship,P3j T22-A)。

Exa (https://exa.ai) 是「语义搜索」MCP 工具,跟 jina/DDG/Baidu 不同,
它用 embedding + LLM 重排序,返回高质量结果(适合研究 / 调研场景)。

设计:
  · 调 Exa REST API(POST https://api.exa.ai/search)
  · key from env EXA_API_KEY(env 全空 → installed=False,不 raise)
  · 默认 type=auto(Exa 自适应 deep / fast)+ numResults=10
  · 失败一律返 {ok: False, error: "exa_xxx"} 不 raise
  · 单次 timeout 30s

公开 API(4 fn):
  · exa_health()                          → {ok, installed, key_set, mode}
  · exa_search(query, options)            → {ok, results: [...], cost}
  · exa_find_similar(url, options)        → {ok, results: [...], cost}
  · exa_answer(query, options)            → {ok, answer: "...", citations}

注:`/findSimilar` 和 `/answer` 端点 Exa 也支持(2026-09 文档),
答案端点更适合「研究综述」场景。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

EXA_API_BASE = "https://api.exa.ai"
EXA_TIMEOUT = 30.0


def _key() -> str:
    return os.environ.get("EXA_API_KEY", "").strip()


def _http_post(path: str, payload: dict[str, Any],
               *, timeout: float = EXA_TIMEOUT) -> dict[str, Any]:
    """POST 到 Exa API,统一异常处理。

    返回 {ok: True, data: <parsed_json>} 或 {ok: False, error: ...}
    """
    key = _key()
    if not key:
        return {"ok": False, "installed": False,
                "error": "exa_not_configured",
                "hint": "EXA_API_KEY 未设(env / media_keys.json)"}
    url = f"{EXA_API_BASE}{path}"
    headers = {
        "x-api-key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "data": json.loads(raw),
                    "status": int(resp.getcode() or 200)}
    except urllib.error.HTTPError as e:
        # Exa 错误信封:{requestId, error, tag}
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        tag = ""
        try:
            tag = json.loads(err_body).get("tag", "")
        except Exception:
            pass
        return {"ok": False, "status": int(e.code),
                "error": f"exa_http_{e.code}",
                "tag": tag, "detail": err_body[:300]}
    except urllib.error.URLError as e:
        return {"ok": False, "error": "exa_url_error",
                "detail": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"exa_{type(e).__name__}",
                "detail": str(e)}


# ---------------------------------------------------------------------------
# 公开 API 1: exa_health
# ---------------------------------------------------------------------------

def exa_health() -> dict[str, Any]:
    """检查 EXA_API_KEY 是否设 + 简单 ping(/search 空 query)。"""
    key = _key()
    if not key:
        return {"ok": True, "installed": False, "key_set": False,
                "mode": "missing_key",
                "hint": "EXA_API_KEY 未设(env / media_keys.json)"}
    # 试一次最小调用(numResults=1)做探活
    r = _http_post("/search", {"query": "ping", "numResults": 1},
                   timeout=10.0)
    if r.get("ok"):
        return {"ok": True, "installed": True, "key_set": True,
                "mode": "live", "key_prefix": key[:8] + "...",
                "search_time_ms": r["data"].get("searchTime", 0)}
    # INVALID_API_KEY 是 key 配错(但格式对),也标 installed=True
    if r.get("tag") == "INVALID_API_KEY":
        return {"ok": True, "installed": True, "key_set": True,
                "mode": "key_invalid",
                "key_prefix": key[:8] + "...",
                "warning": "Exa 返回 INVALID_API_KEY,key 失效",
                "tag": r.get("tag")}
    # RATE_LIMIT_EXCEEDED 等也标 installed=True(说明 key 对,只是限流)
    return {"ok": True, "installed": True, "key_set": True,
            "mode": "error",
            "key_prefix": key[:8] + "...",
            "error": r.get("error", "unknown"),
            "tag": r.get("tag", "")}


# ---------------------------------------------------------------------------
# 公开 API 2: exa_search
# ---------------------------------------------------------------------------

def exa_search(query: str, *,
               num_results: int = 10,
               search_type: str = "auto",
               category: str = "",
               include_domains: list[str] | None = None,
               exclude_domains: list[str] | None = None,
               text: bool = True,
               max_chars: int = 3000,
               highlights: bool = True,
               timeout: float = EXA_TIMEOUT) -> dict[str, Any]:
    """语义搜索 query → [{url, title, snippet, text, highlights}]。

    返回:{ok, query, results, sources: ["exa_search"] * N, cost_dollars,
          search_time_ms}

    失败:{ok: False, error: "exa_xxx", ...}
    """
    if not query or not query.strip():
        return {"ok": False, "error": "empty_query"}
    safe_n = min(max(int(num_results), 1), 100)

    payload: dict[str, Any] = {
        "query": query.strip(),
        "numResults": safe_n,
        "type": search_type if search_type in
                ("instant", "fast", "auto", "deep-lite", "deep",
                 "deep-reasoning") else "auto",
    }
    if category and category.strip():
        payload["category"] = category.strip()
    if include_domains:
        payload["includeDomains"] = include_domains[:1200]
    if exclude_domains:
        payload["excludeDomains"] = exclude_domains[:1200]
    # contents
    contents: dict[str, Any] = {}
    if text:
        contents["text"] = {"maxCharacters": int(max_chars)}
    if highlights:
        contents["highlights"] = {
            "maxCharacters": min(int(max_chars), 500)}
    if contents:
        payload["contents"] = contents

    r = _http_post("/search", payload, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "query": query, **r}
    data = r["data"]
    raw_results = data.get("results") or []
    results: list[dict[str, Any]] = []
    for it in raw_results:
        url = it.get("url", "")
        title = it.get("title", "") or ""
        if not url:
            continue
        # snippet 优先用 highlights,没有则用 text 前 300 字符
        highlights_data = it.get("highlights") or []
        snippet = ""
        if highlights_data and isinstance(highlights_data, list):
            snippet = " · ".join(str(h) for h in highlights_data[:3])
        if not snippet:
            txt = it.get("text", "") or ""
            snippet = txt[:300]
        results.append({
            "url": url,
            "title": title,
            "snippet": snippet,
            "text": it.get("text", "") or "",
            "published_date": it.get("publishedDate", ""),
            "author": it.get("author", ""),
            "image": it.get("image", ""),
        })

    return {
        "ok": True,
        "query": query,
        "results": results,
        "sources": ["exa_search"] * len(results),
        "cost_dollars": data.get("costDollars", {}),
        "search_time_ms": data.get("searchTime", 0),
        "resolved_type": data.get("resolvedSearchType", ""),
    }


# ---------------------------------------------------------------------------
# 公开 API 3: exa_find_similar(URL → 类似内容)
# ---------------------------------------------------------------------------

def exa_find_similar(url: str, *, num_results: int = 10,
                     exclude_source_domain: bool = True,
                     text: bool = True, max_chars: int = 3000,
                     timeout: float = EXA_TIMEOUT) -> dict[str, Any]:
    """给定 URL,找类似内容。失败 → {ok: False, error: ...}。

    返回:[{url, title, snippet, text}]
    """
    if not url or not url.strip():
        return {"ok": False, "error": "empty_url"}
    safe_n = min(max(int(num_results), 1), 100)
    payload = {
        "url": url.strip(),
        "numResults": safe_n,
        "excludeSourceDomain": bool(exclude_source_domain),
    }
    if text:
        payload["contents"] = {"text": {"maxCharacters": int(max_chars)}}

    r = _http_post("/findSimilar", payload, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "url": url, **r}
    data = r["data"]
    raw_results = data.get("results") or []
    results: list[dict[str, Any]] = []
    for it in raw_results:
        u = it.get("url", "")
        if not u:
            continue
        results.append({
            "url": u,
            "title": it.get("title", "") or "",
            "snippet": (it.get("text", "") or "")[:300],
            "text": it.get("text", "") or "",
        })
    return {"ok": True, "url": url, "results": results,
            "sources": ["exa_find_similar"] * len(results),
            "cost_dollars": data.get("costDollars", {}),
            "search_time_ms": data.get("searchTime", 0)}


# ---------------------------------------------------------------------------
# 公开 API 4: exa_answer(带引用的答案)
# ---------------------------------------------------------------------------

def exa_answer(query: str, *, text: bool = False,
               max_chars: int = 500,
               timeout: float = EXA_TIMEOUT) -> dict[str, Any]:
    """问答:返回 Exa 合成的答案 + 引用列表。失败 → {ok: False, ...}。

    返回:{ok, query, answer, citations: [{url, title}]}
    """
    if not query or not query.strip():
        return {"ok": False, "error": "empty_query"}
    payload = {
        "query": query.strip(),
    }
    if text:
        payload["contents"] = {"text": {"maxCharacters": int(max_chars)}}

    r = _http_post("/answer", payload, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "query": query, **r}
    data = r["data"]
    return {"ok": True, "query": query,
            "answer": data.get("answer", "") or
                      data.get("output", {}).get("content", "") or "",
            "citations": data.get("citations") or [],
            "cost_dollars": data.get("costDollars", {}),
            "search_time_ms": data.get("searchTime", 0)}