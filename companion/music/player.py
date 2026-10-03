# -*- coding: utf-8 -*-
"""
player.py — M3.29.1 自实现音乐播放器引擎

模块分层(从下到上):
  AudioBackend          - HTTP 流媒体代理(`/api/stream/<song_id>` 从本地文件字节流出)
  LocalLibrary          - 扫 music_root 下 mp3/m4a/flac/opus 文件 → List[Track]
  OnlineSearch          - jsdom shim lyswhut 走 musicUrl 拿 URL(本期仅直链,搜索功能限)
  PlaylistManager       - 队列 + 种子驱动 + 滚动填充
  Player                - 状态机(idle / playing / paused) + cmd 路由
                          state 广播 ws 订阅者

设计:
  - 音频实际播放由前端 HTMLAudioElement 完成(URL = http://127.0.0.1:{port}/stream/{track_id})
  - 后端不做音频解码 / 不维护 audio buffer(避免 MP3 解码依赖 mutagen 等第三方)
  - 后端只负责:扫库 + cmd 路由 + 状态广播 + 进度计时(估算)

进度:由于 audio 在前端播,后端没有真实 position。
策略:后端根据 `last_play_started_at + paused_offset + volume_delay` 估算 progress,
前端通过 ws 心跳回传真实 position(M3.29.2 接)。这是 LY Music Desktop Open API 的折中方案。
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

log = logging.getLogger("player")

AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac"}


@dataclass
class Track:
    id: str                 # 本地 id = sha1(路径) 前 16
    title: str
    artist: str
    album: str
    path: str               # 本地绝对路径
    duration: float = 0.0   # 秒(本地库暂时 = 0,前端 <audio> 报告后再回填)
    source: str = "local"   # local | online | playlist

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AudioBackend:
    """HTTP 流代理:GET /api/stream/<song_id> → 字节流本地 mp3 文件。

    实现:不在此处(此处只提供 track_id → file_path 映射),
    实际路由在 music_web.py 中实现,本类负责生成 stream URL。
    """

    @staticmethod
    def stream_url(host: str, port: int, track_id: str) -> str:
        return f"http://{host}:{port}/api/stream/{quote(track_id, safe='')}"


class LocalLibrary:
    """扫本地音乐库。

    约定 music_root/<artist?>/<title>.<ext> 结构,文件名解析 artist/title:
      周杰伦 - 晴天.mp3        →  artist=周杰伦 title=晴天
      周杰伦/晴天.mp3          →  artist=周杰伦 title=晴天
      晴天.mp3                 →  artist=""    title=晴天
    """

    def __init__(self, music_root: Path):
        self.music_root = Path(music_root) if music_root else None
        self._tracks: Dict[str, Track] = {}
        self._by_artist: Dict[str, List[str]] = {}

    @staticmethod
    def _track_id(path: Path) -> str:
        import hashlib
        return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _parse_filename(name: str) -> tuple[str, str]:
        """返 (artist, title)。"""
        stem = Path(name).stem
        for sep in (" - ", " – ", " — ", "-", "_"):
            if sep in stem:
                a, _, t = stem.partition(sep)
                return a.strip(), t.strip()
        return "", stem.strip()

    def scan(self, max_count: int = 5000) -> List[Track]:
        if not self.music_root or not self.music_root.exists():
            log.warning("[library] music_root not exists: %s", self.music_root)
            return []
        tracks: List[Track] = []
        seen: Set[str] = set()
        for p in self.music_root.rglob("*"):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                tid = self._track_id(p)
                if tid in seen:
                    continue
                seen.add(tid)
                if len(tracks) >= max_count:
                    break
                artist, title = self._parse_filename(p.name)
                if not title:
                    title = p.stem
                t = Track(
                    id=tid, title=title, artist=artist, album="",
                    path=str(p.resolve()), source="local",
                )
                tracks.append(t)
                self._tracks[tid] = t
                self._by_artist.setdefault(artist, []).append(tid)
        log.info("[library] scanned %d tracks under %s", len(tracks), self.music_root)
        return tracks

    def get(self, track_id: str) -> Optional[Track]:
        return self._tracks.get(track_id)

    def search(self, query: str, limit: int = 20) -> List[Track]:
        """简单匹配:query 命中 title 或 artist(大小写无关)。"""
        q = (query or "").lower().strip()
        if not q:
            return []
        hits: List[Track] = []
        for t in self._tracks.values():
            if q in t.title.lower() or q in t.artist.lower():
                hits.append(t)
                if len(hits) >= limit:
                    break
        return hits

    def add_track(self, t: Track) -> None:
        """P2.5+22(2026-10-03):加入虚拟 track(来自 lx_runtime 多源 fallback)。

        _tracks / _by_artist 都更新,后续 search() / get() 行为一致。
        """
        self._tracks[t.id] = t
        if t.artist:
            self._by_artist.setdefault(t.artist, []).append(t.id)

    def random_track_ids(self, limit: int = 20) -> List[str]:
        """P2.5+22:队列空 fallback 用,库内随机 N 首 id(纯本地,不依赖外源)。"""
        import random
        all_ids = list(self._tracks.keys())
        if not all_ids:
            return []
        random.shuffle(all_ids)
        return all_ids[:limit]


class OnlineSearch:
    """在线搜:复用 lx_runtime_client 走 lyswhut 主源(已集成的多源)。

    注意:LX source 只暴露 musicUrl(给 songmid 返直链),不暴露 search 端点。
    本类只支持「给定 songInfo → 拿 URL」,搜索由 agent 给关键词 → 拼 mock 数据 or
    走 LRCLib 拿歌名匹配(本期不强依赖)。

    P2.5+22(2026-10-03):多源 fallback。默认 mock.js 单源(可能返 googleapis 公共 mp3),
    用户点播放空队列时:
      1) get_url_multi 按 sources 顺序轮询 musicUrl
      2) 任意源 ok → 用其 url 入库(source="lx:<source>")
      3) 全失败 → 返 ok=False,前端给 toast
    """

    DEFAULT_SOURCES = ["mock.js", "juhe.js"]

    def __init__(self, sources: Optional[List[str]] = None):
        # P2.5+22:默认多源(mock + juhe),ikun 排除(国内 DNS 不可达必崩进程)。
        self._sources = sources or list(self.DEFAULT_SOURCES)
        self._client = None

    def _ensure(self):
        if self._client is None:
            try:
                from lx_runtime_client import LxRuntimeClient
                self._client = LxRuntimeClient(sources=self._sources)
            except Exception as e:
                log.warning("[online] client init fail: %s", e)
                self._client = None
        return self._client

    def get_url(self, source: str, song_info: Dict[str, Any]) -> Dict[str, Any]:
        """返 {"ok": bool, "url"?: str, "source"?: str, "err"?: str}。

        lx 框架两层回包形态:
          - source js 直接 return url_string → rpc 返 {ok: True, result: url_string}
          - 中间件返 {ok: True, data: {url: ...}} 或 {ok: True, data: url_string}
        两种都兼容。
        """
        cli = self._ensure()
        if not cli:
            return {"ok": False, "err": "lx_runtime client not available"}
        try:
            resp = cli.call(action="musicUrl", source=source,
                            info={"musicInfo": song_info, "type": "320k"})
            if not resp.get("ok"):
                return {"ok": False, "err": resp.get("err", "lx_runtime returned not-ok")}
            url = ""
            # 路径 1: result 是 url string(juhe/mock 直接 return string)
            r = resp.get("result")
            if isinstance(r, str) and r.startswith("http"):
                url = r
            # 路径 2: data 是 dict 含 url
            elif isinstance(resp.get("data"), dict):
                url = resp["data"].get("url", "") or ""
            # 路径 3: data 本身就是 url string
            elif isinstance(resp.get("data"), str) and resp["data"].startswith("http"):
                url = resp["data"]
            if not url:
                return {"ok": False, "err": f"{source} returned empty url"}
            return {"ok": True, "url": url, "source": source}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "err": f"{type(e).__name__}: {e}"}

    def get_url_multi(self, song_info: Dict[str, Any]) -> Dict[str, Any]:
        """P2.5+22:按 sources 顺序轮询 musicUrl,首个成功即返。

        返回字段: ok / url / source / err
        """
        last_err = ""
        for src in self._sources:
            r = self.get_url(src, song_info)
            if r.get("ok"):
                return r
            last_err = f"{src}={r.get('err', '?')}"
            log.info("[online] %s fail: %s, try next", src, r.get("err"))
        return {"ok": False, "err": f"all sources failed: {last_err}"}

    def list_sources(self) -> List[str]:
        return list(self._sources)

    def shutdown(self) -> None:
        if self._client:
            try:
                self._client.shutdown()
            except Exception:
                pass
            self._client = None


@dataclass
class PlayerState:
    status: str = "idle"          # idle | playing | paused | stopped
    track: Optional[Track] = None
    progress: float = 0.0
    duration: float = 0.0
    volume: int = 80
    muted: bool = False
    playback_mode: str = "sequential"  # sequential | shuffle | repeat_one
    updated_at: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get("track"):
            d["track"] = self.track.to_dict()
        return d


class PlaylistManager:
    """队列 + 种子驱动。

    简单优先:
      - 用户播一首歌 → 入 seeds(deque maxlen=20)
      - 队列空时从 local_library 滚动填充(随机 N 首)
      - 用户搜索 → 替换队列
    """

    def __init__(self, library: LocalLibrary):
        self.library = library
        self.queue: List[str] = []   # track_id 列表
        self.cursor: int = -1         # 当前播放 index,-1 = 未开始

    def set_queue(self, track_ids: List[str]) -> None:
        self.queue = list(track_ids)
        self.cursor = 0 if self.queue else -1

    def append(self, track_id: str) -> None:
        if track_id not in self.queue:
            self.queue.append(track_id)
            if self.cursor < 0:
                self.cursor = 0

    def next_id(self, mode: str = "sequential") -> Optional[str]:
        if not self.queue:
            return None
        if mode == "repeat_one" and self.cursor >= 0:
            return self.queue[self.cursor]
        if mode == "shuffle":
            import random
            if len(self.queue) > 1:
                choices = [i for i in range(len(self.queue)) if i != self.cursor]
                if choices:
                    self.cursor = random.choice(choices)
                    return self.queue[self.cursor]
        # sequential
        if self.cursor + 1 < len(self.queue):
            self.cursor += 1
        else:
            self.cursor = 0  # 列表循环
        return self.queue[self.cursor]

    def prev_id(self) -> Optional[str]:
        if not self.queue:
            return None
        if self.cursor > 0:
            self.cursor -= 1
        else:
            self.cursor = len(self.queue) - 1
        return self.queue[self.cursor]

    def current_id(self) -> Optional[str]:
        if 0 <= self.cursor < len(self.queue):
            return self.queue[self.cursor]
        return None

    def seed_from_search(self, query: str, limit: int = 20) -> List[str]:
        hits = self.library.search(query, limit=limit)
        ids = [t.id for t in hits]
        self.set_queue(ids)
        return ids


class Player:
    """状态机 + cmd 路由 + ws 广播。"""

    def __init__(self, library: LocalLibrary, *, online: Optional["OnlineSearch"] = None,
                 volume: int = 80, playback_mode: str = "sequential"):
        self.library = library
        self.online = online
        self.playlist = PlaylistManager(library)
        self.state = PlayerState(volume=volume, playback_mode=playback_mode)
        self._subscribers: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    # ============================================================
    # 订阅
    # ============================================================
    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers.append(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    async def _publish(self, ev_type: str, payload: Dict[str, Any]) -> None:
        msg = {"type": ev_type, **payload, "ts": int(time.time() * 1000)}
        dead: List[asyncio.Queue] = []
        async with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        if dead:
            async with self._lock:
                for d in dead:
                    try:
                        self._subscribers.remove(d)
                    except ValueError:
                        pass
        # 触发外部 hook(用于自动拉歌词等副作用)
        hook = getattr(self, "_state_hook", None)
        if hook and ev_type == "music_state":
            try:
                import asyncio as _aio
                _aio.ensure_future(hook())
            except Exception:
                pass

    # ============================================================
    # cmd 路由
    # ============================================================
    async def cmd(self, action: str, **kw: Any) -> Dict[str, Any]:
        action = (action or "").strip().lower()
        try:
            if action == "play":
                return await self._cmd_play(kw.get("track_id"))
            elif action == "pause":
                self.state.status = "paused"
            elif action == "resume":
                if self.state.track:
                    self.state.status = "playing"
            elif action == "stop":
                self.state.status = "stopped"
                self.state.track = None
                self.state.progress = 0.0
            elif action == "next":
                return await self._cmd_next()
            elif action == "prev":
                return await self._cmd_prev()
            elif action == "seek":
                off = float(kw.get("offset", 0))
                self.state.progress = max(0.0, min(off, self.state.duration))
            elif action == "volume":
                self.state.volume = max(0, min(100, int(kw.get("volume", self.state.volume))))
            elif action == "mute":
                self.state.muted = bool(kw.get("on", not self.state.muted))
            elif action == "mode":
                m = kw.get("mode")
                if m in ("sequential", "shuffle", "repeat_one"):
                    self.state.playback_mode = m
            elif action == "search":
                query = kw.get("query", "")
                ids = self.playlist.seed_from_search(query)
                return {"ok": True, "action": action, "queued": len(ids), "ids": ids[:10]}
            elif action == "play_url":
                # P2.5+22:多源 fallback 入口。前端给 song_info → 后端走 lx 多源
                # musicUrl 拿直链 → 入库(source="lx:<src>")→ 自动 play。
                song_info = kw.get("song_info") or {}
                title = kw.get("title", "")
                artist = kw.get("artist", "")
                r = await self.seed_from_url(song_info, title=title, artist=artist)
                if not r.get("ok"):
                    return {"ok": False, "err": r.get("err", "play_url failed")}
                pl = await self._cmd_play(r["track_id"])
                if pl.get("ok"):
                    pl["source"] = r.get("source")
                return pl
            elif action == "random":
                # P2.5+22:队列空 fallback 用 — 库内随机 N 首,自动 play 第 1 首。
                count = int(kw.get("count", 1))
                return await self.play_random(count=count)
            elif action == "progress":
                # 前端回传真实 position
                try:
                    self.state.progress = float(kw.get("position", 0.0))
                    self.state.duration = float(kw.get("duration", self.state.duration))
                except (ValueError, TypeError):
                    pass
            else:
                return {"ok": False, "err": f"unknown action: {action}"}
            self.state.updated_at = int(time.time() * 1000)
            await self._publish("music_state", self.state.to_dict())
            return {"ok": True, "action": action, "state": self.state.to_dict()}
        except Exception as e:  # noqa: BLE001
            log.warning("[player] cmd %s fail: %s", action, e)
            return {"ok": False, "err": str(e)}

    async def _cmd_play(self, track_id: Optional[str]) -> Dict[str, Any]:
        if track_id:
            self.playlist.cursor = -1
            for i, tid in enumerate(self.playlist.queue):
                if tid == track_id:
                    self.playlist.cursor = i
                    break
            else:
                self.playlist.append(track_id)
                self.playlist.cursor = len(self.playlist.queue) - 1
        cur = self.playlist.current_id()
        if not cur:
            return {"ok": False, "err": "no track queued"}
        tr = self.library.get(cur)
        if not tr:
            return {"ok": False, "err": f"track not found: {cur}"}
        self.state.track = tr
        self.state.progress = 0.0
        self.state.duration = tr.duration
        self.state.status = "playing"
        self.state.updated_at = int(time.time() * 1000)
        await self._publish("music_state", self.state.to_dict())
        return {"ok": True, "action": "play", "state": self.state.to_dict()}

    # ============================================================
    # P2.5+22(2026-10-03):多源 fallback + 随机播放
    # ============================================================
    async def seed_from_url(self, song_info: Dict[str, Any],
                            title: str = "", artist: str = "") -> Dict[str, Any]:
        """走 online 多源(musicUrl)拿 mp3 直链 → 入 library(source="lx:<src>")。

        Args:
            song_info: 给 lx 的 musicInfo 字段(hash/songmid)。
            title/artist: 入库的展示名(可选)。
        Returns:
            {"ok": bool, "track_id"?: str, "source"?: str, "url"?: str, "err"?: str}
        """
        if not self.online:
            return {"ok": False, "err": "online client not configured"}
        r = self.online.get_url_multi(song_info)
        if not r.get("ok"):
            return {"ok": False, "err": r.get("err", "no url")}
        url = r.get("url", "")
        src = r.get("source", "lx")
        # id 由 url 哈希定(同 url 同 id)
        import hashlib
        tid = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        # 入库(若已存在则覆盖 path/url)
        t = Track(
            id=tid,
            title=title or song_info.get("title") or "未知曲目",
            artist=artist or song_info.get("artist") or src,
            album="",
            path=url,            # source="lx" 时 path=url,stream 端识别后透传
            duration=0.0,
            source=f"lx:{src}",
        )
        self.library.add_track(t)
        return {"ok": True, "track_id": tid, "source": src, "url": url}

    async def play_random(self, count: int = 1) -> Dict[str, Any]:
        """队列空 fallback 用:从本地库随机抽 N 首,自动 play 第 1 首。

        不依赖外部源(纯本地),失败返 ok=False(库 0 首)。
        """
        ids = self.library.random_track_ids(limit=max(1, count))
        if not ids:
            return {"ok": False, "err": "library empty"}
        self.playlist.set_queue(ids)
        return await self._cmd_play(ids[0])

    async def _cmd_next(self) -> Dict[str, Any]:
        nid = self.playlist.next_id(mode=self.state.playback_mode)
        if not nid:
            return {"ok": False, "err": "no next track"}
        return await self._cmd_play(nid)

    async def _cmd_prev(self) -> Dict[str, Any]:
        pid = self.playlist.prev_id()
        if not pid:
            return {"ok": False, "err": "no prev track"}
        return await self._cmd_play(pid)

    # ============================================================
    # 状态查询
    # ============================================================
    def snapshot(self) -> Dict[str, Any]:
        return self.state.to_dict()

    def queue_snapshot(self) -> List[Dict[str, Any]]:
        out = []
        for i, tid in enumerate(self.playlist.queue):
            tr = self.library.get(tid)
            if tr:
                d = tr.to_dict()
                d["cursor"] = (i == self.playlist.cursor)
                out.append(d)
        return out


def quick_smoke() -> Dict[str, Any]:
    """独立可跑:cd companion && python -c 'from music.player import quick_smoke; print(quick_smoke())'"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        # 写 3 个 fake mp3
        for name in ("周杰伦 - 晴天.mp3", "周杰伦 - 七里香.mp3", "陈奕迅 - 十年.mp3"):
            (td_p / name).write_bytes(b"fake")
        lib = LocalLibrary(td_p)
        tracks = lib.scan()
        pl = PlaylistManager(lib)
        pl.set_queue([t.id for t in tracks])
        cur = pl.next_id()
        return {
            "tracks": [t.title for t in tracks],
            "queue_size": len(pl.queue),
            "next_id": cur,
            "search": [t.title for t in lib.search("周杰")],
        }


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(quick_smoke(), ensure_ascii=False, indent=2))
