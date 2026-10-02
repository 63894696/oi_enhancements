"""maps.py — 地理 / 地名 搜索(免 key)。

清单:
  - openstreetmap — Nominatim search(免 key 1 RPS / 秒)
  - photon       — Komoot Photon — OpenStreetMap 的全文搜索后端(免 key)
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from prisir_work.search_engines._common import _json_get, _truncate


def openstreetmap(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Nominatim — 地理地点搜索(免 key, 1 RPS 礼貌限制)。"""
    if not query:
        return []
    url = ("https://nominatim.openstreetmap.org/search?"
           + urllib.parse.urlencode({
               "q": query.strip(),
               "format": "jsonv2",
               "limit": str(max(1, min(limit, 20))),
               "addressdetails": "0",
           }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (OpenStreetMap Nominatim)"})
        out: list[dict[str, Any]] = []
        for r in data or []:
            lat = r.get("lat") or ""
            lon = r.get("lon") or ""
            url_v = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=15/{lat}/{lon}" if lat and lon else ""
            display_name = r.get("display_name") or ""
            rtype = r.get("type") or r.get("category") or ""
            if not url_v:
                continue
            out.append({
                "url": url_v,
                "title": display_name.split(",")[0].strip() or display_name[:60],
                "snippet": _truncate(" · ".join(filter(None, [rtype, display_name])), 300),
            })
        return out
    except Exception:
        return []


def photon(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Komoot Photon — OpenStreetMap 全文搜索(免 key)。"""
    if not query:
        return []
    url = ("https://photon.komoot.io/api/?" + urllib.parse.urlencode({
        "q": query.strip(),
        "limit": str(max(1, min(limit, 20))),
    }))
    try:
        data = _json_get(url, timeout=8.0,
                         headers={"User-Agent": "prisIrai/1.0 (Photon)"})
        out: list[dict[str, Any]] = []
        for r in (data.get("features") or [])[:limit]:
            props = r.get("properties") or {}
            geom = r.get("geometry") or {}
            coords = geom.get("coordinates") or [None, None]
            lon = coords[0] if len(coords) > 0 else None
            lat = coords[1] if len(coords) > 1 else None
            name = (props.get("name") or "").strip()
            url_v = (f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}"
                     if lat and lon else "")
            rtype = props.get("type") or props.get("osm_value") or ""
            country = (props.get("country") or "").strip()
            if not name or not url_v:
                continue
            snippet = " · ".join(filter(None, [rtype, country]))
            out.append({
                "url": url_v,
                "title": name,
                "snippet": _truncate(snippet, 300),
            })
        return out
    except Exception:
        return []


def register_all() -> None:
    from prisir_work import web_search as _ws  # 局部 import 避免循环
    _ws.register_provider("openstreetmap", openstreetmap)
    _ws.register_provider("photon", photon)