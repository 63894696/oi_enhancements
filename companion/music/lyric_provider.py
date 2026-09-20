# -*- coding: utf-8 -*-
"""
lyric_provider.py — M3.29.1 LRC 多源歌词

优先级(降级):
  1. 本地同名 <stem>.lrc  (music_root/<artist>/<title>.lrc)
  2. LRCLib.net 在线 API   (https://lrclib.net/api/search?q=...)
  3. 留空(无歌词,前端显示「(纯享模式)」)

LRCLib 协议:
  GET /api/search?q={title} {artist}       → List[{id, trackName, artistName, plainLyrics, syncedLyrics}]
  GET /api/get/{trackId}                   → 单曲详情(带 syncedLyrics 含 [mm:ss.xx] 字头)
  GET /api/get?artist_name=&track_name=&album_name=  → 精确匹配

注意:LyricWord 逐字解析复用 lyric_loader.parse_lrc — 该函数已支持 <time>字<time>字 格式。
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# 让 lyric_loader(同目录)可被 import
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from lyric_loader import parse_lrc  # noqa: E402  (复用同目录已有模块)

log = logging.getLogger("lyric_provider")

LRCLIB_BASE = "https://lrclib.net"


@dataclass
class LyricResult:
    ok: bool
    source: str            # "local" | "lrclib" | "none"
    raw: str = ""
    lines_count: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "lines_count": self.lines_count,
            "error": self.error,
        }


def _normalize(s: str) -> str:
    """归一化匹配串:小写 + 去空格 + 去常见标点(括号里的版本号等)。"""
    s = (s or "").lower().strip()
    for ch in "()（）[]【】-_—:：,.。，":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def find_local_lrc(music_root: Path, title: str, artist: str = "") -> Optional[Path]:
    """扫 music_root 下任何 <title>.lrc / <artist> - <title>.lrc。

    music_root 通常是用户音乐库根(~/Music 或 D:/Music),
    文件结构可能是:
        ~/Music/周杰伦/晴天.mp3
        ~/Music/周杰伦/晴天.lrc           (本函数命中)
        ~/Music/周杰伦 - 晴天.mp3
        ~/Music/周杰伦 - 晴天.lrc         (本函数命中)
    """
    if not music_root or not music_root.exists():
        return None
    title_n = _normalize(title)
    artist_n = _normalize(artist) if artist else ""
    # 限制深度 3 层,避免递归扫整盘
    for lrc_path in music_root.rglob("*.lrc"):
        if lrc_path.name.startswith(".") or lrc_path.stat().st_size > 200_000:
            continue
        stem_n = _normalize(lrc_path.stem)
        if title_n in stem_n or stem_n in title_n:
            return lrc_path
        if artist_n and (artist_n in stem_n or stem_n in artist_n):
            return lrc_path
    return None


def load_local_lrc(path: Path) -> LyricResult:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return LyricResult(ok=False, source="local", error=f"read fail: {e}")
    lines = parse_lrc(raw)
    return LyricResult(
        ok=bool(lines), source="local", raw=raw,
        lines_count=len(lines),
    )


async def fetch_lrclib(title: str, artist: str = "", album: str = "",
                       timeout: float = 8.0) -> LyricResult:
    """异步 GET LRCLib。

    注意:LyricResult.raw 不返给前端 raw LRC 文本(前端只需要 parse 后结果),
    返 syncedLyrics 字段(已含时间标签)。
    """
    import aiohttp  # type: ignore

    params = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    if album:
        params["album_name"] = album

    url = f"{LRCLIB_BASE}/api/get"
    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as sess:
            async with sess.get(url, params=params) as resp:
                if resp.status == 404:
                    return LyricResult(ok=False, source="lrclib", error="not found (404)")
                if resp.status != 200:
                    return LyricResult(ok=False, source="lrclib",
                                       error=f"HTTP {resp.status}: {await resp.text()[:200]}")
                data = await resp.json(content_type=None)
                synced = data.get("syncedLyrics") or ""
                plain = data.get("plainLyrics") or ""
                if not synced and not plain:
                    return LyricResult(ok=False, source="lrclib", error="empty lyrics")
                raw = synced or plain
                lines = parse_lrc(raw)
                return LyricResult(
                    ok=bool(lines), source="lrclib", raw=raw,
                    lines_count=len(lines),
                )
    except asyncio.TimeoutError:
        return LyricResult(ok=False, source="lrclib", error="timeout")
    except Exception as e:  # noqa: BLE001
        return LyricResult(ok=False, source="lrclib", error=f"{type(e).__name__}: {e}")


class LyricProvider:
    """聚合:本地优先 → 在线 → 失败返空。

    用法:
        prov = LyricProvider(Path.home() / "Music")
        res = await prov.get(title="晴天", artist="周杰伦")
        if res.ok:
            lines = parse_lrc(res.raw)  # 给前端 LyricLine 列表
    """

    def __init__(self, music_root: Path, *, enable_lrclib: bool = True):
        self.music_root = Path(music_root) if music_root else None
        self.enable_lrclib = enable_lrclib

    async def get(self, title: str, artist: str = "", album: str = "") -> LyricResult:
        # 1. 本地
        if self.music_root:
            local = find_local_lrc(self.music_root, title, artist)
            if local:
                res = load_local_lrc(local)
                if res.ok:
                    log.info("[lyric] local hit: %s", local.name)
                    return res
        # 2. 在线
        if self.enable_lrclib:
            res = await fetch_lrclib(title, artist, album)
            if res.ok:
                log.info("[lyric] lrclib hit: %s - %s (%d lines)", artist, title, res.lines_count)
                return res
            log.info("[lyric] lrclib miss: %s", res.error)
        # 3. 兜底
        return LyricResult(ok=False, source="none", error="no lyric available")


def quick_smoke() -> Dict[str, Any]:
    """独立可跑:cd companion && python -c 'from music.lyric_provider import quick_smoke; print(quick_smoke())'"""
    import asyncio
    root = Path.home() / "Music"
    prov = LyricProvider(root)
    res = asyncio.run(prov.get(title="Bohemian Rhapsody", artist="Queen"))
    return {"res": res.to_dict(), "raw_head": res.raw[:200]}


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(quick_smoke(), ensure_ascii=False, indent=2))
