"""
prisir_work/pixabay_client.py — Pixabay 免费 stock 图片/视频 客户端(Phase 12 OM-P4 + OM-P4-fix, 2026-09-28)。

承接 [[prisIr-phase-11-om-p3-pre-compose]] + 用户「渐进 ship + 证据」决策。

## 定位
Pixabay 公开 REST API,免版权图片/视频素材。
- **需 key**(免费注册 → https://pixabay.com/api/docs/ → /api/key/)
- **速率 100 请求/60 秒**(实测 X-RateLimit-Remaining;非官方文档的 5000/小时)
- **真集成** — 用 urllib(零外部依赖)调 REST API

## 关键 API
  - search_images(query, image_type='all', limit=15) → list[ImageHit]
  - search_videos(query, limit=15) → list[VideoHit]
  - is_key_configured() → bool
  - probe_key() → ProbeResult  (真调一次 /?key= 探测)

## ⚠ Pixabay Music 在网页有,但 API 不开放(2026-09-28 用户纠正)
  - **网页**:https://pixabay.com/music/ 有海量免费 BGM,可下载
  - **公开 REST API**:https://pixabay.com/api/docs/ **只有 images + videos**
    - /api/ → 图片
    - /api/videos/ → 视频
  - /api/audio/ 返 403 Forbidden(API 不提供 Music 端点)
  - 因此本客户端**只覆盖 images + videos**,**没有 search_music**
  - BGM 程序化需求请走:archive_org_client(mp3) / 本地 fma 资源 / 第三方付费
  - **如需 Pixabay Music**:只能手动到 https://pixabay.com/music/ 下载 → 存本地,再用本地路径

## key 来源(按优先级)
  1. env PIXABAY_API_KEY
  2. ~/work/easel/.env 里 PIXABAY_API_KEY=xxx
  3. None(用户未配 → 探测返 False)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "ImageHit",
    "VideoHit",
    "ProbeResult",
    "search_images",
    "search_videos",
    "is_key_configured",
    "get_api_key",
    "probe_key",
    "PIXABAY_API_BASE",
]

log = logging.getLogger("prisir_work.pixabay_client")


PIXABAY_API_BASE = "https://pixabay.com/api/"
PIXABAY_VIDEOS_URL = "https://pixabay.com/api/videos/"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class ImageHit:
    """单条图片结果。"""
    id: int
    tags: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    thumbnail: str = ""
    webformat_url: str = ""   # ~640px 宽
    large_image_url: str = ""  # 原图
    page_url: str = ""        # Pixabay 详情页
    user: str = ""
    likes: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "tags": self.tags,
            "width": self.width, "height": self.height,
            "thumbnail": self.thumbnail,
            "webformat_url": self.webformat_url,
            "large_image_url": self.large_image_url,
            "page_url": self.page_url, "user": self.user, "likes": self.likes,
        }


@dataclass
class VideoHit:
    """单条视频结果(实测字段 2026-09-28 /api/videos/ 响应)。"""
    id: int
    tags: list[str] = field(default_factory=list)
    duration_sec: int = 0
    width: int = 0
    height: int = 0
    thumbnail: str = ""           # picture_id(由前端拼 url)
    videos: dict[str, str] = field(default_factory=dict)  # large/medium/small/tiny → url
    page_url: str = ""
    user: str = ""
    downloads: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "tags": self.tags,
            "duration_sec": self.duration_sec,
            "width": self.width, "height": self.height,
            "thumbnail": self.thumbnail, "videos": self.videos,
            "page_url": self.page_url, "user": self.user, "downloads": self.downloads,
        }


@dataclass
class ProbeResult:
    """key 探测结果。"""
    configured: bool
    valid: bool = False
    error: str = ""
    rate_limit_remaining: int = -1
    rate_limit_limit: int = -1
    rate_limit_reset: int = -1

    def to_dict(self) -> dict:
        return {
            "configured": self.configured,
            "valid": self.valid,
            "error": self.error,
            "rate_limit_remaining": self.rate_limit_remaining,
            "rate_limit_limit": self.rate_limit_limit,
            "rate_limit_reset": self.rate_limit_reset,
        }


# ---------------------------------------------------------------------------
# Key 探测
# ---------------------------------------------------------------------------

def _read_easel_env() -> dict[str, str]:
    """读 ~/work/easel/.env(SKILL.md 规范),只读不抛。"""
    out: dict[str, str] = {}
    for p in [Path.home() / "work" / "easel" / ".env",
              Path.home() / "work" / "zju_easel" / ".env"]:
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            continue
    return out


def get_api_key() -> Optional[str]:
    """按优先级取 key:env > easel/.env。"""
    k = os.environ.get("PIXABAY_API_KEY")
    if k:
        return k.strip()
    env = _read_easel_env()
    return env.get("PIXABAY_API_KEY")


def is_key_configured() -> bool:
    return bool(get_api_key())


def _parse_rate_limit_headers(headers) -> tuple[int, int, int]:
    """从 urllib response headers 取 X-RateLimit-* 三个字段。"""
    def _g(name: str) -> int:
        v = headers.get(name, "")
        try:
            return int(v) if v.lstrip("-").isdigit() else -1
        except Exception:
            return -1
    return _g("X-RateLimit-Remaining"), _g("X-RateLimit-Limit"), _g("X-RateLimit-Reset")


def probe_key(api_key: Optional[str] = None) -> ProbeResult:
    """真调一次 Pixabay API 探测 key 是否有效。

    无 key → configured=False
    有 key 但 400/403 → configured=True, valid=False, error=...
    有 key 且 200 → configured=True, valid=True
    """
    key = api_key or get_api_key()
    if not key:
        return ProbeResult(configured=False, valid=False, error="PIXABAY_API_KEY 未配置")

    url = f"{PIXABAY_API_BASE}?key={urllib.parse.quote(key)}&per_page=3&q=test"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PrisirAI/Phase-12"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
            rl, rlim, rrst = _parse_rate_limit_headers(resp.headers)
            body = resp.read(1024).decode("utf-8", errors="ignore")
            if code == 200:
                return ProbeResult(
                    configured=True, valid=True,
                    rate_limit_remaining=rl,
                    rate_limit_limit=rlim,
                    rate_limit_reset=rrst,
                )
            return ProbeResult(
                configured=True, valid=False,
                error=f"HTTP {code}: {body[:200]}",
            )
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(200).decode("utf-8", errors="ignore")
        except Exception:
            pass
        return ProbeResult(
            configured=True, valid=False,
            error=f"HTTP {e.code}: {body[:200]}",
        )
    except Exception as e:
        return ProbeResult(configured=True, valid=False, error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 搜索 API
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = 15) -> dict:
    """GET URL 返 JSON 字典(抛异常给调用方)。"""
    req = urllib.request.Request(url, headers={"User-Agent": "PrisirAI/Phase-12"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_tags(raw) -> list[str]:
    """Pixabay tags 字段是逗号分隔字符串。"""
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def search_images(query: str = "", image_type: str = "all", limit: int = 15,
                  api_key: Optional[str] = None) -> list[ImageHit]:
    """搜 Pixabay 图片。

    Args:
        query: 关键词(留空返 popular)
        image_type: 'all' / 'photo' / 'illustration' / 'vector'
        limit: 1-200(默认 15,Pixabay 上限 200)
        api_key: 覆盖默认 key

    Returns:
        list[ImageHit];无 key 或失败返 []
    """
    key = api_key or get_api_key()
    if not key:
        log.warning("search_images: PIXABAY_API_KEY 未配置")
        return []
    try:
        params = {
            "key": key, "q": query, "per_page": str(limit),
            "image_type": image_type, "safesearch": "true",
        }
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        url = f"{PIXABAY_API_BASE}?{qs}"
        data = _http_get_json(url)
        hits: list[ImageHit] = []
        for h in data.get("hits", []):
            hits.append(ImageHit(
                id=int(h.get("id", 0)),
                tags=_parse_tags(h.get("tags", "")),
                width=int(h.get("imageWidth", 0)),
                height=int(h.get("imageHeight", 0)),
                thumbnail=h.get("previewURL", ""),
                webformat_url=h.get("webformatURL", ""),
                large_image_url=h.get("largeImageURL", ""),
                page_url=h.get("pageURL", ""),
                user=h.get("user", ""),
                likes=int(h.get("likes", 0)),
            ))
        return hits
    except Exception as e:
        log.warning("search_images 失败: %s", e)
        return []


def search_videos(query: str = "", limit: int = 15,
                  api_key: Optional[str] = None) -> list[VideoHit]:
    """搜 Pixabay 视频(/api/videos/)。

    实测响应字段(2026-09-28):id/pageURL/type/tags/duration/videos.{large,medium,small,tiny}。
    """
    key = api_key or get_api_key()
    if not key:
        log.warning("search_videos: PIXABAY_API_KEY 未配置")
        return []
    try:
        params = {
            "key": key, "q": query, "per_page": str(limit),
            "safesearch": "true",
        }
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        url = f"{PIXABAY_VIDEOS_URL}?{qs}"
        data = _http_get_json(url)
        hits: list[VideoHit] = []
        for h in data.get("hits", []):
            videos = h.get("videos", {})
            hits.append(VideoHit(
                id=int(h.get("id", 0)),
                tags=_parse_tags(h.get("tags", "")),
                duration_sec=int(h.get("duration", 0)),
                width=int(h.get("width", 0)),
                height=int(h.get("height", 0)),
                thumbnail=h.get("picture_id", ""),
                videos={k: v.get("url", "") for k, v in videos.items() if isinstance(v, dict)},
                page_url=h.get("pageURL", ""),
                user=h.get("user", ""),
                downloads=int(h.get("downloads", 0)),
            ))
        return hits
    except Exception as e:
        log.warning("search_videos 失败: %s", e)
        return []