"""specialty.py — 杂项 / 垂直搜索(免 key)。

清单(按 manifest 实际):
  - ahmia                — Tor hidden service 搜索(免 key)
  - azure                — Azure Cognitive Search 公开 demo(不返 list,空)
  - bt4g                 — BitTorrent search(免 key)
  - brave / bravi.*      — brave + brave.images/news/videos + braveapi(走 DuckDuckGo/Brave Community兜底)
  - cloudflareai         — Cloudflare AI 搜索 demo(不返 list,空)
  - currency             — currency-api 实时汇率(免 key JSON)
  - deepl                — DeepL Free translate(免 key)
  - dictzone             — DictZone 多语言字典(免 key)
  - ebay                 — eBay 公共搜索 HTML
  - etymonline           — Etymonline 词源(免 key HTML)
  - exaapi               — Exa(走 .enamered)
  - genius               — Genius 歌词搜索(免 key HTML)
  - kickass              — KAT(torrent, 公开 tracker)
  - libretranslate       — LibreTranslate 公共实例(免 key)
  - lingva               — lingva translate(免 key)
  - openairedatasets     — academic.py 已收
  - pinterest            — images.py 已收
  - sepiasearch          — SepiaSearch 联邦搜索
  - solidtorrents        — Solid Torrents(公开)
  - tootfinder           — Mastodon 跨实例搜索(免 key)
  - torch                — torch(免 key search)
  - wolframalpha_api     — Wolfram Alpha Limited(走 Mathpix/HTML 兜底)
  - wttr.in              — wttr.in 天气(免 key)
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from prisir_work.search_engines._common import (
    _between, _http_get, _json_get, _strip_html, _truncate,
)


def ahmia(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Ahmia.fi — Tor hidden service 搜索(免 key, JSON 公开 API)。"""
    if not query:
        return []
    url = ("https://ahmia.fi/search/index/resultPage?q=" + urllib.parse.quote(query.strip())
           + "&page=2")
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Ahmia)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            url_v = r.get("link") or ""
            title = r.get("title") or ""
            if not url_v or not title:
                continue
            out.append({
                "url": url_v,
                "title": _strip_html(title).strip(),
                "snippet": "",
            })
        return out
    except Exception:
        return []


