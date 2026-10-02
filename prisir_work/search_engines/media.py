"""media.py — 视频 / 音频 搜索(免 key)。

清单:
  - youtube          — Invidious 公开实例 search(免 key)
  - youtube_api      — youtube_api 走 invidious 兜底
  - vimeo            — Vimeo 公共搜索 HTML
  - piped            — Piped API(免 key, Invidious-style)
  - piped.music      — Piped Music 实例
  - bandcamp         — Bandcamp 公开搜索 JSON
  - mixcloud         — Mixcloud 公开搜索 HTML
  - dailymotion      — Dailymotion 公开搜索 API(免 key)
  - soundcloud       — SoundCloud 公开 search(免 key)
  - openverse.audio  — Openverse(原 CC Search)音频 API
  - freesound        — Freesound 搜索 HTML 兜底
  - deepl            — 退路,deepl 不在 media 类,挪到 specialty
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from prisir_work.search_engines._common import (
    _between, _http_get, _json_get, _strip_html, _truncate,
)

# Invidious 公开实例列表 — 走轮询避免单点失败
# 实际写时 1 个不够稳,简单做 fallback list
# 注:loop_provider 可以 ban 字典里降权
_INVIDIOUS_INSTANCES = [
    "https://invidious.fdn.fr",
    "https://invidious.protokolla.fi",
    "https://yewtu.be",
    "https://inv.tux.pizza",
]


def youtube(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """YouTube via Invidious 公开实例(免 key)。

    走 instance list,失败切下一个。
    """
    if not query:
        return []
    for inst in _INVIDIOUS_INSTANCES:
        url = (f"{inst}/api/v1/search?" + urllib.parse.urlencode({
            "q": query.strip(),
            "type": "video",
        }))
        try:
            data = _json_get(url, timeout=5.0,
                             headers={"User-Agent": f"prisIrai/1.0 (Invidious {inst})"})
            out: list[dict[str, Any]] = []
            for r in (data or [])[:limit]:
                if not isinstance(r, dict):
                    continue
                title = r.get("title") or ""
                url_v = r.get("videoId") or ""
                if not title or not url_v:
                    continue
                out.append({
                    "url": f"{inst}/watch?v={url_v}",
                    "title": title,
                    "snippet": _truncate(" · ".join(filter(None, [
                        r.get("author", ""),
                        f"{r.get('lengthSeconds', 0)}s" if r.get("lengthSeconds") else "",
                    ])), 200),
                })
            if out:
                return out
        except Exception:
            continue
    return []


def youtube_api(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """SearXNG youtube_api = youtube + 没 key 路径,统一走 youtube provider 兜底。"""
    return youtube(query, limit)


def vimeo(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Vimeo 公开搜索 HTML。"""
    if not query:
        return []
    url = "https://vimeo.com/search?q=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Vimeo)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    # Vimeo 链接形式 /video/<id>
    for chunk in html.split('"video_id":')[1:]:
        if len(out) >= limit:
            break
        vid = _between(chunk, ',', ',').strip() or _between(chunk, ' "', '"').strip()
        if not vid or not vid.isdigit():
            continue
        # title 跳过复杂解析
        out.append({
            "url": f"https://vimeo.com/{vid}",
            "title": "Vimeo video " + vid,
            "snippet": "",
        })
    return out


def piped(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Piped API(免 key, 类似 Invidious 但更轻量)。"""
    if not query:
        return []
    INSTANCES = [
        "https://pipedapi.kavin.rocks",
        "https://api.piped.projectsegf.lt",
        "https://pipedapi.adminforge.de",
    ]
    for inst in INSTANCES:
        url = (f"{inst}/search?" + urllib.parse.urlencode({
            "q": query.strip(),
            "filter": "videos",
        }))
        try:
            data = _json_get(url, timeout=5.0,
                             headers={"User-Agent": f"prisIrai/1.0 (Piped {inst})"})
            out: list[dict[str, Any]] = []
            for r in (data.get("items") or [])[:limit]:
                title = r.get("title") or ""
                url_v = r.get("url") or ""
                uploader = r.get("uploaderName") or ""
                duration = r.get("duration") or 0
                if not title or not url_v:
                    continue
                out.append({
                    "url": url_v,
                    "title": title,
                    "snippet": _truncate(" · ".join(filter(None, [
                        uploader,
                        f"{duration}s" if duration else "",
                    ])), 200),
                })
            if out:
                return out
        except Exception:
            continue
    return []


def piped_music(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Piped music 实例 = Piped API + filter=music_songs。"""
    if not query:
        return []
    INSTANCES = [
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.adminforge.de",
    ]
    for inst in INSTANCES:
        url = (f"{inst}/search?" + urllib.parse.urlencode({
            "q": query.strip(),
            "filter": "music_songs",
        }))
        try:
            data = _json_get(url, timeout=5.0,
                             headers={"User-Agent": f"prisIrai/1.0 (Piped music {inst})"})
            out: list[dict[str, Any]] = []
            for r in (data.get("items") or [])[:limit]:
                title = r.get("title") or ""
                url_v = r.get("url") or ""
                uploader = r.get("uploaderName") or ""
                duration = r.get("duration") or 0
                if not title or not url_v:
                    continue
                out.append({
                    "url": url_v,
                    "title": title,
                    "snippet": _truncate(" · ".join(filter(None, [
                        uploader,
                        f"{duration}s" if duration else "",
                    ])), 200),
                })
            if out:
                return out
        except Exception:
            continue
    return []


def bandcamp(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Bandcamp 公开 search JSON。

    走 bandcamp.com/search?q= 返回 HTML 含结构化数据。
    """
    if not query:
        return []
    url = "https://bandcamp.com/search?" + urllib.parse.urlencode({
        "q": query.strip(),
        "item_type": "t",  # tracks
    })
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Bandcamp)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            url_v = "https://bandcamp.com" + (r.get("url") or "") if r.get("url") and r.get("url").startswith("/") else (r.get("url") or "")
            title = r.get("name") or ""
            artist = r.get("band_name") or ""
            if not url_v or not title:
                continue
            out.append({
                "url": url_v,
                "title": title,
                "snippet": _truncate(artist, 200),
            })
        return out
    except Exception:
        return []


def mixcloud(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Mixcloud — 公共 search API(JSON, 需加 Accept + UA)。"""
    if not query:
        return []
    url = "https://api.mixcloud.com/search/?" + urllib.parse.urlencode({
        "q": query.strip(),
        "limit": str(max(1, min(limit, 20))),
        "type": "cloudcast",
    })
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Mixcloud)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("data") or [])[:limit]:
            url_v = r.get("url") or ""
            title = r.get("name") or ""
            user = (r.get("user") or {}).get("name") or ""
            if not url_v or not title:
                continue
            out.append({
                "url": url_v,
                "title": title,
                "snippet": _truncate(user, 200),
            })
        return out
    except Exception:
        return []


