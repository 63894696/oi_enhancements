"""general.py — 通用网页搜索(无 key, SearXNG 启用无 key 段)。

清单(已 ship 内的 ddg / baidu / bing 不计):
  - mojeek      — Mojeek(英国隐私搜索)
  - startpage   — Startpage(隐私 meta-search)
  - dogpile     — Dogpile meta-search(老牌)
  - elasticsearch — elasticsearch 公共 search service

duckduckgo_test / ecosia / qwant SearXNG 中实测为 requires_api_key(100) 或 disabled,
不在本计划范围。
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from prisir_work.search_engines._common import (
    _between, _http_get, _json_get, _strip_html, _truncate,
)


def mojeek(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Mojeek 搜索(英国, 隐私, 免 key)。"""
    if not query:
        return []
    url = ("https://www.mojeek.com/search?" + urllib.parse.urlencode({
        "q": query.strip(),
        "fmt": "json",
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Mojeek)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            url_v = r.get("url") or ""
            title = _strip_html(r.get("title") or "").strip()
            desc = r.get("desc") or r.get("description") or ""
            if not url_v or not title:
                continue
            out.append({
                "url": url_v,
                "title": title,
                "snippet": _truncate(_strip_html(desc), 300),
            })
        return out
    except Exception:
        return []


def startpage(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Startpage — 走匿名搜索 HTML 解析。

    Startpage 没公开 API,只能抓搜索页 HTML,用 ?<div class="w-gl__result"> 分块。
    """
    if not query:
        return []
    url = "https://www.startpage.com/sp/search?" + urllib.parse.urlencode({
        "query": query.strip(),
        "cat": "web",
    })
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Startpage)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    # Startpage HTML 结构变化频繁,粗解析
    for chunk in html.split('class="w-gl__result"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, '<h3', '</h3>'))
        if not link or not title:
            continue
        out.append({
            "url": link,
            "title": title,
            "snippet": "",
        })
    return out


def dogpile(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Dogpile — 经典 meta-search(免 key, HTML)。

    实现:走 dogpile.com search 页面抓 result-block 简化版。
    """
    if not query:
        return []
    url = "https://www.dogpile.com/serp?q=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Dogpile)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('<a class="result-link"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        # 紧跟标题近似:取 link 内 text
        title = _strip_html(_between(chunk, '>', '</a>'))
        if not link or not title:
            continue
        out.append({
            "url": link,
            "title": title,
            "snippet": "",
        })
    return out


def elasticsearch(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """SearXNG elasticsearch 是内部 backend 不是 search engine;在 searxng 配置中是
    engine: elasticsearch 走 ES REST。公开 ES 搜索聚合为 elasticsearch.co/community
    等托管实例,实际无可信免 key 端点。返 []。
    """
    return []


def register_all() -> None:
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("mojeek", mojeek)
    _ws.register_provider("startpage", startpage)
    _ws.register_provider("dogpile", dogpile)
    _ws.register_provider("elasticsearch", elasticsearch)