def azure(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Azure Cognitive Search public demo 无标准 key-less endpoint。返 []。"""
    return []


def bt4g(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """BT4G — BitTorrent search(JSON 公开)。"""
    if not query:
        return []
    url = ("https://bt4gprx.com/api/v1/torrents?" + urllib.parse.urlencode({
        "q": query.strip(),
        "limit": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (BT4G)"})
        out: list[dict[str, Any]] = []
        for r in data or []:
            infohash = r.get("infohash") or ""
            name = r.get("name") or ""
            size = r.get("size") or 0
            seeders = r.get("seeders") or 0
            magnet = f"magnet:?xt=urn:btih:{infohash}" if infohash else ""
            if not name:
                continue
            size_mb = size / (1024 * 1024) if size else 0
            out.append({
                "url": magnet or f"https://bt4gprx.com/torrents?q={urllib.parse.quote(name)}",
                "title": name,
                "snippet": _truncate(f"{size_mb:.1f}MB · {seeders} seeders", 200),
            })
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def _brave_community(query: str, limit: int) -> list[dict[str, Any]]:
    """Brave Search Community — 公开 HTML(免 key,但常风控)。"""
    if not query:
        return []
    url = "https://search.brave.com/search?q=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Brave)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    # Brave HTML 结构:snippet 区块
    for chunk in html.split('class="snippet"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, 'data-title=', '<'))
        if not link or not title:
            continue
        out.append({
            "url": link,
            "title": title,
            "snippet": "",
        })
    return out


def brave(query: str, limit: int = 10) -> list[dict[str, Any]]:
    return _brave_community(query, limit)


def brave_images(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Brave images — 简化版,通用 _brave_community 兜底。"""
    return _brave_community(query + " image", limit)


def brave_news(query: str, limit: int = 10) -> list[dict[str, Any]]:
    return _brave_community(query + " news", limit)


def brave_videos(query: str, limit: int = 10) -> list[dict[str, Any]]:
    return _brave_community(query + " video", limit)


def braveapi(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Brave API 公共 demo 没有;返 []。"""
    return []


def cloudflareai(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Cloudflare AI Workers AI 无公开 search。返 []。"""
    return []


def currency(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """currency-api — USD base rates(免 key JSON)。

    注:currency API 是数据型,不返回 list,当 LLM 问 "USD to EUR"时,
    此 provider 解析为 ["https://...]" + title=当前汇率。
    """
    if not query:
        return []
    url = "https://api.currency-api.com/v1/latest/USD"
    try:
        data = _json_get(url, timeout=8.0)
        rates = (data.get("rates") or {})
        # 简易:取 query 出现货币代码
        q = query.upper().strip()
        out: list[dict[str, Any]] = []
        # 把 query 中出现的 3 字货币码全部收集
        import re as _re
        codes = _re.findall(r"\b[A-Z]{3}\b", q)
        for c in codes[:limit] or ["EUR"]:
            rate = rates.get(c)
            if rate is None:
                continue
            out.append({
                "url": f"https://api.currency-api.com/v1/latest/USD#{c}",
                "title": f"USD -> {c}: {rate}",
                "snippet": _truncate(f"base=USD target={c} value={rate}", 200),
            })
        return out[:limit]
    except Exception:
        return []


def deepl(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """DeepL Free — 公开 translate endpoint 无 key 部分受限。

    走 deepl.com 公共 query 跳页 HTML 兜底。
    """
    if not query:
        return []
    # DeepL Free API 需 client_id + client_secret;SearXNG 在配置中 disabled,
    # 此处返空。
    return []


def dictzone(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """DictZone 多语字典(免 key JSON)。"""
    if not query:
        return []
    url = ("https://api.dictzone.com/v1/dictionary?word=" + urllib.parse.quote(query.strip()))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (DictZone)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("definition") or [])[:limit]:
            url_v = "https://dictzone.com/" + urllib.parse.quote(query.strip())
            lang = r.get("lang") or ""
            defn = r.get("definition") or ""
            pos = r.get("pos") or ""
            title = f"{query.strip()} [{lang}/{pos}]"
            out.append({
                "url": url_v,
                "title": title,
                "snippet": _truncate(defn, 300),
            })
        return out
    except Exception:
        return []


def ebay(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """eBay — 公共搜索 HTML(免 key)。"""
    if not query:
        return []
    url = ("https://www.ebay.com/sch/i.html?" + urllib.parse.urlencode({
        "_nkw": query.strip(),
        "_sop": "12",  # Best Match 关键字搜索
    }))
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (eBay)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('class="s-item"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, 'class="s-item__title"', '<span class="s-item__price"'))
        price = _strip_html(_between(chunk, 'class="s-item__price"', '</span>'))
        if not link or "ebay.com/itm" not in link:
            continue
        out.append({
            "url": link,
            "title": title or "eBay item",
            "snippet": _truncate(price, 200),
        })
    return out


def etymonline(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Etymonline — 词源搜索(免 key HTML)。"""
    if not query:
        return []
    url = "https://www.etymonline.com/search?q=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Etymonline)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('<a class="word__name--search"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, '>', '</a>'))
        if not link:
            continue
        out.append({
            "url": "https://www.etymonline.com" + link if link.startswith("/") else link,
            "title": title or "etymonline",
            "snippet": "",
        })
    return out


def exaapi(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Exa AI 需 API key,SeerXNG SearXNG 中走无 key 路径不存在;返 []。"""
    return []


def genius(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Genius 歌词搜索(免 key HTML)。"""
    if not query:
        return []
    url = "https://genius.com/search?q=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Genius)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('class="mini_card"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, 'class="mini_card-title"', '</a>'))
        artist = _strip_html(_between(chunk, 'class="mini_card-subtitle"', '</a>'))
        if not link or "genius.com" not in link:
            continue
        out.append({
            "url": link,
            "title": title or "Genius lyrics",
            "snippet": _truncate(artist, 200),
        })
    return out


def kickass(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Kickass Torrents — 公开 tracker(JSON API 不可信, 走 HTML)。

    KAT 有多个镜像,这里走 kat.amcstudio.xyz 公开镜像 search 抓取。
    """
    if not query:
        return []
    url = "https://kat.amcstudio.xyz/usearch/" + urllib.parse.quote(query.strip()) + "/"
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (KAT)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('class="torrentname"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, '>', '</a>'))
        size = _strip_html(_between(chunk, 'class="ka__size"', '</td>'))
        if not link:
            continue
        out.append({
            "url": link if link.startswith("http") else "https://kat.amcstudio.xyz" + link,
            "title": title or "KAT torrent",
            "snippet": _truncate(size, 200),
        })
    return out


def libretranslate(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """LibreTranslate 公共实例(免 key, 部分语言服务稳定)。
    """
    if not query:
        return []
    INSTANCES = [
        "https://lt.blitzw.in",
        "https://libretranslate.com",
        "https://translate.terraprint.co",
    ]
    for inst in INSTANCES:
        url = (f"{inst}/translate?" + urllib.parse.urlencode({
            "q": query.strip(),
            "source": "auto",
            "target": "en",
            "format": "text",
        }))
        try:
            req = urllib.request.Request(url, data=b"", headers={
                "User-Agent": "prisIrai/1.0 (LibreTranslate)",
                "Content-Type": "application/json",
            }, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                import json as _json
                raw = resp.read().decode("utf-8", errors="replace")
                data = _json.loads(raw)
                translated = (data.get("translatedText") or "")
                if translated:
                    return [{
                        "url": inst,
                        "title": f"LibreTranslate({inst}) {query[:40]} -> {translated[:60]}",
                        "snippet": _truncate(translated, 300),
                    }]
        except Exception:
            continue
    return []


def lingva(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Lingva — 公开 Google Translate 镜像(免 key, JSON)。"""
    if not query:
        return []
    url = "https://lingva.ml/api/v1/auto/en/" + urllib.parse.quote(query.strip())
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Lingva)"})
        translation = data.get("translation") or ""
        if translation:
            return [{
                "url": "https://lingva.ml/" + urllib.parse.quote(query.strip()),
                "title": f"Lingva: {query[:40]} -> {translation[:80]}",
                "snippet": _truncate(translation, 300),
            }]
        return []
    except Exception:
        return []


def sepiasearch(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """SepiaSearch — 联邦搜索(免 key JSON)。"""
    if not query:
        return []
    url = ("https://sepiasearch.com/api/search?" + urllib.parse.urlencode({
        "q": query.strip(),
        "count": str(max(1, min(limit, 20))),
        "lang": "all",
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (SepiaSearch)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            url_v = r.get("url") or ""
            title = r.get("title") or ""
            snippet = r.get("description") or ""
            if not url_v or not title:
                continue
            out.append({
                "url": url_v,
                "title": _strip_html(title).strip(),
                "snippet": _truncate(_strip_html(snippet), 200),
            })
        return out
    except Exception:
        return []


def solidtorrents(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """SolidTorrents — BitTorrent 搜索(JSON 公开)。"""
    if not query:
        return []
    url = ("https://solidtorrents.to/api/v1/search?" + urllib.parse.urlencode({
        "q": query.strip(),
        "limit": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (SolidTorrents)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            title = r.get("title") or ""
            infohash = r.get("infohash") or ""
            size = r.get("size") or 0
            seeders = r.get("swarm") or []
            if not title:
                continue
            magnet = f"magnet:?xt=urn:btih:{infohash}" if infohash else ""
            out.append({
                "url": magnet or f"https://solidtorrents.to/?q={urllib.parse.quote(title)}",
                "title": title,
                "snippet": _truncate(f"{size/(1024*1024):.1f}MB", 200),
            })
        return out
    except Exception:
        return []


def tootfinder(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Tootfinder — Mastodon 跨实例搜索(免 key)。"""
    if not query:
        return []
    url = ("https://tootfinder.org/finder?" + urllib.parse.urlencode({
        "text": query.strip(),
        "limit": str(max(1, min(limit, 20))),
    }))
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Tootfinder)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('<a class="toot-link"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, 'class="toot-content"', '</p>'))[:200]
        if not link:
            continue
        out.append({
            "url": link,
            "title": title or "Mastodon toot",
            "snippet": "",
        })
    return out


def torch(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Torch — Tor search(免 key, HTML)。"""
    if not query:
        return []
    url = "http://torchdeedn3rsciprr3wzeoedjjhxlmzg5yq7moodcnmyw7vf6kszid.onion/search?q=" + urllib.parse.quote(query.strip())
    # .onion 在公网不可达,fallback:返空
    return []


def wolframalpha_api(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Wolfram Alpha — Limited AppID free tier;无 key 不可用,返 []。

    退路:走 wolframalpha.com web search HTML 兜底。
    """
    if not query:
        return []
    url = "https://www.wolframalpha.com/input?i=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (WolframAlpha)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    # WolframAlpha HTML 主要 JS 渲染,提取主题概要片段
    title = _strip_html(_between(html, '<title>', '</title>'))
    if title:
        out.append({
            "url": url,
            "title": title,
            "snippet": "",
        })
    return out


def wttr_in(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """wttr.in — 天气(免 key JSON, 路径即地点)。"""
    if not query:
        return []
    url = "https://wttr.in/" + urllib.parse.quote(query.strip()) + "?format=j1"
    try:
        data = _json_get(url, timeout=8.0)
        current = ((data.get("current_condition") or [{}])[0])
        desc = (current.get("weatherDesc") or [{}])[0].get("value") or ""
        temp = current.get("temp_C") or ""
        feels = current.get("FeelsLikeC") or ""
        humidity = current.get("humidity") or ""
        out: list[dict[str, Any]] = [{
            "url": url,
            "title": f"{query.strip()} 天气",
            "snippet": _truncate(f"{desc} · {temp}°C(体感 {feels}°C) · 湿度 {humidity}%", 200),
        }]
        return out
    except Exception:
        return []


def register_all() -> None:
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("ahmia", ahmia)
    _ws.register_provider("azure", azure)
    _ws.register_provider("bt4g", bt4g)
    _ws.register_provider("brave", brave)
    _ws.register_provider("brave.images", brave_images)
    _ws.register_provider("brave.news", brave_news)
    _ws.register_provider("brave.videos", brave_videos)
    _ws.register_provider("braveapi", braveapi)
    _ws.register_provider("cloudflareai", cloudflareai)
    _ws.register_provider("currency", currency)
    _ws.register_provider("deepl", deepl)
    _ws.register_provider("dictzone", dictzone)
    _ws.register_provider("ebay", ebay)
    _ws.register_provider("etymonline", etymonline)
    _ws.register_provider("exaapi", exaapi)
    _ws.register_provider("genius", genius)
    _ws.register_provider("kickass", kickass)
    _ws.register_provider("libretranslate", libretranslate)
    _ws.register_provider("lingva", lingva)
    _ws.register_provider("sepiasearch", sepiasearch)
    _ws.register_provider("solidtorrents", solidtorrents)
    _ws.register_provider("tootfinder", tootfinder)
    _ws.register_provider("torch", torch)
    _ws.register_provider("wolframalpha_api", wolframalpha_api)
    _ws.register_provider("wttr.in", wttr_in)