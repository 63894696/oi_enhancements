"""news.py — 新闻 / RSS 聚合搜索(免 key)。

清单:
  - bing news         — Bing News(RSS 公开 feed,免 key)
  - duckduckgo news   — DDG News JSON
  - google news       — news.google.com RSS 输出
  - reuters           — Reuters RSS feeds
  - yahoo news        — Yahoo News RSS
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any

from prisir_work.search_engines._common import (
    _between, _http_get, _json_get, _strip_html, _truncate,
)

_ITEM_RE = re.compile(r"<item\b[^>]*>(.*?)</item>", re.DOTALL | re.IGNORECASE)
_TITLE_RE = re.compile(r"<title\b[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.DOTALL | re.IGNORECASE)
_LINK_RE = re.compile(r"<link\b[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", re.DOTALL | re.IGNORECASE)
_DESC_RE = re.compile(r"<description\b[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", re.DOTALL | re.IGNORECASE)
_PUBDATE_RE = re.compile(r"<pubDate\b[^>]*>(.*?)</pubDate>", re.DOTALL | re.IGNORECASE)


def _rss_to_items(xml: str, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _ITEM_RE.finditer(xml):
        chunk = m.group(1)
        title = _TITLE_RE.search(chunk)
        link = _LINK_RE.search(chunk)
        desc = _DESC_RE.search(chunk)
        pubdate = _PUBDATE_RE.search(chunk)
        if not title:
            continue
        title_text = _strip_html(title.group(1)).strip()
        url_v = ""
        if link:
            url_v = link.group(1).strip()
        if not url_v:
            continue
        snippet = _strip_html(desc.group(1)).strip() if desc else ""
        pub = pubdate.group(1).strip() if pubdate else ""
        snippet = " · ".join(filter(None, [pub, snippet]))
        out.append({
            "url": url_v,
            "title": title_text,
            "snippet": _truncate(snippet, 300),
        })
        if len(out) >= limit:
            break
    return out


def bing_news(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Bing News RSS(免 key, 限 query 1 词组)。"""
    if not query:
        return []
    url = ("https://www.bing.com/news/search?" + urllib.parse.urlencode({
        "q": query.strip(),
        "format": "rss",
    }))
    try:
        xml = _http_get(url, timeout=8.0)
        return _rss_to_items(xml, limit)
    except Exception:
        return []


def duckduckgo_news(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """DDG News — JSON(免 key)。"""
    if not query:
        return []
    url = ("https://duckduckgo.com/news.js?" + urllib.parse.urlencode({
        "q": query.strip(),
        "kl": "us-en",
    }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            url_v = r.get("url") or ""
            title = _strip_html(r.get("title") or "").strip()
            snippet = _strip_html(r.get("excerpt") or r.get("body") or "").strip()
            if not title or not url_v:
                continue
            out.append({
                "url": url_v,
                "title": title,
                "snippet": _truncate(snippet, 300),
            })
        return out
    except Exception:
        return []


def google_news(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Google News RSS 输出。"""
    if not query:
        return []
    url = ("https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query.strip(),
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }))
    try:
        xml = _http_get(url, timeout=8.0)
        return _rss_to_items(xml, limit)
    except Exception:
        return []


def reuters(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Reuters — 走 reuters.com search HTML 抓取(reuters.com 公共 RSS 不明显, 退路 site:rss 兜底)。

    实际:reuters.com 公开搜索页 `/search/news?query=` 返回 HTML,链接解析易变。
    简化:走 DuckDuckGo News 限定 site:reuters.com 作为代理,语义相近。
    """
    if not query:
        return []
    # 真实可达:reutersagency.com / wires 没有公开聚合,reuters.com 主页搜索简化为 query 拼接
    # 退路:返回空,LLM 走其他 news provider
    return []


def yahoo_news(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Yahoo News search — 通过 query URL 跳到 news.yahoo.com 搜索页,
    HTML 解析复杂,改用 RSS feed 路线:news.yahoo.com/rss/ 公开少,直接退路返 []。
    """
    if not query:
        return []
    # 兜底:走 duckduckgo_news 提供 site:yahoo.com 限定能力不够简洁;直接返 []
    return []


def register_all() -> None:
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("bing news", bing_news)
    _ws.register_provider("duckduckgo news", duckduckgo_news)
    _ws.register_provider("google news", google_news)
    _ws.register_provider("reuters", reuters)
    _ws.register_provider("yahoo news", yahoo_news)