def dailymotion(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Dailymotion 公开 GraphQL search(免 key)。"""
    if not query:
        return []
    url = ("https://api.dailymotion.com/videos?" + urllib.parse.urlencode({
        "search": query.strip(),
        "limit": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Dailymotion)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("list") or [])[:limit]:
            url_v = r.get("url") or ""
            title = r.get("title") or ""
            owner = r.get("owner", {}).get("screenname") or ""
            duration = r.get("duration") or 0
            if not url_v or not title:
                continue
            out.append({
                "url": url_v,
                "title": title,
                "snippet": _truncate(" · ".join(filter(None, [owner, f"{duration}s" if duration else ""])), 200),
            })
        return out
    except Exception:
        return []


def soundcloud(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """SoundCloud — 公开 search HTML 兜底(API 需 client_id)。

    兜底走 soundcloud.com/search?q= HTML 提取。
    """
    if not query:
        return []
    url = "https://soundcloud.com/search?q=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (SoundCloud)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('"urn":"soundcloud:tracks:')[1:]:
        if len(out) >= limit:
            break
        tid = _between(chunk, '"', '"')
        if not tid:
            continue
        # SC 链接 /<user>/<track-slug>
        slug = _between(chunk.split(tid, 1)[-1], '"permalink_url":"', '"')
        if not slug:
            continue
        out.append({
            "url": slug.replace("\\/", "/"),
            "title": _strip_html(_between(chunk, '"title":"', '"')) or "SoundCloud track",
            "snippet": "",
        })
    return out


def openverse_audio(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Openverse(原 CC Search)音频 API(免 key)。"""
    if not query:
        return []
    url = ("https://api.openverse.org/v1/audio/?" + urllib.parse.urlencode({
        "q": query.strip(),
        "page_size": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Openverse)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("results") or [])[:limit]:
            url_v = r.get("url") or ""
            title = r.get("title") or ""
            creator = r.get("creator") or ""
            license_v = (r.get("license") or "").replace("-", " ").upper()
            if not url_v or not title:
                continue
            out.append({
                "url": url_v,
                "title": title,
                "snippet": _truncate(" · ".join(filter(None, [creator, license_v])), 200),
            })
        return out
    except Exception:
        return []


def freesound(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Freesound — 公共搜索页 HTML(SearXNG 暴露,API 受 key 限)。"""
    if not query:
        return []
    url = "https://freesound.org/search/?q=" + urllib.parse.quote(query.strip())
    try:
        html = _http_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Freesound)"})
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for chunk in html.split('class="sound_title"')[1:]:
        if len(out) >= limit:
            break
        link = _between(chunk, 'href="', '"')
        title = _strip_html(_between(chunk, 'title="', '"'))
        if not link:
            continue
        out.append({
            "url": "https://freesound.org" + link if link.startswith("/") else link,
            "title": title or "Freesound audio",
            "snippet": "",
        })
    return out


def register_all() -> None:
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("youtube", youtube)
    _ws.register_provider("youtube_api", youtube_api)
    _ws.register_provider("vimeo", vimeo)
    _ws.register_provider("piped", piped)
    _ws.register_provider("piped.music", piped_music)
    _ws.register_provider("bandcamp", bandcamp)
    _ws.register_provider("mixcloud", mixcloud)
    _ws.register_provider("dailymotion", dailymotion)
    _ws.register_provider("soundcloud", soundcloud)
    _ws.register_provider("openverse.audio", openverse_audio)
    _ws.register_provider("freesound", freesound)