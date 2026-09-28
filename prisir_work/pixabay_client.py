"""
prisir_work/pixabay_client.py — Pixabay 免费 BGM/图片/视频 客户端(Phase 12 OM-P4, 2026-09-28)。

承接 [[prisIr-phase-11-om-p3-pre-compose]] + 用户「渐进 ship + 证据」决策。

## 定位
Pixabay 公开 REST API,免版权 BGM/图片/视频素材。
- **需 key**(免费注册 → https://pixabay.com/api/docs/ → /api/key/)
- **5000 请求/小时 限额**(个人完全够用)
- **真集成** — 用 urllib(零外部依赖)调 REST API

## 关键 API
  - search_music(query, limit=10) → list[BGMHit]
  - search_videos(query, limit=10) → list[VideoHit]
  - is_key_configured() → bool
  - probe_key() → ProbeResult  (真调一次 /videos/?key= 探测)

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
    "BGMHit",
    "VideoHit",
    "ProbeResult",
    "search_music",
    "search_videos",
    "is_key_configured",
    "get_api_key",
    "probe_key",
    "PIXABAY_API_BASE",
]

log = logging.getLogger("prisir_work.pixabay_client")


PIXABAY_API_BASE = "https://pixabay.com/api/"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class BGMHit:
    """单条音乐结果。"""
    id: int
    title: str
    artist: str
    duration_sec: int
    tags: list[str] = field(default_factory=list)
    url: str = ""           # Pixabay 详情页
    audio_url: str = ""     # 直链(可能需要二次请求)
    license: str = ""       # pixabay license(免费商用)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "artist": self.artist,
            "duration_sec": self.duration_sec, "tags": self.tags,
            "url": self.url, "audio_url": self.audio_url, "license": self.license,
        }


@dataclass
class VideoHit:
    """单条视频结果。"""
    id: int
    tags: list[str] = field(default_factory=list)
    duration_sec: int = 0
    width: int = 0
    height: int = 0
    thumbnail: str = ""
    videos: dict[str, str] = field(default_factory=dict)  # quality → url

    def to_dict(self) -> dict:
        return {
            "id": self.id, "tags": self.tags,
            "duration_sec": self.duration_sec,
            "width": self.width, "height": self.height,
            "thumbnail": self.thumbnail, "videos": self.videos,
        }


@dataclass
class ProbeResult:
    """key 探测结果。"""
    configured: bool
    valid: bool = False
    error: str = ""
    rate_limit_remaining: int = -1

    def to_dict(self) -> dict:
        return {
            "configured": self.configured,
            "valid": self.valid,
            "error": self.error,
            "rate_limit_remaining": self.rate_limit_remaining,
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


def probe_key(api_key: Optional[str] = None) -> ProbeResult:
    """真调一次 Pixabay API 探测 key 是否有效。

    无 key → configured=False
    有 key 但 400 → configured=True, valid=False, error=...
    有 key 且 200 → configured=True, valid=True
    """
    key = api_key or get_api_key()
    if not key:
        return ProbeResult(configured=False, valid=False, error="PIXABAY_API_KEY 未配置")

    url = f"{PIXABAY_API_BASE}?key={urllib.parse.quote(key)}&per_page=1&q=test"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PrisirAI/Phase-12"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
            rl = resp.headers.get("X-RateLimit-Remaining", "-1")
            body = resp.read(1024).decode("utf-8", errors="ignore")
            if code == 200:
                return ProbeResult(
                    configured=True, valid=True,
                    rate_limit_remaining=int(rl) if rl.lstrip("-").isdigit() else -1,
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


def search_music(query: str = "", limit: int = 10,
                 api_key: Optional[str] = None) -> list[BGMHit]:
    """搜索 BGM(query 可留空返 trending)。

    返回 [] 如果 key 未配或 API 失败(不抛栈)。
    """
    key = api_key or get_api_key()
    if not key:
        log.warning("search_music: PIXABAY_API_KEY 未配置")
        return []
    try:
        url = (
            f"{PIXABAY_API_BASE}?key={urllib.parse.quote(key)}"
            f"&q={urllib.parse.quote(query)}&per_page={limit}&category=music"
        )
        # Pixabay music 用 /api/?category=music 不可,要走 /api/audio/
        url_audio = (
            f"https://pixabay.com/api/audio/?key={urllib.parse.quote(key)}"
            f"&q={urllib.parse.quote(query)}&per_page={limit}"
        )
        try:
            data = _http_get_json(url_audio)
        except urllib.error.HTTPError:
            # 旧 API 也接受 /api/?
            data = _http_get_json(url)
        hits = []
        for h in data.get("hits", []):
            hits.append(BGMHit(
                id=h.get("id", 0),
                title=h.get("title", ""),
                artist=h.get("artist", ""),
                duration_sec=int(h.get("duration", 0)),
                tags=h.get("tags", "").split(", ") if isinstance(h.get("tags"), str) else h.get("tags", []),
                url=h.get("pageURL", ""),
                audio_url=h.get("audio", ""),
                license="pixabay",
            ))
        return hits
    except Exception as e:
        log.warning("search_music 失败: %s", e)
        return []


def search_videos(query: str = "", limit: int = 10,
                  api_key: Optional[str] = None) -> list[VideoHit]:
    """搜索 stock 视频。"""
    key = api_key or get_api_key()
    if not key:
        log.warning("search_videos: PIXABAY_API_KEY 未配置")
        return []
    try:
        url = (
            f"https://pixabay.com/api/videos/?key={urllib.parse.quote(key)}"
            f"&q={urllib.parse.quote(query)}&per_page={limit}"
        )
        data = _http_get_json(url)
        hits = []
        for h in data.get("hits", []):
            videos = h.get("videos", {})
            hit = VideoHit(
                id=h.get("id", 0),
                tags=h.get("tags", "").split(", ") if isinstance(h.get("tags"), str) else h.get("tags", []),
                duration_sec=int(h.get("duration", 0)),
                width=int(h.get("width", 0)),
                height=int(h.get("height", 0)),
                thumbnail=h.get("picture_id", ""),
                videos=videos,
            )
            hits.append(hit)
        return hits
    except Exception as e:
        log.warning("search_videos 失败: %s", e)
        return []