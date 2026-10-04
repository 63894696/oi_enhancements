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
import hashlib
import json
import logging
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set
from urllib.parse import quote

# P2.5+23(2026-10-03):download 拉远端字节流需要 aiohttp,放顶层避免函数内 inline import。
# music_web 已有 aiohttp 依赖,顶层 import 不会引入新依赖。
try:
    import aiohttp as _aiohttp_top
except Exception:  # noqa: BLE001 — 缺包时降级,_cmd_download 返错即可
    _aiohttp_top = None

log = logging.getLogger("player")

AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".aac"}

if TYPE_CHECKING:
    from music.song_pool import SongPoolCatalog  # noqa: F401


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

    def remove_track(self, track_id: str) -> bool:
        """P2.5+23 hotfix(2026-10-03):取消收藏 / 删除虚拟 track。

        Returns:
            True if removed, False if not found.
        同步清理 _by_artist(避免 search() 返回已删除 id)。
        """
        if track_id not in self._tracks:
            return False
        tr = self._tracks.pop(track_id)
        if tr.artist and tr.artist in self._by_artist:
            lst = self._by_artist[tr.artist]
            if track_id in lst:
                lst.remove(track_id)
            if not lst:
                self._by_artist.pop(tr.artist, None)
        return True

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

    # P2.5+28 C 阶段调研(2026-10-05):9 源候选列表。
    # 实际跑起来发现:shim 实现"call-all-handlers,first non-null wins",local.js 会屏蔽所有
    # P2.5+28 Y 阶段(2026-10-05):实测发现 lyswhut lx_main.js 的 5 源(kw/kg/tx/wy/mg)
    # 在 LX dispatcher + evt={action,source,info,musicInfo,type} 协议下能正确调通 QQ/网易/
    # 酷我/酷狗/咪咕 API,但所有 5 源都被 CDN 返 101404 fnameHitCache_404 区域屏蔽,
    # 拿不到真实歌曲 URL(只能拿 30 秒 preview)。真正能 deliver mp3 的是 huibq.js(89 行可审计,
    # 走 lxmusicapi.onrender.com 公共 3rd-party API + share-v3 token,返真 mp3 CDN URL)。
    # DEFAULT_SOURCES 是 LX sub-source 名(给 dispatcher 派单用,不是文件名):
    #   - 'local' → local.js(本地 mp3 占位 fallback)
    #   - 'tx'/'kw'/'wy'/'kg'/'mg' → huibq.js 内的 5 子源
    # LxRuntimeClient 启动时按需把 huibq.js 装进 Node 子进程。
    DEFAULT_SOURCES = ["local", "tx", "kw", "wy", "kg", "mg"]

    def __init__(self, sources: Optional[List[str]] = None):
        # 2026-10-04:默认 local-only(0 外网请求,0 上传)
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

        P2.5+28 Y 阶段(2026-10-05):sources 是 LX sub-source 名(local/tx/kw/wy/kg/mg),
        不是源文件名。LxRuntimeClient.LX_SOURCES 才装源文件(huibq.js)。
        轮询时按 sub-source 名派给 dispatcher,handler 再按 sub-source 名命中正确 API。
        local 没 tx/wy... 等 sub-source → 永远返 local:// 占位。
        huibq.js 注册了 tx/kw/wy/kg/mg → 这 5 个 sub-source 都能命中。

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
                 catalog: Optional["SongPoolCatalog"] = None,
                 volume: int = 80, playback_mode: str = "sequential"):
        self.library = library
        self.online = online
        # P2.5+23(2026-10-03):song pool 注入,favorite/download cmd 拿 catalog 查 title/artist。
        self.catalog = catalog
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
            elif action == "favorite":
                # P2.5+23(2026-10-03):收藏 / 取消收藏 toggle。
                # P2.5+23 hotfix(2026-10-03):已收藏再点 = 取消,前端按钮文案切。
                return await self._cmd_favorite(kw.get("track_id"))
            elif action == "is_favorite":
                # P2.5+23 hotfix:前端查当前 track 收藏状态,决定 favoriteBtn 文案(♥ 收藏 / ♥ 已收藏)。
                return self.is_favorite(kw.get("track_id"))
            elif action == "download":
                # P2.5+23(2026-10-03):下载当前 track → cache/<title>.mp3,LocalLibrary source="local"。
                return await self._cmd_download(kw.get("track_id"))
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
    # P2.5+28 A 阶段(2026-10-04):彻底删 seed.mp3 兜底。
    #   之前 _SEED_MP3 静态变量 + seed_from_url 第 3 段兜底 + preload_next_url googleapis→seed.mp3 分支
    #   全删。原因:用户原话「30 秒静音需要彻底去掉,不能播放就说明原因是什么」。
    #   后续若要再启用兜底(基本不会),需重新引入 P2.5+27 的逻辑,但 0 上传红线优先。

    async def seed_from_url(self, song_info: Dict[str, Any],
                            title: str = "", artist: str = "") -> Dict[str, Any]:
        """走 online 多源(musicUrl)拿 mp3 直链 → 入 library。

        Args:
            song_info: 给 lx 的 musicInfo 字段(hash/songmid)。
            title/artist: 入库的展示名(可选)。
        Returns:
            {"ok": bool, "track_id"?: str, "source"?: str, "url"?: str, "err"?: str,
             "title"?: str, "artist"?: str}

        路径分流(P2.5+28 A 阶段 2026-10-04):
          1) 优先:本地 library 扫描到的 mp3(LocalLibrary.scan 已包含 ~/Music 真 mp3)
             按 title/artist 模糊匹配 → 命中 → 直接入库(source="local",path=真 mp3 路径)
          2) 次优:LX 在线源(lx 多源 get_url_multi)。local.js 返 local:// → 不命中真 mp3;
             真 LX 源返 http(s) URL → 入库 lx:<src>(C 阶段才会启用外网源)
          3) 失败:不兜底,直接返 err + 清晰原因

        Returns.source 取值:
          - "local" = 命中 LocalLibrary 真 mp3
          - "lx:<src>" = 远端 URL 可达时才会有(C 阶段才走得到)
        """
        actual_title = title or song_info.get("title") or song_info.get("songname") or "未知曲目"
        actual_artist = artist or song_info.get("artist") or ""
        id_seed = f"{actual_title}|{actual_artist}|{song_info.get('hash', song_info.get('songmid', ''))}"
        tid = hashlib.sha1(id_seed.encode("utf-8")).hexdigest()[:16]

        # 2026-10-04 bug fix:顺序很关键 — 必须先查本地真 mp3,再尝试 LX 在线源,
        #   最后才兜底 seed.mp3。之前实现漏了"先查本地"步骤,导致所有歌掉 seed。
        #   P3.10b 红线:0 上传/外传。当前 LX DEFAULT_SOURCES=['local.js'],
        #   local.js 返 local:// 占位,不调任何外网,所以这一段等于"试探但不真发请求"。

        # 1) 优先:本地 library 模糊匹配 title/artist → 命中真 mp3
        local_hit = self._find_local_match(actual_title, actual_artist)
        if local_hit:
            t = Track(
                id=tid, title=actual_title, artist=actual_artist,
                album="", path=local_hit, duration=0.0, source="local",
            )
            self.library.add_track(t)
            return {"ok": True, "track_id": tid, "source": "local",
                    "url": local_hit, "matched_local": True}

        # 2) 次优:LX 在线源(lx 多源 get_url_multi)。local.js 返 local:// → 不命中真 mp3;
        #   真 LX 源(若 user 后续接 juhe/csv 私源)返 http(s) URL → 入库 lx:<src>。
        if self.online:
            try:
                r = self.online.get_url_multi(song_info)
                if r.get("ok") and r.get("url"):
                    url = r["url"]
                    src_name = r.get("source", "lx")
                    if url.startswith("local://"):
                        # local.js 显式走"无可用 URL"语义,不命中真 mp3 走兜底
                        pass
                    elif url.startswith("http://") or url.startswith("https://"):
                        # 远端 URL → 入库 lx:<src>。浏览器 fetch 受跨源限制可能失败,
                        # 但 Python aiohttp 透传 + LX 源 DNS 通了就能播。
                        t = Track(
                            id=tid, title=actual_title, artist=actual_artist,
                            album="", path=url, duration=0.0,
                            source=f"lx:{src_name}",
                        )
                        self.library.add_track(t)
                        return {"ok": True, "track_id": tid,
                                "source": f"lx:{src_name}", "url": url}
            except Exception as e:  # noqa: BLE001
                log.warning("[seed_from_url] online probe failed: %s", e)

        # 3) 没有本地命中 + 没有可用 LX URL → 返清晰错(沿用 P3.10b 0 上传红线)。
        #   P2.5+28 A 阶段:不再 seed.mp3 兜底,直接告诉用户真实原因。
        log.warning("[seed_from_url] no source for: %s - %s (local=%s, online_tried=%s)",
                    actual_title, actual_artist, bool(local_hit),
                    bool(self.online))
        return {
            "ok": False,
            "err": (f"无法播放「{actual_title} - {actual_artist}」:"
                    f"本地 ~/Music 无匹配 mp3,在线源不可达"
                    f"(沿用 P3.10b 0 上传红线,未接外网 LX API;C 阶段接新源后可播)"),
            "title": actual_title,
            "artist": actual_artist,
        }

    def _find_local_match(self, title: str, artist: str) -> Optional[str]:
        """P0 bug fix(2026-10-04):在 LocalLibrary 已扫到的本地 mp3 里找匹配 title/artist。

        匹配规则:
          1) 同 artist + 同 title(精确,不区分大小写)
          2) 同 title(忽略 artist)
          3) title 含/被含(模糊)
          4) path 里含 title / artist

        命中返绝对路径,无命中返 None。
        """
        if not title:
            return None
        t_low = title.lower().strip()
        a_low = (artist or "").lower().strip()
        candidates: list[tuple[int, str]] = []
        for tr in self.library._tracks.values():
            if tr.source != "local" or not tr.path or tr.path.endswith("seed.mp3"):
                # seed.mp3 跳过(已在兜底处理),且非 local source 的跳过
                continue
            p = Path(tr.path)
            if not p.exists():
                continue
            tr_title = (tr.title or "").lower().strip()
            tr_artist = (tr.artist or "").lower().strip()
            p_low = str(p).lower()
            # 1) 同 artist + 同 title
            if a_low and tr_artist == a_low and tr_title == t_low:
                return str(p)
            # 2) 同 title(忽略 artist)
            if tr_title == t_low and t_low:
                candidates.append((1, str(p)))
            # 3) title 含/被含
            elif t_low and tr_title and (t_low in tr_title or tr_title in t_low):
                candidates.append((2, str(p)))
            # 4) path 含 title
            elif t_low and t_low in p_low:
                candidates.append((3, str(p)))
        # 取最低 score(1 > 2 > 3),同分取首条
        if candidates:
            candidates.sort(key=lambda x: x[0])
            return candidates[0][1]
        return None

    async def play_random(self, count: int = 1) -> Dict[str, Any]:
        """队列空 fallback 用:从本地库随机抽 N 首,自动 play 第 1 首。

        不依赖外部源(纯本地),失败返 ok=False(库 0 首)。
        """
        ids = self.library.random_track_ids(limit=max(1, count))
        if not ids:
            return {"ok": False, "err": "library empty"}
        self.playlist.set_queue(ids)
        return await self._cmd_play(ids[0])

    # ============================================================
    # P2.5+23(2026-10-03):收藏 / 下载
    # ============================================================
    async def _cmd_favorite(self, track_id: Optional[str]) -> Dict[str, Any]:
        """P2.5+23(2026-10-03):收藏 / 取消收藏 toggle。

        语义:已存在 source='song_pool_fav' 且同 title/artist → 删除返 unfavorited=True;
              否则注入新收藏返 favorited=True。

        同 track 收藏 idempotent,但 toggle 后再次点击 = 取消收藏(用户原话)。

        Returns:
                {"ok": bool, "favorited"?: bool, "fav_id"?: str, "title"?: str, "err"?: str}
        """
        if not track_id:
            return {"ok": False, "err": "missing track_id"}
        tr = self.library.get(track_id)
        if not tr:
            return {"ok": False, "err": f"track not found: {track_id}"}
        # toggle:已收藏则删,未收藏则加
        for existing in self.library._tracks.values():
            if (existing.source == "song_pool_fav"
                    and existing.title == tr.title
                    and existing.artist == tr.artist):
                self.library.remove_track(existing.id)
                return {"ok": True, "favorited": False, "fav_id": existing.id,
                        "title": existing.title, "unfavorited": True}
        fav_id = hashlib.sha1(f"fav::{tr.id}".encode("utf-8")).hexdigest()[:16]
        fav = Track(
            id=fav_id,
            title=tr.title,
            artist=tr.artist,
            album=tr.album,
            path=tr.path,
            duration=tr.duration,
            source="song_pool_fav",
        )
        self.library.add_track(fav)
        return {"ok": True, "favorited": True, "fav_id": fav_id, "title": fav.title}

    def is_favorite(self, track_id: Optional[str]) -> Dict[str, Any]:
        """P2.5+23 hotfix(2026-10-03):前端查当前 track 是否已收藏 → 决定 favoriteBtn 文案。

        Returns:
            {"ok": True, "favorited": bool, "fav_id"?: str}
        """
        if not track_id:
            return {"ok": True, "favorited": False}
        tr = self.library.get(track_id)
        if not tr:
            return {"ok": True, "favorited": False}
        for existing in self.library._tracks.values():
            if (existing.source == "song_pool_fav"
                    and existing.title == tr.title
                    and existing.artist == tr.artist):
                return {"ok": True, "favorited": True, "fav_id": existing.id}
        return {"ok": True, "favorited": False}

    # ============================================================
    # P3.3(2026-10-03):长按收藏菜单 — list_favorites / remove_favorite
    # ============================================================
    def list_favorites(self, limit: int = 50) -> List[Track]:
        """P3.3(2026-10-03):返所有 source='song_pool_fav' 的收藏 track。

        按 _tracks dict 插入顺序(Python 3.7+ 保持),新加入收藏在列表尾部。
        Returns: List[Track] (最长 limit 条,默认 50 防 toast 太长)
        """
        if not self.library:
            return []
        favs = [t for t in self.library._tracks.values() if t.source == "song_pool_fav"]
        return favs[: max(1, int(limit))]

    def remove_favorite(self, fav_id: str) -> Dict[str, Any]:
        """P3.3(2026-10-03):按 fav_id 删收藏。

        Returns: {ok: bool, removed?: Track.to_dict(), err?: str}
        """
        if not fav_id:
            return {"ok": False, "err": "missing fav_id"}
        tr = self.library.get(fav_id) if self.library else None
        if not tr:
            return {"ok": False, "err": f"track not found: {fav_id}"}
        if tr.source != "song_pool_fav":
            return {"ok": False, "err": "not a favorite"}
        self.library.remove_track(fav_id)
        return {"ok": True, "removed": tr.to_dict()}

    async def _cmd_download(self, track_id: Optional[str]) -> Dict[str, Any]:
        """下载当前 track 到 companion/music/cache/ → LocalLibrary 加 source='local' 入库。
        remote(lx: 开头):aiohttp 拉字节 → 写文件
        本地:直接 copy2
        Returns:
                {"ok": bool, "local_id"?: str, "path"?: str, "title"?: str, "err"?: str}
        """
        if not track_id:
            return {"ok": False, "err": "missing track_id"}
        tr = self.library.get(track_id)
        if not tr:
            # P3.6(2026-10-04):下载完成广播 — 失败也 publish,renderer 弹 error toast
            await self._publish("download_done", {"track_id": track_id, "ok": False,
                                                  "err": f"track not found: {track_id}"})
            return {"ok": False, "err": f"track not found: {track_id}"}
        cache_dir = Path(__file__).resolve().parent / "cache"
        try:
            cache_dir.mkdir(exist_ok=True)
        except Exception as e:  # noqa: BLE001
            await self._publish("download_done", {"track_id": track_id, "title": tr.title,
                                                  "ok": False, "err": f"mkdir cache: {e}"})
            return {"ok": False, "err": f"mkdir cache: {e}"}
        # 文件名清洗
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", tr.title)[:60].strip() or "untitled"
        dst = cache_dir / f"{safe}.mp3"
        is_remote = isinstance(tr.source, str) and tr.source.startswith("lx:")
        try:
            if is_remote:
                # aiohttp 拉上游字节流,落本地文件
                if _aiohttp_top is None:
                    await self._publish("download_done", {"track_id": track_id, "title": tr.title,
                                                          "ok": False, "err": "aiohttp not available"})
                    return {"ok": False, "err": "aiohttp not available"}
                async with _aiohttp_top.ClientSession() as sess:
                    async with sess.get(tr.path,
                                            timeout=_aiohttp_top.ClientTimeout(total=60)) as r:
                        if r.status >= 400:
                            await self._publish("download_done", {"track_id": track_id,
                                                                  "title": tr.title,
                                                                  "ok": False,
                                                                  "err": f"upstream {r.status}"})
                            return {"ok": False, "err": f"upstream {r.status}"}
                        with open(dst, "wb") as f:
                            async for chunk in r.content.iter_chunked(64 * 1024):
                                if chunk:
                                    f.write(chunk)
            else:
                src = Path(tr.path)
                if not src.exists():
                    await self._publish("download_done", {"track_id": track_id, "title": tr.title,
                                                          "ok": False, "err": "source file missing"})
                    return {"ok": False, "err": "source file missing"}
                import shutil
                shutil.copy2(src, dst)
        except Exception as e:  # noqa: BLE001
            await self._publish("download_done", {"track_id": track_id, "title": tr.title,
                                                  "ok": False, "err": f"download: {e}"})
            return {"ok": False, "err": f"download: {e}"}
        # 入库为本地
        local_id = hashlib.sha1(str(dst).encode("utf-8")).hexdigest()[:16]
        self.library.add_track(Track(
            id=local_id, title=tr.title, artist=tr.artist, album=tr.album,
            path=str(dst), duration=tr.duration, source="local",
        ))
        size = dst.stat().st_size
        # P3.6(2026-10-04):下载成功广播 → renderer 弹 info toast
        await self._publish("download_done", {"track_id": track_id, "title": tr.title,
                                              "artist": tr.artist, "ok": True,
                                              "local_id": local_id, "path": str(dst),
                                              "size": size})
        return {"ok": True, "local_id": local_id, "path": str(dst),
                "title": tr.title, "size": size}

    async def _cmd_next(self) -> Dict[str, Any]:
        nid = self.playlist.next_id(mode=self.state.playback_mode)
        if not nid:
            return {"ok": False, "err": "no next track"}
        return await self._cmd_play(nid)

    # ============================================================
    # P2.5+24(2026-10-03):预取下一首 URL — 借鉴 LX usePreloadNextMusic。
    # 前端 audio 结束前 ~10s 调,后端提前解析下一首 url,
    # 前端切歌时直接拿缓存 url,避免切歌卡顿。
    # ============================================================
    async def preload_next_url(self, current_track_id: str) -> Dict[str, Any]:
        """P2.5+24(2026-10-03):预取下一首 URL。

        Args:
            current_track_id: 当前播的 track_id(仅用于日志调试,实际走 playlist 下一首)
        Returns:
            {"ok": bool, "track_id"?: str, "title"?: str,
             "url"?: str, "source"?: str,
             "stream_url"?: str, "err"?: str}
        """
        # 走 state 决定下一首 id(current_track_id 仅日志)
        nid = self.playlist.next_id(mode=self.state.playback_mode)
        if not nid:
            return {"ok": False, "err": "no next track in queue"}
        tr = self.library.get(nid)
        if not tr:
            return {"ok": False, "err": f"track not found: {nid}"}
        # 已下载本地文件 → 直接返本地 stream URL,不需解析 url
        if tr.source == "local" and Path(tr.path).exists():
            return {
                "ok": True,
                "track_id": nid,
                "title": tr.title,
                "artist": tr.artist,
                "url": None,
                "source": "local",
                "stream_url": f"/api/stream/{quote(nid, safe='')}",
            }
        # 远端(lx: 前缀):需要解析 url
        if isinstance(tr.source, str) and tr.source.startswith("lx:"):
            if not self.online:
                return {"ok": False, "err": "online client not configured"}
            song_info = {
                "hash": nid,
                "songmid": nid,
                "songname": tr.title,
                "singer": tr.artist,
            }
            r = self.online.get_url_multi(song_info)
            if not r.get("ok"):
                return {"ok": False, "err": r.get("err", "no url from any source")}
            url = r["url"]
            # P2.5+28 A 阶段(2026-10-04):googleapis 不可达不再兜底 seed.mp3,直接返错。
            # 用户原话「30 秒静音需要彻底去掉,不能播放就说明原因是什么」。
            is_googleapis = "googleapis.com" in url
            if is_googleapis:
                return {
                    "ok": False,
                    "track_id": nid,
                    "title": tr.title,
                    "artist": tr.artist,
                    "err": (f"upstream {url.split('/')[2]} 不可达"
                            f"(googleapis 国内 DNS 通常不通),C 阶段接新源后可播"),
                }
            return {
                "ok": True,
                "track_id": nid,
                "title": tr.title,
                "artist": tr.artist,
                "url": url,
                "source": r["source"],
                "stream_url": f"/api/stream/{quote(nid, safe='')}",
            }
        # 其它 source(比如 song_pool_fav 也可能 path=url):直接返
        return {
            "ok": True,
            "track_id": nid,
            "title": tr.title,
            "artist": tr.artist,
            "url": tr.path if tr.path.startswith("http") else None,
            "source": tr.source,
            "stream_url": f"/api/stream/{quote(nid, safe='')}",
        }

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
