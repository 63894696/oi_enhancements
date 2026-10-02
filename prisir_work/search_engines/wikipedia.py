"""wikipedia.py — 维基媒体家族(免 key, MediaWiki Action API + REST)。

清单:
  - wikipedia           — en.wikipedia.org article search(API)
  - wikidata            — wikidata.org entity search
  - wikinews            — en.wikinews.org article search
  - wiktionary          — en.wiktionary.org entry search
  - wikicommons.images  — Wikimedia Commons 图片搜索
  - wikicommons.videos  — Wikimedia Commons 视频
  - wikicommons.audio   — Wikimedia Commons audio
  - wikicommons.files   — Wikimedia Commons 通用文件
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from prisir_work.search_engines._common import (
    _http_get, _json_get, _strip_html, _truncate,
)

# MediaWiki Action API: ?action=query&list=search&srsearch=...&format=json
_API = "https://en.wikipedia.org/w/api.php"


def _mw_search(host: str, query: str, limit: int) -> list[dict[str, Any]]:
    """MediaWiki action API list=search 通用封装。"""
    url = (f"{host}/w/api.php?" + urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query.strip(),
        "srlimit": str(max(1, min(limit, 20))),
        "format": "json",
        "srprop": "snippet|titlesnippet",
    }))
    try:
        data = _json_get(url, timeout=8.0)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in (data.get("query") or {}).get("search") or []:
        title = r.get("title") or ""
        snippet = _strip_html(r.get("snippet") or "")
        page_id = r.get("pageid") or ""
        if not title:
            continue
        url_v = f"{host}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        out.append({
            "url": url_v,
            "title": title,
            "snippet": _truncate(snippet, 300),
        })
    return out[:limit]


def wikipedia(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """英文维基百科 — article 标题 + snippet。"""
    if not query:
        return []
    return _mw_search("https://en.wikipedia.org", query, limit)


def wikinews(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """英文维基新闻。"""
    if not query:
        return []
    return _mw_search("https://en.wikinews.org", query, limit)


def wiktionary(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """英文维基词典。"""
    if not query:
        return []
    return _mw_search("https://en.wiktionary.org", query, limit)


def wikidata(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Wikidata — wbsearchentities。"""
    if not query:
        return []
    url = ("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({
        "action": "wbsearchentities",
        "search": query.strip(),
        "language": "en",
        "limit": str(max(1, min(limit, 20))),
        "format": "json",
    }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for it in data.get("search") or []:
            qid = it.get("id") or ""
            label = it.get("label") or ""
            desc = it.get("description") or ""
            if not label or not qid:
                continue
            out.append({
                "url": f"https://www.wikidata.org/wiki/{qid}",
                "title": label,
                "snippet": _truncate(desc, 300),
            })
        return out[:limit]
    except Exception:
        return []


def _commons_search(mime_type: str, query: str, limit: int) -> list[dict[str, Any]]:
    """Wikimedia Commons — generator=search + 过滤 mime。"""
    if not query:
        return []
    url = ("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query",
        "generator": "search",
        "gsrsearch": f"{query.strip()} filemime:{mime_type}",
        "gsrnamespace": "6",
        "gsrlimit": str(max(1, min(limit, 20))),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json",
    }))
    try:
        data = _json_get(url, timeout=8.0)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    pages = (data.get("query") or {}).get("pages") or {}
    for pid, page in pages.items():
        title = page.get("title") or ""
        info = (page.get("imageinfo") or [{}])[0]
        url_v = info.get("url") or ""
        ext = (info.get("extmetadata") or {})
        artist = (ext.get("Artist") or {}).get("value") or ""
        license_v = (ext.get("LicenseShortName") or {}).get("value") or ""
        if int(pid) < 0 or not title or not url_v:
            continue
        snippet = " · ".join(filter(None, [
            _strip_html(artist).strip(),
            license_v,
        ]))
        out.append({
            "url": url_v,
            "title": title.replace("File:", ""),
            "snippet": _truncate(snippet, 300),
        })
        if len(out) >= limit:
            break
    return out[:limit]


def wikicommons_images(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Wikimedia Commons 图片。"""
    return _commons_search("image", query, limit)


def wikicommons_videos(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Wikimedia Commons 视频(OGG / WebM)。"""
    return _commons_search("video", query, limit)


def wikicommons_audio(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Wikimedia Commons 音频(OGG / MP3)。"""
    return _commons_search("audio", query, limit)


def wikicommons_files(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Wikimedia Commons 通用文件(任意 mime)。"""
    return _commons_search("", query, limit)


def register_all() -> None:
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("wikipedia", wikipedia)
    _ws.register_provider("wikinews", wikinews)
    _ws.register_provider("wiktionary", wiktionary)
    _ws.register_provider("wikidata", wikidata)
    _ws.register_provider("wikicommons.images", wikicommons_images)
    _ws.register_provider("wikicommons.videos", wikicommons_videos)
    _ws.register_provider("wikicommons.audio", wikicommons_audio)
    _ws.register_provider("wikicommons.files", wikicommons_files)