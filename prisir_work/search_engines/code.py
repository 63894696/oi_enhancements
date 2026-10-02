"""code.py — 代码 / 文档 / 软件包 搜索(免 key)。

清单:
  - github               — GitHub REST search/repos(匿名 RPS 60/小时)
  - docker               — Docker Hub search
  - pypi                 — PyPI Simple Index + JSON RPC search
  - npmjs                — npm registry search
  - stackoverflow        — StackExchange API search
  - askubuntu            — StackExchange askubuntu
  - superuser            — StackExchange superuser
  - mankier              — mankier.com(替代 mankier,代码片段聚合)
  - mdn                  — Mozilla Developer Network search
  - arch linux wiki      — Arch Wiki search
  - gentoo               — Gentoo Wiki search
  - nixos                — Nixos Wiki search
  - sourcehut            — sourcehut 邮件列表/项目搜索
  - hoogle               — Haskell Hoogle API
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from prisir_work.search_engines._common import (
    _between, _http_get, _json_get, _strip_html, _truncate,
)


def github(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """GitHub REST API search/repositories(匿名, RPS 60/小时)。"""
    if not query:
        return []
    url = ("https://api.github.com/search/repositories?"
           + urllib.parse.urlencode({
               "q": query.strip(),
               "per_page": str(max(1, min(limit, 20))),
           }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": "prisIrai/1.0 (GitHub Search)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("items") or [])[:limit]:
            full_name = r.get("full_name") or ""
            url_v = r.get("html_url") or ""
            desc = r.get("description") or ""
            stars = r.get("stargazers_count") or 0
            lang = r.get("language") or ""
            if not full_name or not url_v:
                continue
            out.append({
                "url": url_v,
                "title": f"{full_name} ({stars}⭐)",
                "snippet": _truncate(" · ".join(filter(None, [lang, desc])), 300),
            })
        return out
    except Exception:
        return []


def docker(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Docker Hub search。"""
    if not query:
        return []
    url = ("https://hub.docker.com/v2/search/repositories?"
           + urllib.parse.urlencode({
               "query": query.strip(),
               "page_size": str(max(1, min(limit, 20))),
           }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Docker Hub)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            repo = r.get("repo_name") or ""
            desc = r.get("short_description") or ""
            stars = r.get("star_count") or 0
            url_v = f"https://hub.docker.com/r/{repo}/"
            if not repo:
                continue
            out.append({
                "url": url_v,
                "title": f"{repo} ({stars}⭐)",
                "snippet": _truncate(desc, 300),
            })
        return out
    except Exception:
        return []


def pypi(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """PyPI Simple Search(POST XML-RPC 太复杂,改用官方 RPC JSON search)。"""
    if not query:
        return []
    # PyPI 已 ship 过一个 gh_search 走 gh CLI 路径;这里走 JSON RPC search
    body = '{"method":"search","params":{"q":"' + query.strip().replace('"', '\\"') + '","max_results":' + str(max(1, min(limit, 20))) + '},"jsonrpc":"2.0","id":1}'
    try:
        import json as _json
        req = urllib.request.Request(
            "https://pypi.org/pypi/-/search",
            data=body.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "prisIrai/1.0 (PyPI)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = _json.loads(raw)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in (data.get("result") or [])[:limit]:
        name = r.get("name") or ""
        version = r.get("version") or ""
        desc = r.get("summary") or ""
        if not name:
            continue
        out.append({
            "url": f"https://pypi.org/project/{name}/",
            "title": f"{name} {version}",
            "snippet": _truncate(desc, 300),
        })
    return out


def npmjs(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """npm registry search API。"""
    if not query:
        return []
    url = ("https://registry.npmjs.org/-/v1/search?" + urllib.parse.urlencode({
        "text": query.strip(),
        "size": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for r in (data.get("objects") or [])[:limit]:
            pkg = (r.get("package") or {})
            name = pkg.get("name") or ""
            desc = pkg.get("description") or ""
            url_v = pkg.get("links", {}).get("npm") or (f"https://www.npmjs.com/package/{name}" if name else "")
            if not name or not url_v:
                continue
            out.append({
                "url": url_v,
                "title": name,
                "snippet": _truncate(desc, 300),
            })
        return out
    except Exception:
        return []


def _se_search(host: str, query: str, limit: int) -> list[dict[str, Any]]:
    """StackExchange API 通用 search 封装。"""
    if not query:
        return []
    url = (f"https://api.stackexchange.com/2.3/search/advanced?"
           + urllib.parse.urlencode({
               "order": "desc",
               "sort": "relevance",
               "q": query.strip(),
               "site": host,
               "pagesize": str(max(1, min(limit, 20))),
           }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (StackExchange)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("items") or [])[:limit]:
            title = _strip_html(r.get("title") or "").strip()
            link = r.get("link") or ""
            snippet = _strip_html(r.get("excerpt") or "").strip()
            tags = r.get("tags") or []
            if not title or not link:
                continue
            if tags:
                snippet = ("[" + "][".join(tags[:3]) + "] " + snippet).strip()
            out.append({
                "url": link,
                "title": title,
                "snippet": _truncate(snippet, 300),
            })
        return out
    except Exception:
        return []


def stackoverflow(query: str, limit: int = 10) -> list[dict[str, Any]]:
    return _se_search("stackoverflow", query, limit)


def askubuntu(query: str, limit: int = 10) -> list[dict[str, Any]]:
    return _se_search("askubuntu", query, limit)


def superuser(query: str, limit: int = 10) -> list[dict[str, Any]]:
    return _se_search("superuser", query, limit)


def mdn(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """MDN — site search via duckduckgo HTML 兜底(Severite 风格的 site: 限定)。
    MDN 自身 search API 不稳定,走 google-programmable-search-engine free tier 不在。
    退路:用 DDG site: 检索但作为独立 provider 暴露,意义在于 LLM 在 use_case 时知道。
    """
    if not query:
        return []
    url = ("https://developer.mozilla.org/en-US/search.json?"
           + urllib.parse.urlencode({
               "q": query.strip(),
               "page": "1",
               "locale": "en-US",
           }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (MDN)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("documents") or [])[:limit]:
            title = r.get("title") or ""
            url_v = r.get("url") or ""
            snippet = _strip_html(r.get("excerpt") or "").strip()
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


def mankier(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """mankier.com — search engine / cheat sheet 搜索(免 key, JSON)。"""
    if not query:
        return []
    url = ("https://www.mankier.com/api/search?" + urllib.parse.urlencode({
        "q": query.strip(),
        "max": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for r in data.get("results") or []:
            title = r.get("name") or ""
            url_v = r.get("url") or ""
            desc = r.get("desc") or ""
            if not title or not url_v:
                continue
            out.append({
                "url": url_v,
                "title": title,
                "snippet": _truncate(desc, 300),
            })
        return out[:limit]
    except Exception:
        return []


def arch_linux_wiki(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Arch Wiki — MediaWiki API 同 wikipedia.py 套路。"""
    if not query:
        return []
    url = ("https://wiki.archlinux.org/w/api.php?" + urllib.parse.urlencode({
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
        if not title:
            continue
        out.append({
            "url": f"https://wiki.archlinux.org/title/{urllib.parse.quote(title.replace(' ', '_'))}",
            "title": title,
            "snippet": _truncate(snippet, 300),
        })
        if len(out) >= limit:
            break
    return out


def gentoo(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Gentoo Wiki — MediaWiki API。"""
    if not query:
        return []
    url = ("https://wiki.gentoo.org/w/api.php?" + urllib.parse.urlencode({
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
        if not title:
            continue
        out.append({
            "url": f"https://wiki.gentoo.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            "title": title,
            "snippet": _truncate(snippet, 300),
        })
        if len(out) >= limit:
            break
    return out


def nixos(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """NixOS Wiki — MediaWiki API。"""
    if not query:
        return []
    url = ("https://wiki.nixos.org/w/api.php?" + urllib.parse.urlencode({
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
        if not title:
            continue
        out.append({
            "url": f"https://wiki.nixos.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            "title": title,
            "snippet": _truncate(snippet, 300),
        })
        if len(out) >= limit:
            break
    return out


def sourcehut(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """sourcehut — pagure-based 仓库 + lists 搜索。

    sourcehut 没有公开聚合 search API,只走项目 page:@host: list-search。
    退路:用 meta.sr.ht GraphQL(需要 token,跳过)。
    实际:返回空,LLM 走其他 code provider。
    """
    if not query:
        return []
    # 简洁实现 — 走 lists.sr.ht 邮件列表搜索页面但 HTML 解析复杂,直接返空兜住。
    return []


def hoogle(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Haskell Hoogle — function signature 搜索(免 key)。"""
    if not query:
        return []
    url = ("https://hoogle.haskell.org/api?hoogle=" + urllib.parse.quote(query.strip())
           + "&mode=json&count=" + str(max(1, min(limit, 20))))
    try:
        data = _json_get(url, timeout=8.0)
        out: list[dict[str, Any]] = []
        for r in data.get("results") or [][:limit]:
            if isinstance(r, dict):
                item = r.get("item") or {}
                name = item.get("name") or ""
                url_v = item.get("url") or ""
                docs = r.get("docs") or ""
                if not name:
                    continue
                out.append({
                    "url": url_v or f"https://hoogle.haskell.org/?hoogle={urllib.parse.quote(query)}",
                    "title": name,
                    "snippet": _truncate(docs, 300),
                })
        return out
    except Exception:
        return []


def register_all() -> None:
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("github", github)
    _ws.register_provider("docker", docker)
    _ws.register_provider("pypi", pypi)
    _ws.register_provider("npmjs", npmjs)
    _ws.register_provider("stackoverflow", stackoverflow)
    _ws.register_provider("askubuntu", askubuntu)
    _ws.register_provider("superuser", superuser)
    _ws.register_provider("mankier", mankier)
    _ws.register_provider("mdn", mdn)
    _ws.register_provider("arch linux wiki", arch_linux_wiki)
    _ws.register_provider("gentoo", gentoo)
    _ws.register_provider("nixos", nixos)
    _ws.register_provider("sourcehut", sourcehut)
    _ws.register_provider("hoogle", hoogle)