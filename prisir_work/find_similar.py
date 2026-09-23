# -*- coding: utf-8 -*-
"""相似 URL 发现 v0.1.0 (P2.5+16f, 2026-09-23)

输入 URL,基于:
  1. 参考页内容(title/h1/h2/meta description)提取关键词
  2. web_search.search(keyword) 多 provider RRF 取一批候选
  3. (可选) Serper "related" 端点 /google.serper.dev/related

返回相似 URL 列表(去重 + 同 host 不过 3 + score 排序)。

任何源失败/超时/raise 全部吞,warnings 透出。
零依赖(Serper 走 urllib.request stdlib)。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _step(name: str, **extra) -> dict:
    out = {"step": name, "ok": True, "duration_ms": 0}
    out.update(extra)
    return out


def _normalize_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_H_RE = re.compile(r"<h[1-6][^>]*>([^<]+)</h[1-6]>", re.IGNORECASE)
_META_DESC_RE = re.compile(
    r'<meta\s+name=["\']description["\']?\s+content=["\']?([^"\'>]+)["\']?',
    re.IGNORECASE
)


def _strip_html(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()


def _extract_keywords(html: str, max_kw: int = 5) -> list[str]:
    """从 title/h1/h2/meta description 抽核心关键词(去停用词)。"""
    text_parts = []
    t = _TITLE_RE.search(html)
    if t:
        text_parts.append(t.group(1))
    for h in _H_RE.findall(html)[:5]:
        text_parts.append(h)
    md = _META_DESC_RE.search(html)
    if md:
        text_parts.append(md.group(1))
    text = " ".join(text_parts).lower()
    # 简单分词:中文按字 / 英文按词
    tokens = []
    # 英文词
    tokens.extend(re.findall(r"[a-z]{3,}", text))
    # 中文短语(2-4 字)
    tokens.extend(re.findall(r"[一-鿿]{2,4}", text))
    # 去停用词(简短常见词)
    stop = {"the", "and", "for", "with", "this", "that", "from", "are", "you", "your",
            "的", "了", "是", "在", "和", "与", "或", "我", "你", "他", "她", "它"}
    counter = Counter(t for t in tokens if t not in stop)
    return [w for w, _ in counter.most_common(max_kw)]


def _serper_related(url: str, api_key: str, timeout: float) -> list[dict]:
    """POST https://google.serper.dev/related,带 key 走 env。"""
    try:
        req = urllib.request.Request(
            "https://google.serper.dev/related",
            data=json.dumps({"url": url}).encode(),
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        out = []
        for item in (data.get("related") or data.get("results") or [])[:10]:
            u = item.get("url") or item.get("link")
            if not u:
                continue
            out.append({"url": u, "title": item.get("title", ""),
                        "snippet": item.get("snippet", ""), "source": "serper"})
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("serper related failed: %s", e)
        return []


def find_similar(url: str, *, max_results: int = 10, timeout: float = 10.0,
                 serper_key: str | None = None,
                 providers: list[str] | None = None) -> dict:
    """基于 URL 找相似 URL。

    Args:
        url: 输入 URL
        max_results: 最多返几条相似
        timeout: 单次 HTTP 超时秒数
        serper_key: Serper API key;None 时读 env SERPER_API_KEY
        providers: web_search 用的 provider 列表(None = 全部已注册)

    Returns:
    {
        'ok': True,
        'input_url': str,
        'keywords': [str, ...],
        'similar': [{'url', 'title', 'snippet', 'source', 'score': float}, ...],
        'warnings': list[str],
        'steps': [...]
    }
    """
    warnings: list[str] = []
    steps: list[dict] = []
    candidates: dict[str, dict] = {}  # url → entry(累加 source + score)

    if not url or not isinstance(url, str) or not url.strip():
        return {"ok": False, "input_url": url, "keywords": [], "similar": [],
                "warnings": ["empty_url"], "steps": []}

    input_host = _normalize_host(url)

    # ── 1. 抓参考页 ──
    t0 = _now_ms()
    content = ""
    try:
        from prisir_work import web_fetch as _wf
        r = _wf.fetch(url, options={"timeout": timeout})
        if r.get("ok") and r.get("content"):
            content = r["content"]
        else:
            warnings.append("fetch_failed")
    except Exception as e:  # noqa: BLE001
        warnings.append(f"fetch_error:{type(e).__name__}")
    steps.append(_step("fetch", content_chars=len(content), duration_ms=_now_ms() - t0))

    # ── 2. 提取关键词 ──
    t0 = _now_ms()
    keywords = _extract_keywords(content) if content else []
    if not keywords:
        # fallback:用 url path 最后一段
        try:
            path = urlparse(url).path
            last = re.split(r"[/\-_.]", path)[-1] if path else ""
            if last and len(last) >= 3:
                keywords = [last.lower()]
        except Exception:
            pass
    if not keywords:
        warnings.append("no_keywords")
    steps.append(_step("keywords", count=len(keywords), duration_ms=_now_ms() - t0))

    # ── 3. web_search 找候选 ──
    if keywords:
        t0 = _now_ms()
        try:
            from prisir_work import web_search as _ws
            kw_query = " ".join(keywords[:3])
            search_results = _ws.search(kw_query, limit=max(10, max_results),
                                        providers=providers, timeout=min(8.0, timeout))
            for r in search_results:
                u = r.get("url")
                if not u or _normalize_host(u) == input_host:
                    continue
                cur = candidates.get(u, {"url": u, "title": "", "snippet": "",
                                         "source": set(), "score": 0.0})
                cur["title"] = cur["title"] or r.get("title", "")
                cur["snippet"] = cur["snippet"] or r.get("snippet", "")
                cur["source"].add("web_search")
                cur["score"] += r.get("score", 0) or 0.1
                candidates[u] = cur
        except Exception as e:  # noqa: BLE001
            warnings.append("search_failed")
            log.warning("web_search failed in find_similar: %s", e)
        steps.append(_step("web_search", found=len(candidates), duration_ms=_now_ms() - t0))

    # ── 4. Serper "related"(可选) ──
    t0 = _now_ms()
    key = serper_key or os.environ.get("SERPER_API_KEY", "").strip()
    if key:
        serper_hits = _serper_related(url, key, timeout=min(8.0, timeout))
        for r in serper_hits:
            u = r.get("url")
            if not u or _normalize_host(u) == input_host:
                continue
            cur = candidates.get(u, {"url": u, "title": "", "snippet": "",
                                     "source": set(), "score": 0.0})
            cur["title"] = cur["title"] or r.get("title", "")
            cur["snippet"] = cur["snippet"] or r.get("snippet", "")
            cur["source"].add("serper")
            cur["score"] += 0.05  # serper 加分
            candidates[u] = cur
        steps.append(_step("serper", count=len(serper_hits), duration_ms=_now_ms() - t0))
    else:
        steps.append(_step("serper", skipped="no_key", duration_ms=_now_ms() - t0))
        warnings.append("serper_no_key")

    # ── 5. 同 host 限制(≤ 3) + 排序 ──
    t0 = _now_ms()
    host_count: dict[str, int] = {}
    final = []
    sorted_cands = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
    for c in sorted_cands:
        h = _normalize_host(c["url"])
        if host_count.get(h, 0) >= 3:
            continue
        host_count[h] = host_count.get(h, 0) + 1
        c["source"] = sorted(c["source"])
        final.append({
            "url": c["url"], "title": c["title"], "snippet": c["snippet"],
            "source": ",".join(c["source"]), "score": round(c["score"], 4),
        })
        if len(final) >= max_results:
            break
    steps.append(_step("rank", kept=len(final), duration_ms=_now_ms() - t0))

    return {
        "ok": True,
        "input_url": url,
        "keywords": keywords,
        "similar": final,
        "warnings": warnings,
        "steps": steps,
    }


if __name__ == "__main__":
    import sys as _sys
    _url = _sys.argv[1] if len(_sys.argv) > 1 else ""
    print(json.dumps(find_similar(_url), ensure_ascii=False, indent=2))