"""images.py — 图片 / 摄影 搜索(免 key)。

清单:
  - 500px     — 500px 摄影社区(API 受限,HTML 兜底)
  - 1x        — 1x.com 高质图库(HTML)
  - deviantart — DeviantArt 搜索(HTML 兜底)
  - pexels    — Pexels 公开搜索(免 key 受限,但许可部分)
  - unsplash  — Unsplash Source / HTML 兜底
  - stocksnap — StockSnap 公开免版税图库(HTML)
  - picjumbo  — Picjumbo 公开图库(HTML)
  - wallhaven — Wallhaven Wallpaper 搜索(免 key JSON)
  - pinterest — Pinterest 公开搜索(免 key HTML 兜底)
  - findborg  — FindBorg / magento search 退路(返回 [])
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from prisir_work.search_engines._common import (
    _between, _http_get, _json_get, _strip_html, _truncate,
)


def _500px(query: str, limit: int) -> list[dict[str, Any]]:
    """500px — 公共搜索页 HTML。"""
    if not query:
        return []
    url = "https://500px.com/search?q=" + urllib.parse.quote(query.strip()) + "&type=photos"
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (500px)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('"photo":')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, '"url":"', '"')
        title = _between(chunk, '"title":"', '"')
        if not link:
            continue
        out.append({
            "url": "https://500px.com" + link if link.startswith("/") else link,
            "title": _strip_html(title).strip() or "500px photo",
            "snippet": "",
        })
    return out


def _1x(query: str, limit: int) -> list[dict[str, Any]]:
    """1x.com — 走公共搜索页 HTML。"""
    if not query:
        return []
    url = "https://1x.com/search?q=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (1x.com)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('class="thumbnail"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, 'alt="', '"'))
        if not link:
            continue
        out.append({
            "url": "https://1x.com" + link if link.startswith("/") else link,
            "title": title or "1x photo",
            "snippet": "",
        })
    return out


def deviantart(query: str, limit: int) -> list[dict[str, Any]]:
    """DeviantArt — 走公开搜索页 HTML。"""
    if not query:
        return []
    url = ("https://www.deviantart.com/search/artists?" + urllib.parse.urlencode({
        "q": query.strip(),
    }))
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (DeviantArt)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('"deviation":')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, '"url":"'[:0], '"')  # noop
        link = _between(chunk, 'https://www.deviantart.com/', '"')
        title = _between(chunk, '"title":"', '"')
        if not link:
            continue
        out.append({
            "url": "https://www.deviantart.com/" + link.split('"')[0],
            "title": _strip_html(title).strip() or "DeviantArt art",
            "snippet": "",
        })
    return out


def pexels(query: str, limit: int) -> list[dict[str, Any]]:
    """Pexels — 公开搜索页 HTML 兜底(API key 走付费品,免 key 解析 HTML)。"""
    if not query:
        return []
    url = "https://www.pexels.com/search/" + urllib.parse.quote(query.strip()) + "/"
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Pexels)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('"alt":"')[1:]:
        if len(out) >= limit:
            break
        title = _between(chunk, '"', '"')
        link = _between(chunk.split(title, 1)[0] if title else chunk, '"src":"', '"')
        if not link or "http" not in link:
            continue
        out.append({
            "url": link,
            "title": title.strip() or "Pexels image",
            "snippet": "",
        })
    return out


def unsplash(query: str, limit: int) -> list[dict[str, Any]]:
    """Unsplash Source 已 deprecated, 走 unsplash.com search HTML 兜底。"""
    if not query:
        return []
    url = "https://unsplash.com/s/photos/" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Unsplash)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('"alt":"')[1:]:
        if len(out) >= limit:
            break
        title = _between(chunk, '"', '"')
        # img src
        prev = chunk.split(title, 1)[0] if title else chunk
        link = _between(prev, '"src":"', '"')
        if not link or "http" not in link:
            continue
        out.append({
            "url": link,
            "title": title.strip() or "Unsplash photo",
            "snippet": "",
        })
    return out


def stocksnap(query: str, limit: int) -> list[dict[str, Any]]:
    """StockSnap — 公开搜索图库。"""
    if not query:
        return []
    url = "https://stocksnap.io/search/" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (StockSnap)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('"photo-snaps__photo"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, 'alt="', '"'))
        if not link:
            continue
        out.append({
            "url": "https://stocksnap.io" + link if link.startswith("/") else link,
            "title": title or "StockSnap photo",
            "snippet": "",
        })
    return out


def picjumbo(query: str, limit: int) -> list[dict[str, Any]]:
    """Picjumbo — 公开搜索图库(HTML)。"""
    if not query:
        return []
    url = "https://picjumbo.com/?s=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Picjumbo)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('"photo-item"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, 'alt="', '"'))
        if not link:
            continue
        out.append({
            "url": link if link.startswith("http") else "https://picjumbo.com" + link,
            "title": title or "Picjumbo photo",
            "snippet": "",
        })
    return out


def wallhaven(query: str, limit: int) -> list[dict[str, Any]]:
    """Wallhaven — Wallpaper search(免 key JSON 公开 API)。"""
    if not query:
        return []
    url = ("https://wallhaven.cc/api/v1/search?" + urllib.parse.urlencode({
        "q": query.strip(),
        "per_page": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Wallhaven)"})
        out: list[dict[str, Any]] = []
        for w in (data.get("data") or [])[:limit]:
            url_v = w.get("url") or ""
            img_id = w.get("id") or ""
            resolution = w.get("dimension_x", "?") and f"{w.get('dimension_x', '?')}x{w.get('dimension_y', '?')}"
            colors = (w.get("colors") or [])
            color_str = "".join([f"#{c[:6]}" for c in colors[:3]]) if colors else ""
            if not url_v and img_id:
                url_v = f"https://wallhaven.cc/w/{img_id}"
            if not url_v:
                continue
            out.append({
                "url": url_v,
                "title": f"Wallhaven {img_id} · {resolution}",
                "snippet": _truncate(color_str, 200),
            })
        return out
    except Exception:
        return []


def pinterest(query: str, limit: int) -> list[dict[str, Any]]:
    """Pinterest 公共搜索 HTML 兜底。

    Pinterest 无 key 搜索常需 JS 渲染,这里走 `/search/pins/?q=` 简化版。
    """
    if not query:
        return []
    url = ("https://www.pinterest.com/search/pins/?q="
           + urllib.parse.quote(query.strip()))
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Pinterest)"})
    except Exception:
        return []
    # Pinterest HTML 大量 JS,真实链接难提,直接返 []
    if '"pinLink"' not in html:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('"pinLink":"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, '"', '"').replace("\\/", "/")
        if not link:
            continue
        out.append({
            "url": link,
            "title": "Pinterest pin",
            "snippet": "",
        })
    return out


def findborg(query: str, limit: int) -> list[dict[str, Any]]:
    """FindBorg / Magen — search Borg-like sites;公开 API 不稳定,返 []。"""
    return []


def register_all() -> None:
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("500px", _500px)
    _ws.register_provider("1x", _1x)
    _ws.register_provider("deviantart", deviantart)
    _ws.register_provider("pexels", pexels)
    _ws.register_provider("unsplash", unsplash)
    _ws.register_provider("stocksnap", stocksnap)
    _ws.register_provider("picjumbo", picjumbo)
    _ws.register_provider("wallhaven", wallhaven)
    _ws.register_provider("pinterest", pinterest)
    _ws.register_provider("findborg", findborg)