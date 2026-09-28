"""
prisir_work/free_resource_fetcher.py — 免费资源统一门面(Phase 12 OM-P4, 2026-09-28)。

承接 [[prisIr-phase-11-om-p3-pre-compose]] + 用户「渐进 ship + 证据」决策。

## 定位
统一封装 3 个免费资源客户端,作为 video_creator 的 fallback 入口:
- edge_tts_client  — 免费 TTS(无 key,中文 Xiaoxiao)
- pixabay_client    — 免费 BGM/视频(需 PIXABAY_API_KEY)
- archive_org_client — 免费 stock 视频(无 key,历史素材)

## 关键 API
  - free_tts(text, output_path) → TTSResult
  - free_bgm(query, limit=10) → list[BGMHit]
  - free_stock_video(query, limit=10) → list[Union[VideoHit, ArchiveHit]]
  - free_resource_status() → dict  (3 个资源就绪状态 + 配额)

## 不破坏 video_creator
- video_creator.pick_provider_for_creator 已 ship(OM-P2)
- 本模块是更高层 facade,让 video_creator fallback 时有真路径
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Union

__all__ = [
    "FreeResourceStatus",
    "free_tts",
    "free_bgm",
    "free_stock_video",
    "free_resource_status",
]

log = logging.getLogger("prisir_work.free_resource_fetcher")


# ---------------------------------------------------------------------------
# 状态聚合
# ---------------------------------------------------------------------------

@dataclass
class FreeResourceStatus:
    """3 个免费资源的就绪状态。"""
    edge_tts_available: bool = False
    pixabay_configured: bool = False
    pixabay_valid: bool = False
    archive_org_available: bool = False
    chinese_voices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_tts_available": self.edge_tts_available,
            "pixabay_configured": self.pixabay_configured,
            "pixabay_valid": self.pixabay_valid,
            "archive_org_available": self.archive_org_available,
            "chinese_voices": list(self.chinese_voices),
        }


def free_resource_status() -> FreeResourceStatus:
    """聚合 3 个资源状态(轻探测,不应阻塞)。"""
    status = FreeResourceStatus()

    try:
        from .edge_tts_client import is_available as _et, list_chinese_voices
        status.edge_tts_available = _et()
        status.chinese_voices = list_chinese_voices()
    except ImportError:
        status.edge_tts_available = False

    try:
        from .pixabay_client import is_key_configured, probe_key
        status.pixabay_configured = is_key_configured()
        if status.pixabay_configured:
            pr = probe_key()
            status.pixabay_valid = pr.valid
    except ImportError:
        status.pixabay_configured = False

    try:
        from .archive_org_client import is_available
        status.archive_org_available = is_available()
    except ImportError:
        status.archive_org_available = False

    return status


# ---------------------------------------------------------------------------
# 高层 API
# ---------------------------------------------------------------------------

def free_tts(text: str, output_path: str,
             voice: str = "zh-CN-XiaoxiaoNeural",
             rate: str = "+0%",
             pitch: str = "+0Hz") -> "TTSResult":
    """免费 TTS 调用(转 edge_tts_client.synthesize_sync)。"""
    try:
        from .edge_tts_client import synthesize_sync
    except ImportError as e:
        from dataclasses import dataclass
        @dataclass
        class ErrR:
            ok: bool = False
            error: str = ""
        return ErrR(ok=False, error=f"edge_tts_client 未装: {e}")
    return synthesize_sync(text, output_path, voice=voice, rate=rate, pitch=pitch)


def free_bgm(query: str = "", limit: int = 10) -> list:
    """免费 BGM 调用(转 pixabay_client.search_music)。

    无 key → 返 []
    """
    try:
        from .pixabay_client import search_music
    except ImportError:
        return []
    return search_music(query=query, limit=limit)


def free_stock_video(query: str = "", limit: int = 10) -> list:
    """免费 stock 视频(优先 Pixabay,失败 fallback Archive.org)。"""
    try:
        from .pixabay_client import is_key_configured, search_videos
    except ImportError:
        search_videos = None
        is_key_configured = lambda: False  # noqa: E731

    hits: list = []
    if search_videos is not None and is_key_configured():
        hits.extend(search_videos(query=query, limit=limit))

    # Pixabay 不够 → Archive.org 兜底
    if len(hits) < limit:
        try:
            from .archive_org_client import search_videos as arch_search
        except ImportError:
            arch_search = None
        if arch_search is not None:
            remaining = limit - len(hits)
            arch_hits = arch_search(query=query, limit=remaining,
                                    mediatype="movies")
            # 避免重复 identifier
            existing_ids = {getattr(h, "id", None) for h in hits}
            for ah in arch_hits:
                if ah.identifier not in existing_ids:
                    hits.append(ah)

    return hits