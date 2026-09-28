"""
prisir_work/archive_org_client.py — Archive.org 免费 stock 视频客户端(Phase 12 OM-P4, 2026-09-28)。

承接 [[prisIr-phase-11-om-p3-pre-compose]] + 用户「渐进 ship + 证据」决策。

## 定位
Internet Archive 公开 metadata API + Advanced Search,免版权历史素材。
- **零依赖 / 免 key / 完全免费**
- **真集成** — 用 urllib(零外部依赖)调 archive.org JSON API

## 关键 API
  - search_videos(query, limit=10) → list[ArchiveHit]
  - get_metadata(identifier) → dict  (单条详情)
  - is_available() → bool  (无需探测,archive.org 通常可用)

## 资源类型
- mediatype:movies   — 视频
- mediatype:audio    — 音频
- mediatype:image    — 图片
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "ArchiveHit",
    "search_videos",
    "get_metadata",
    "is_available",
    "ARCHIVE_API_BASE",
]

log = logging.getLogger("prisir_work.archive_org_client")


ARCHIVE_API_BASE = "https://archive.org"
ADVANCED_SEARCH_URL = f"{ARCHIVE_API_BASE}/advancedsearch.php"


@dataclass
class ArchiveHit:
    """单条 archive.org 搜索结果。"""
    identifier: str
    title: str
    mediatype: str
    year: int = 0
    creator: str = ""
    description: str = ""
    downloads: int = 0
    files_count: int = 0
    url: str = ""           # https://archive.org/details/<id>

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier, "title": self.title,
            "mediatype": self.mediatype, "year": self.year,
            "creator": self.creator, "description": self.description,
            "downloads": self.downloads, "files_count": self.files_count,
            "url": self.url,
        }


def is_available() -> bool:
    """archive.org 一般总是可用,这里做一次轻探测。"""
    try:
        req = urllib.request.Request(
            f"{ARCHIVE_API_BASE}/advancedsearch.php?q=mediatype:movies&output=json&rows=1",
            headers={"User-Agent": "PrisirAI/Phase-12"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.getcode() == 200
    except Exception:
        return False


def search_videos(query: str = "", limit: int = 10,
                  mediatype: str = "movies") -> list[ArchiveHit]:
    """搜 archive.org 视频/音频/图片。

    Args:
        query: 关键词(留空返 trending)
        limit: 返几条(默认 10)
        mediatype: movies / audio / image(默认 movies)

    Returns:
        list[ArchiveHit](失败返 [])
    """
    try:
        q_parts = [f"mediatype:{mediatype}"]
        if query:
            q_parts.append(f"({query})")
        q = " AND ".join(q_parts)
        url = (
            f"{ADVANCED_SEARCH_URL}?q={urllib.parse.quote(q)}"
            f"&output=json&rows={limit}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "PrisirAI/Phase-12"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        hits: list[ArchiveHit] = []
        for doc in data.get("response", {}).get("docs", []):
            hits.append(ArchiveHit(
                identifier=doc.get("identifier", ""),
                title=doc.get("title", ""),
                mediatype=doc.get("mediatype", mediatype),
                year=int(doc.get("year", 0) or 0),
                creator=doc.get("creator", "") or "",
                description=(doc.get("description", "") or "")[:200],
                downloads=int(doc.get("downloads", 0) or 0),
                files_count=int(doc.get("item_size", 0) or 0),  # archive.org 字段不固定
                url=f"{ARCHIVE_API_BASE}/details/{doc.get('identifier', '')}",
            ))
        return hits
    except Exception as e:
        log.warning("archive.org search_videos 失败: %s", e)
        return []


def get_metadata(identifier: str) -> Optional[dict]:
    """取单条 archive.org item 的完整 metadata。"""
    if not identifier:
        return None
    try:
        url = f"{ARCHIVE_API_BASE}/metadata/{urllib.parse.quote(identifier)}"
        req = urllib.request.Request(url, headers={"User-Agent": "PrisirAI/Phase-12"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("archive.org get_metadata 失败: %s", e)
        return None