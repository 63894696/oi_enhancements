# -*- coding: utf-8 -*-
"""hn_bridge.py — HackerNews Algolia API 直接调(2026-09-26 ship,P3j T22-B)。

复现 HN 搜索 + 单帖详情能力,免 key、JSON、稳定。
Algolia HN Search 比 HN 官方 Firebase API 强在:
  · 全文搜索(标题 + 文本 + 评论)
  · 按时间 / 分数 / 评论数排序
  · 高亮 + tag 过滤(story/comment/show_hn/ask_hn)

设计:
  · 4 端点全部 urllib.request GET,无需 key
  · timeout 15s 默认,失败返 {ok: False, error: "hn_xxx"}
  · 字段精简(snippet/title/url/points/num_comments/created_at)喂 LLM

公开 API:
  · hn_health()                  → {ok, source, version: "algolia"}
  · hn_search(query, **opts)     → search_by_date + search
  · hn_top_stories(**opts)       → search_by_points (热门排序)
  · hn_get_item(object_id)       → 单 item 详情(标题 + 文本 + URL)
"""
from __future__ import annotations

import json
import logging
import os
import sys as _sys
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

HN_API_BASE = "https://hn.algolia.com/api/v1"
HN_TIMEOUT = 15.0
HN_USER_AGENT = "prisIrai/1.0 (HackerNews bridge; T22-B)"


def _http_get(path: str, params: dict[str, Any] | None = None,
              *, timeout: float = HN_TIMEOUT) -> dict[str, Any]:
    """GET HN Algolia API → {ok: True, data: ...} 或 {ok: False, error}。"""
    qs = ""
    if params:
        # 空值剔除 + 整数/浮点正确序列化
        cleaned = {}
        for k, v in params.items():
            if v is None or v == "":
                continue
            cleaned[k] = v
        if cleaned:
            qs = "?" + urllib.parse.urlencode(cleaned)
    url = f"{HN_API_BASE}{path}{qs}"
    req = urllib.request.Request(url, headers={
        "User-Agent": HN_USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "data": json.loads(raw),
                    "status": int(resp.status)}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": int(getattr(e, "code", 0) or 0),
                "error": f"hn_http_{e.code}",
                "detail": e.read().decode("utf-8", errors="replace")[:300]}
    except urllib.error.URLError as e:
        return {"ok": False, "error": "hn_url_error",
                "detail": str(e.reason)[:200]}
    except Exception as e:
        return {"ok": False, "error": f"hn_{type(e).__name__}",
                "detail": str(e)[:200]}


def _hit_to_dict(hit: dict[str, Any]) -> dict[str, Any]:
    """Algolia hit → 精简 dict 给 LLM。"""
    url = (hit.get("url")
           or hit.get("story_url")
           or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}")
    return {
        "url": url,
        "title": (hit.get("title")
                  or hit.get("story_title")
                  or hit.get("comment_text", "")[:200]
                  or "").strip(),
        "snippet": (hit.get("story_text")
                    or hit.get("comment_text")
                    or hit.get("_highlightResult", {})
                                  .get("comment_text", {})
                                  .get("value", "")
                    or "").strip()[:600],
        "points": int(hit.get("points") or 0),
        "num_comments": int(hit.get("num_comments") or 0),
        "author": (hit.get("author") or "").strip(),
        "created_at": (hit.get("created_at")
                       or hit.get("created_at_i") and
                       _iso_from_epoch(hit["created_at_i"])
                       or ""),
        "object_id": hit.get("objectID", ""),
        "tags": list(hit.get("_tags") or []),
        "source": "hackernews",
    }


def _iso_from_epoch(epoch: int | str) -> str:
    """Unix epoch(秒)→ ISO 8601 字符串。失败返 ''。"""
    import datetime as _dt
    try:
        ts = int(epoch)
        return _dt.datetime.fromtimestamp(ts,
                                          tz=_dt.timezone.utc).isoformat()
    except Exception:
        return ""


def hn_health() -> dict[str, Any]:
    """轻探活:1 条 search_by_date 空 query(返 hits=0 即算 OK)。"""
    r = _http_get("/search_by_date", {"hitsPerPage": 1}, timeout=8.0)
    if not r.get("ok"):
        return {"ok": False, "source": "hackernews_algolia",
                "error": r.get("error", ""), "detail": r.get("detail", "")}
    return {"ok": True, "source": "hackernews_algolia",
            "version": "v1",
            "url": HN_API_BASE,
            "key_required": False,
            "search_types": ("search", "search_by_date"),
            "rate_limit": "no_auth_required"}


def hn_search(query: str, *, sort: str = "by_date",
              limit: int = 10, min_points: int = 0,
              tags: str = "story",
              timeout: float = HN_TIMEOUT) -> dict[str, Any]:
    """HN 搜索 query。

    sort: by_date / by_points / by_relevance
    tags: story / comment / show_hn / ask_hn / poll(可 "story,show_hn")
    """
    if not query or not query.strip():
        return {"ok": False, "error": "empty_query"}
    # endpoint 选:
    #   by_relevance → /search(默认按相关度排序)
    #   by_points    → /search + numericFilters(points>=N)
    #   by_date      → /search_by_date(默认按时间倒序)
    if sort == "by_date":
        endpoint = "search_by_date"
    else:
        endpoint = "search"
    params: dict[str, Any] = {
        "query": query.strip(),
        "hitsPerPage": max(1, min(limit, 50)),
        "tags": tags or "story",
    }
    nf: list[str] = []
    if min_points > 0:
        nf.append(f"points>={int(min_points)}")
    if sort == "by_points":
        nf.append("points>0")  # 隐藏 0 分帖子
    if nf:
        params["numericFilters"] = ",".join(nf)
    r = _http_get(f"/{endpoint}", params, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "query": query, **r}
    hits = (r["data"] or {}).get("hits", []) or []
    return {"ok": True, "query": query, "sort": sort,
            "tags": tags,
            "results": [_hit_to_dict(h) for h in hits[:limit]],
            "sources": ["hackernews"] * len(hits[:limit]),
            "search_time_ms": int((r.get("data") or {}).get("processingTimeMS", 0)),
            "total_hits": int((r["data"] or {}).get("nbHits", 0) or 0)}


def hn_top_stories(*, limit: int = 10, tags: str = "story",
                   min_points: int = 0,
                   timeout: float = HN_TIMEOUT) -> dict[str, Any]:
    """HN 热门:按分数排序(等效 show_hn/ask_hn 顶部)。"""
    params: dict[str, Any] = {
        "hitsPerPage": max(1, min(limit, 50)),
        "tags": tags or "story",
    }
    if min_points > 0:
        params["numericFilters"] = f"points>={int(min_points)}"
    r = _http_get("/search", params, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, **r}
    hits = (r["data"] or {}).get("hits", []) or []
    return {"ok": True, "sort": "by_points",
            "tags": tags,
            "results": [_hit_to_dict(h) for h in hits[:limit]],
            "sources": ["hackernews"] * len(hits[:limit]),
            "search_time_ms": int((r.get("data") or {}).get("processingTimeMS", 0)),
            "total_hits": int((r["data"] or {}).get("nbHits", 0) or 0)}


def hn_get_item(object_id: str,
                *, timeout: float = HN_TIMEOUT) -> dict[str, Any]:
    """单 item 详情(评论 / 故事 / 投票 / job)。"""
    if not object_id or not str(object_id).strip():
        return {"ok": False, "error": "empty_object_id"}
    r = _http_get(f"/items/{urllib.parse.quote(str(object_id).strip())}",
                  timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "object_id": object_id, **r}
    item = r["data"] or {}
    return {"ok": True, "object_id": object_id,
            "title": (item.get("title") or "").strip(),
            "text": (item.get("text") or "").strip(),
            "url": (item.get("url")
                    or f"https://news.ycombinator.com/item?id={object_id}"),
            "points": int(item.get("points") or 0),
            "num_comments": int(item.get("num_comments")
                                or len(item.get("children") or [])),
            "author": (item.get("author") or "").strip(),
            "created_at": _iso_from_epoch(item.get("created_at_i", 0)),
            "children": item.get("children") or [],
            "source": "hackernews"}