# -*- coding: utf-8 -*-
"""
prisiragent-music-web.py — M3.29.1 music web 后端主入口

启动:
  python prisiragent-music-web.py [--port 0] [--host 127.0.0.1] [--root ~/Music]

绑定 0 → 自动分配端口 → 写 HKCU 注册表 + _prisir_registry/music_port.json
Tauri 壳 + companion 都通过 read_music_port() 探测本服务。

路由:
  GET  /api/health                       - 健康检查
  GET  /api/state                        - 当前 player state
  GET  /api/queue                        - 队列快照
  GET  /api/library                      - 扫库(返回 track 列表)
  GET  /api/library/search?q=...         - 搜索
  GET  /api/stream/{track_id}            - 流代理(返回 mp3 字节)
  POST /api/cmd                          - cmd 路由(play/pause/next/.../search)
  GET  /api/agent/cfg/list               - 列全部 agent 配置
  GET  /api/agent/cfg/get?path=...       - 读单个
  POST /api/agent/cfg/set                - 写单个/批量
  POST /api/agent/intent                 - 自然语言意图(text=...)
  GET  /api/lyric?title=&artist=&album=  - 拉歌词
  GET  /ws/state                         - ws 状态广播
  GET  /ws/lyrics                        - ws 歌词广播 + cfg 推
  GET  /lyrics                           - 桌面歌词页(独立 HTML,供 Tauri 透明窗加载)
  GET  /                                 - 主页(music web 前端)

端口分配 + 跨进程: music.port_registry
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import aiohttp
from aiohttp import web

# 让 music/* 子模块可被 import
_COMPANION_DIR = Path(__file__).resolve().parent
if str(_COMPANION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPANION_DIR))

from music.agent_cfg import AgentCfg, SCHEMA, DEFAULTS, validate  # noqa: E402
from music.lyric_provider import LyricProvider  # noqa: E402
from music.player import (  # noqa: E402
    AudioBackend, LocalLibrary, OnlineSearch, Player, Track,
)
from music.song_pool import SongPoolCatalog  # noqa: E402
# N9(2026-10-04)AI 歌单推荐:从 SongPoolCatalog 聚合 favorites/play_history + 冷启动均匀
from music.recommender import recommend_from_catalog  # noqa: E402
from music.port_registry import (  # noqa: E402
    is_music_alive, pick_free_port as _legacy_pick_free_port, read_music_port, write_music_port,
)
# M3.34(2026-09-19)端口统一:music 也走 port_config,与 web/companion 共用 HKCU/JSON 通道。
# 保留 port_registry 的 is_music_alive / read_music_port(对外协议:返 (port,pid) 元组)。
from music.port_config import (  # noqa: E402
    read_port as _pc_read_port,
    write_port as _pc_write_port,
    pick_free_port as _pc_pick_free_port,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("music_web")

# 强制 UTF-8(Windows 中文路径)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ============================================================
# 全局状态
# ============================================================
class AppState:
    def __init__(self):
        self.host: str = "127.0.0.1"
        self.port: int = 0
        self.library: Optional[LocalLibrary] = None
        self.online: Optional[OnlineSearch] = None
        self.player: Optional[Player] = None
        # P2.5+23(2026-10-03):真歌名池 — 用户填 CSV,启动时随机洗牌 60 首。
        self.song_pool: Optional[SongPoolCatalog] = None
        self.cfg: Optional[AgentCfg] = None
        self.lyric: Optional[LyricProvider] = None
        self.ws_state_subs: List[asyncio.Queue] = []
        self.ws_lyric_subs: List[asyncio.Queue] = []
        self.ws_lock: Optional[asyncio.Lock] = None
        # 当前曲目歌词缓存
        self._cur_lyric_lines: List[Dict[str, Any]] = []
        self._cur_lyric_idx: int = -1
        self._cur_lyric_source: str = ""
        self._cur_track_id: str = ""


APP = AppState()


# ============================================================
# 工具
# ============================================================
def _ok(**kw: Any) -> web.Response:
    return web.json_response({"ok": True, **kw})


def _err(msg: str, **kw: Any) -> web.Response:
    return web.json_response({"ok": False, "err": msg, **kw})


async def _publish_state(ev_type: str, payload: Dict[str, Any]) -> None:
    msg = {"type": ev_type, **payload, "ts": int(time.time() * 1000)}
    dead = []
    async with APP.ws_lock:
        subs = list(APP.ws_state_subs)
    for q in subs:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    if dead:
        async with APP.ws_lock:
            for d in dead:
                try:
                    APP.ws_state_subs.remove(d)
                except ValueError:
                    pass


async def _publish_lyric(ev_type: str, payload: Dict[str, Any]) -> None:
    msg = {"type": ev_type, **payload, "ts": int(time.time() * 1000)}
    dead = []
    async with APP.ws_lock:
        subs = list(APP.ws_lyric_subs)
    for q in subs:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    if dead:
        async with APP.ws_lock:
            for d in dead:
                try:
                    APP.ws_lyric_subs.remove(d)
                except ValueError:
                    pass


def _cfg_to_lyric_dict() -> Dict[str, Any]:
    cfg = APP.cfg.get_all()
    return {
        "color": cfg.get("lyrics.color", "#f6f1e7"),
        "font_size": cfg.get("lyrics.font_size", 32),
        "mode": cfg.get("lyrics.mode", "line"),
        "delay_ms": cfg.get("lyrics.delay_ms", 0),
        "opacity": cfg.get("lyrics.opacity", 0.85),
        "window_visible": cfg.get("lyrics.window_visible", True),
    }


# ============================================================
# 路由
# ============================================================
async def api_health(req: web.Request) -> web.Response:
    """健康探针 — 前端 LX 探测灯用。

    P2.5+23 hotfix(2026-10-03):lx online client 状态返前端,前端不再一直灰灯。
    字段:
      - online.configured: bool,APP.online 是否初始化
      - online.initialized: bool,LxRuntimeClient 实际实例化(jsdom shim 已跑)
      - online.sources: list[str],实际注册的多源
      - seed_fallback: bool,companion/static/music/seed.mp3 是否存在(googleapis 不可达兜底)
    """
    online = APP.online
    online_info: Dict[str, Any] = {
        "configured": bool(online),
        "initialized": False,
        "sources": list(online._sources) if online else [],
    }
    if online:
        cli = online._ensure()
        online_info["initialized"] = bool(cli)

    # P2.5+23 hotfix:seed.mp3 本地兜底文件状态
    seed_path = STATIC_DIR / "music" / "seed.mp3"
    seed_fallback = seed_path.exists() and seed_path.stat().st_size > 0

    return _ok(
        service="prisiragent-music-web",
        version="0.1.0",
        port=APP.port,
        library=bool(APP.library and APP.library._tracks),
        online=online_info,
        seed_fallback=seed_fallback,
    )


async def api_state(req: web.Request) -> web.Response:
    if not APP.player:
        return _err("player not initialized")
    return _ok(state=APP.player.snapshot())


async def api_queue(req: web.Request) -> web.Response:
    if not APP.player:
        return _err("player not initialized")
    return _ok(queue=APP.player.queue_snapshot(), cursor=APP.player.playlist.cursor)


async def api_library(req: web.Request) -> web.Response:
    if not APP.library:
        return _err("library not initialized")
    tracks = list(APP.library._tracks.values())
    return _ok(count=len(tracks), tracks=[t.to_dict() for t in tracks[:500]])


async def api_library_search(req: web.Request) -> web.Response:
    if not APP.library:
        return _err("library not initialized")
    q = req.query.get("q", "")
    limit = int(req.query.get("limit", "20"))
    hits = APP.library.search(q, limit=limit)
    return _ok(q=q, count=len(hits), tracks=[t.to_dict() for t in hits])


# ============================================================
# P2.5+23(2026-10-03):真歌名池 API
# ============================================================
async def api_songs(req: web.Request) -> web.Response:
    """GET /api/songs[?tag=流行&tag=摇滚][&q=周杰伦]
    P3.7(2026-10-04):多 tag OR 合并 + title/artist 模糊搜索。
    返当前可见歌名池(默认 60 首)+ tags 列表(全 14 tag)。
    """
    if not APP.song_pool:
        return _err("song_pool not initialized")
    # 多 tag:getall('tag') 收 ?tag=a&tag=b;同时兼容旧 ?tag=xxx 单值(已在 List 内)
    raw_tags = req.query.getall("tag", [])
    sel_tags = [t for t in (x.strip() for x in raw_tags) if t]
    q = (req.query.get("q") or "").strip()
    songs = APP.song_pool.list_visible(tags=sel_tags, q=q)
    return _ok(
        count=len(songs),
        total=len(APP.song_pool.all_songs),
        # P3.7:已选 tag 数组 + 搜索词回显(便于前端调试)
        sel_tags=sel_tags,
        q=q,
        # P3.7:全 14 tag 列表(MusicView 仍按 `tags` 字段读 — 保持 P2.5+24 兼容)
        tags=APP.song_pool.list_tags(),
        songs=[s.to_dict() for s in songs],
    )


async def api_songs_respin(req: web.Request) -> web.Response:
    """POST /api/songs/respin — 重新洗牌 60 首可见。"""
    if not APP.song_pool:
        return _err("song_pool not initialized")
    APP.song_pool.shuffle()
    return _ok(count=len(APP.song_pool.visible_songs))


# ============================================================
# N9(2026-10-04)AI 歌单推荐
# ============================================================
async def api_recommend(req: web.Request) -> web.Response:
    """GET /api/recommend?k=20&seed=None

    N9:从 SongPoolCatalog 聚合 favorites/play_history,复用 recommend_poc 启发式打分,
    冷启动(无收藏 + 无 30 天内播放)→ 14 tag 均匀分布 + random.shuffle。

    Returns:
        {
          "ok": True,
          "count": int,           # 实际返的推荐数(可能 < k)
          "k": int,               # 请求的 k
          "seed": int|None,
          "is_cold_start": bool,
          "items": [
            {"id": "sp001", "title": "...", "artist": "...", "tag": "...",
             "score": 0.83, "reason": "你收藏过 5 首流行"},
            ...
          ]
        }
    """
    if not APP.song_pool:
        return _err("song_pool not initialized")
    try:
        k = max(1, min(50, int(req.query.get("k", "20"))))
    except (ValueError, TypeError):
        k = 20
    seed_raw = (req.query.get("seed") or "").strip()
    seed: int | None = None
    if seed_raw:
        try:
            seed = int(seed_raw)
        except (ValueError, TypeError):
            seed = None

    items = recommend_from_catalog(APP.song_pool, k=k, seed=seed)
    is_cold = (len(items) > 0
               and items[0].get("reason", "").startswith("冷启动"))
    return _ok(
        count=len(items),
        k=k,
        seed=seed,
        is_cold_start=is_cold,
        items=items,
    )


async def api_songs_preload(req: web.Request) -> web.Response:
    """P2.5+24(2026-10-03):预取下一首 URL — 借鉴 LX usePreloadNextMusic。

    前端 audio 结束前 ~10s 调,后端提前解析 url 缓存,前端切歌时不卡顿。
    """
    if not APP.player:
        return _err("player not initialized")
    current_id = req.query.get("current_track_id", "")
    r = await APP.player.preload_next_url(current_id)
    return web.json_response(r)


async def api_stream(req: web.Request) -> web.StreamResponse:
    """流代理:返回 mp3 字节。

    HTMLAudioElement 直接连这个 URL,无需 CORS(same-origin)。
    P2.5+22(2026-10-03):track.source 以 "lx:" 开头 → path 视为远程 url,代理透传
    (不做 Range 支持,浏览器 audio seek 不可用但能播)。
    """
    tid = req.match_info.get("track_id", "")
    if not APP.library:
        return _err("library not initialized")
    tr = APP.library.get(tid)
    if not tr:
        return _err(f"track not found: {tid}")
    is_remote = isinstance(tr.source, str) and tr.source.startswith("lx:")
    if is_remote:
        return await _stream_remote_url(req, tr.path)
    p = Path(tr.path)
    if not p.exists():
        return _err(f"track file missing: {tid}")
    size = p.stat().st_size
    # Range header 支持(浏览器 audio seek 用)
    range_hdr = req.headers.get("Range")
    start = 0
    end = size - 1
    if range_hdr:
        m = range_hdr.replace("bytes=", "").split("-")
        if m[0]:
            start = int(m[0])
        if len(m) > 1 and m[1]:
            end = int(m[1])
        end = min(end, size - 1)
    chunk = end - start + 1

    ctype, _ = mimetypes.guess_type(str(p))
    ctype = ctype or "audio/mpeg"
    headers = {
        "Content-Type": ctype,
        "Content-Length": str(chunk),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    if range_hdr:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        status = 206
    else:
        status = 200

    resp = web.StreamResponse(status=status, headers=headers)
    await resp.prepare(req)

    with p.open("rb") as f:
        if start:
            f.seek(start)
        remaining = chunk
        while remaining > 0:
            buf = f.read(min(64 * 1024, remaining))
            if not buf:
                break
            await resp.write(buf)
            remaining -= len(buf)
    await resp.write_eof()
    return resp


async def _stream_remote_url(req: web.Request, url: str) -> web.StreamResponse:
    """P2.5+22:lx 远端 url 透传到浏览器。Range 不支持,只做 streaming proxy。"""
    import aiohttp as _aio
    ctype = "audio/mpeg"
    try:
        async with _aio.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=60)) as upstream:
                if upstream.status >= 400:
                    return _err(f"upstream {upstream.status}")
                # 尝试从 content-type 拿 ctype
                ctype = upstream.headers.get("Content-Type", ctype)
                resp = web.StreamResponse(
                    status=200,
                    headers={
                        "Content-Type": ctype,
                        "Cache-Control": "no-store",
                        "Access-Control-Allow-Origin": "*",
                    },
                )
                await resp.prepare(req)
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    if not chunk:
                        break
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
    except Exception as e:  # noqa: BLE001
        log.warning("[stream_remote] %s fail: %s", url[:80], e)
        return _err(f"remote stream failed: {type(e).__name__}: {e}")


async def api_cmd(req: web.Request) -> web.Response:
    if not APP.player:
        return _err("player not initialized")
    try:
        body = await req.json()
    except Exception:
        body = {}
    action = body.get("action", "")
    kw = {k: v for k, v in body.items() if k != "action"}
    cmd_res = await APP.player.cmd(action, **kw)
    # P3.6(2026-10-04):下载完成广播透传到 ws_state 客户端
    # player._publish 走 player._subscribers(内部队列),与 web 层 ws_state_subs 是两套;
    # renderer 是 web 层 ws 客户端,需 web 层显式 _publish_state 才会收到 download_done。
    if action == "download" and isinstance(cmd_res, dict):
        payload = {"track_id": kw.get("track_id"), **cmd_res}
        await _publish_state("download_done", payload)
    return web.json_response(cmd_res)


async def api_agent_cfg_list(req: web.Request) -> web.Response:
    if not APP.cfg:
        return _err("cfg not initialized")
    return _ok(items=APP.cfg.list())


async def api_agent_cfg_get(req: web.Request) -> web.Response:
    if not APP.cfg:
        return _err("cfg not initialized")
    path = req.query.get("path", "")
    if not path:
        return _err("missing path")
    if path not in SCHEMA:
        return _err(f"unknown path: {path}")
    return _ok(path=path, value=APP.cfg.get(path))


async def api_agent_cfg_set(req: web.Request) -> web.Response:
    if not APP.cfg:
        return _err("cfg not initialized")
    try:
        body = await req.json()
    except Exception:
        return _err("invalid json body")
    results = {}
    changed: Dict[str, Any] = {}
    for k, v in body.items():
        ok, err = APP.cfg.set(k, v)
        results[k] = {"ok": ok, "err": err}
        if ok:
            changed[k] = APP.cfg.get(k)
    # 触发 lyric 推送(歌词相关 cfg 变化)
    if any(k.startswith("lyrics.") for k in changed):
        await _publish_lyric("lyric_cfg", _cfg_to_lyric_dict())
    return _ok(results=results, changed=changed)


async def api_agent_intent(req: web.Request) -> web.Response:
    if not APP.cfg:
        return _err("cfg not initialized")
    try:
        body = await req.json()
    except Exception:
        body = {}
    text = body.get("text", "") if isinstance(body, dict) else ""
    res = APP.cfg.handle_intent(text)
    if not res.get("matched"):
        return _ok(**res, fallback_to_llm=True)
    # 执行
    action = res["action"]
    payload = res["payload"]
    if action == "set":
        results = {}
        for k, v in payload.items():
            ok, err = APP.cfg.set(k, v)
            results[k] = {"ok": ok, "err": err}
        if any(k.startswith("lyrics.") for k in payload):
            await _publish_lyric("lyric_cfg", _cfg_to_lyric_dict())
        return _ok(intent=res, executed=results)
    elif action == "cmd":
        if not APP.player:
            return _err("player not initialized")
        cmd_action = payload.get("action", "")
        cmd_kw = {k: v for k, v in payload.items() if k != "action"}
        cmd_res = await APP.player.cmd(cmd_action, **cmd_kw)
        return _ok(intent=res, executed=cmd_res)
    return _err(f"unknown intent action: {action}")


# ============================================================
# P3.3(2026-10-03):长按收藏菜单 — /api/favorites 系列
# ============================================================
async def api_favorites(req: web.Request) -> web.Response:
    """P3.3(2026-10-03):列出所有 source='song_pool_fav' 收藏(供 Toast 列表)。"""
    if not APP.player:
        return _err("player not initialized")
    favs = APP.player.list_favorites(limit=50)
    return _ok(
        count=len(favs),
        favorites=[{
            "fav_id": t.id,
            "title": t.title,
            "artist": t.artist,
            "duration": t.duration,
        } for t in favs],
    )


async def api_favorite_remove(req: web.Request) -> web.Response:
    """P3.3(2026-10-03):按 fav_id 删收藏(走 POST + ?fav_id= query,前端 fetch 简洁)。"""
    if not APP.player:
        return _err("player not initialized")
    fav_id = req.query.get("fav_id", "").strip()
    if not fav_id:
        return _err("missing fav_id")
    return web.json_response(APP.player.remove_favorite(fav_id))


async def api_lyric(req: web.Request) -> web.Response:
    if not APP.lyric:
        return _err("lyric provider not initialized")
    title = req.query.get("title", "")
    artist = req.query.get("artist", "")
    album = req.query.get("album", "")
    if not title:
        return _err("missing title")
    res = await APP.lyric.get(title=title, artist=artist, album=album)
    out = res.to_dict()
    if res.ok and res.raw:
        from lyric_loader import parse_lrc
        lines = parse_lrc(res.raw)
        out["lines"] = [
            {"time_ms": ll.time_ms, "text": ll.text,
             "words": [{"time_ms": w.time_ms, "text": w.text} for w in (ll.words or [])]}
            for ll in lines
        ]
    return _ok(**out)


# ============================================================
# ws
# ============================================================
async def ws_state(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    q = asyncio.Queue(maxsize=200)
    async with APP.ws_lock:
        APP.ws_state_subs.append(q)
    log.info("[ws_state] sub added, total=%d", len(APP.ws_state_subs))
    try:
        # 立即推一帧 snapshot
        if APP.player:
            try:
                await ws.send_json({"type": "music_state", **APP.player.snapshot(),
                                    "ts": int(time.time() * 1000)})
            except Exception:
                pass
        while not ws.closed:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=1.0)
                if ws.closed:
                    break
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                if ws.closed:
                    break
                try:
                    await ws.send_json({"type": "heartbeat", "ts": int(time.time() * 1000)})
                except Exception:
                    break
            except Exception as e:
                log.warning("[ws_state] err: %s", e)
                break
    finally:
        async with APP.ws_lock:
            try:
                APP.ws_state_subs.remove(q)
            except ValueError:
                pass
    return ws


async def ws_lyrics(req: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(req)
    q = asyncio.Queue(maxsize=200)
    async with APP.ws_lock:
        APP.ws_lyric_subs.append(q)
    log.info("[ws_lyrics] sub added, total=%d", len(APP.ws_lyric_subs))
    try:
        # 立即推一帧 cfg + 当前 lyric
        try:
            await ws.send_json({"type": "lyric_cfg", **_cfg_to_lyric_dict(),
                                "ts": int(time.time() * 1000)})
        except Exception:
            pass
        if APP._cur_lyric_lines and not ws.closed:
            try:
                await ws.send_json({
                    "type": "lyric_line",
                    "lines": APP._cur_lyric_lines,
                    "current_idx": APP._cur_lyric_idx,
                    "source": APP._cur_lyric_source,
                    "ts": int(time.time() * 1000),
                })
            except Exception:
                pass
        while not ws.closed:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=1.0)
                if ws.closed:
                    break
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                if ws.closed:
                    break
                try:
                    await ws.send_json({"type": "heartbeat", "ts": int(time.time() * 1000)})
                except Exception:
                    break
            except Exception as e:
                log.warning("[ws_lyrics] err: %s", e)
                break
    finally:
        async with APP.ws_lock:
            try:
                APP.ws_lyric_subs.remove(q)
            except ValueError:
                pass
    return ws


# ============================================================
# 静态文件
# ============================================================
STATIC_DIR = _COMPANION_DIR / "static"


async def index(req: web.Request) -> web.Response:
    # P2.5+24(2026-10-03):优先 serve vite build 产物(Vue 3 + Pinia 新前端),
    # 不存在则 fallback 老版 static/music/index.html(vanilla JS / old UI)。
    p = STATIC_DIR / "music-vue" / "dist" / "index.html"
    if not p.exists():
        p = STATIC_DIR / "music" / "index.html"
        if not p.exists():
            return web.Response(text="music/index.html not found", status=404)
    # P2.5+23 hotfix:ship 后用户实测右键开音乐子窗界面没变 → 浏览器缓存了旧版。
    # 加 no-store 强制每次重拉,避免 ship 后用户看不到新前端。
    return web.Response(text=p.read_text(encoding="utf-8"),
                        content_type="text/html",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})


async def lyrics_page(req: web.Request) -> web.Response:
    """桌面歌词独立页(Tauri 透明窗加载)。"""
    p = STATIC_DIR / "music" / "lyrics.html"
    if not p.exists():
        return web.Response(text="music/lyrics.html not found", status=404)
    return web.Response(text=p.read_text(encoding="utf-8"),
                        content_type="text/html")


# ============================================================
# 歌词自动加载 + 进度跟踪
# ============================================================
async def _on_player_state_change(snapshot: Dict[str, Any]) -> None:
    """player 状态变化时:切歌 → 自动拉歌词,更新 ws lyric 缓存。"""
    track = snapshot.get("track") or {}
    tid = track.get("id", "")
    # 修复:即使同一曲目重复 play,也要刷新歌词(用户可能期待重置进度)
    # 真实切歌判断:tid 变了 OR 之前没有歌词
    if tid and (tid != APP._cur_track_id or not APP._cur_lyric_lines):
        APP._cur_track_id = tid
        # 切歌 → 拉歌词
        title = track.get("title", "")
        artist = track.get("artist", "")
        album = track.get("album", "")
        if title and APP.lyric:
            try:
                res = await APP.lyric.get(title=title, artist=artist, album=album)
                if res.ok and res.raw:
                    from lyric_loader import parse_lrc
                    lines = parse_lrc(res.raw)
                    APP._cur_lyric_lines = [
                        {"time_ms": ll.time_ms, "text": ll.text,
                         "words": [{"time_ms": w.time_ms, "text": w.text} for w in (ll.words or [])]}
                        for ll in lines
                    ]
                    APP._cur_lyric_idx = -1
                    APP._cur_lyric_source = res.source
                    await _publish_lyric("lyric_line", {
                        "lines": APP._cur_lyric_lines,
                        "current_idx": -1,
                        "source": res.source,
                    })
                    log.info("[music_web] auto lyric %d lines from %s", len(APP._cur_lyric_lines), res.source)
            except Exception as e:
                log.warning("[music_web] auto lyric fail: %s", e)


async def _progress_watcher() -> None:
    """每秒:根据 last_updated + status 推 progress event。"""
    last_progress = -1.0
    last_status = ""
    while True:
        try:
            await asyncio.sleep(1.0)
            if not APP.player:
                continue
            snap = APP.player.snapshot()
            track = snap.get("track") or {}
            if not track:
                APP._cur_lyric_idx = -1
                continue
            status = snap.get("status", "")
            # 计算 progress
            progress = float(snap.get("progress", 0.0))
            if status == "playing":
                # 后端没有真实 timer 推进,等前端回传 cmd progress
                # 这里只推 cache 的 lyric line index 推进
                pass
            elif status == "paused":
                pass
            else:
                continue

            # 推 lyric index 推进
            delay_ms = (APP.cfg.get("lyrics.delay_ms", 0) if APP.cfg else 0)
            effective_ms = int(progress * 1000) + int(delay_ms)
            if APP._cur_lyric_lines:
                from lyric_loader import find_line_at, LyricLine
                ll_objs = [LyricLine(time_ms=l["time_ms"], text=l["text"]) for l in APP._cur_lyric_lines]
                idx = find_line_at(ll_objs, effective_ms)
                if idx != APP._cur_lyric_idx:
                    APP._cur_lyric_idx = idx
                    await _publish_lyric("lyric_line", {
                        "lines": APP._cur_lyric_lines,
                        "current_idx": idx,
                        "current_text": (APP._cur_lyric_lines[idx]["text"]
                                         if 0 <= idx < len(APP._cur_lyric_lines) else ""),
                    })
        except asyncio.CancelledError:
            return
        except Exception as e:  # noqa: BLE001
            log.warning("[music_web] progress_watcher: %s", e)


# ============================================================
# 启动
# ============================================================
async def on_startup(app: web.Application) -> None:
    APP.ws_lock = asyncio.Lock()
    # cfg
    APP.cfg = AgentCfg(workdir=_COMPANION_DIR)
    log.info("[music_web] cfg loaded: %d keys", len(APP.cfg.get_all()))

    # library
    music_root = Path(APP.cfg.get("music.root") or str(Path.home() / "Music"))
    APP.library = LocalLibrary(music_root)
    tracks = await asyncio.get_event_loop().run_in_executor(
        None, APP.library.scan
    )
    log.info("[music_web] library scanned: %d tracks", len(tracks))

    # online (懒启动,首次 get_url 时初始化 jsdom)
    # P2.5+22(2026-10-03):多源 mock+juhe 双源,musicUrl 失败按序轮询下一个。
    # ikun 排除:api.ikunshare.com 在国内 DNS 不可达(Node ENOTFOUND 必崩进程)。
    APP.online = OnlineSearch(sources=["mock.js", "juhe.js"])

    # P2.5+23(2026-10-03):song pool — 读用户填的 CSV,启动时随机洗 60 首。
    # 失败静默 → 前端拿到 {ok:false,err:"..."} 不影响其他功能。
    APP.song_pool = SongPoolCatalog()
    try:
        n = APP.song_pool.load()
        log.info("[music_web] song_pool initialized: %d songs", n)
    except Exception as e:  # noqa: BLE001
        log.warning("[music_web] song_pool load failed: %s", e)
        APP.song_pool = None

    # player
    APP.player = Player(
        APP.library,
        online=APP.online,
        catalog=APP.song_pool,
        volume=APP.cfg.get("playback.volume", 80),
        playback_mode=APP.cfg.get("playback.mode", "sequential"),
    )

    # lyric provider
    APP.lyric = LyricProvider(
        music_root,
        enable_lrclib=(APP.cfg.get("music.lyrics_provider", "auto") != "local"),
    )

    # 钩 player 状态变化:自动拉歌词
    async def state_hook():
        try:
            await _on_player_state_change(APP.player.snapshot())
        except Exception as e:
            log.warning("[music_web] state_hook err: %s", e)
    APP.player._state_hook = state_hook

    # 启动 progress watcher
    app["watcher"] = asyncio.create_task(_progress_watcher(), name="music_web.watcher")

    log.info("[music_web] all systems initialized")


async def on_cleanup(app: web.Application) -> None:
    log.info("[music_web] cleanup")
    if APP.online:
        APP.online.shutdown()


def build_app() -> web.Application:
    # P2.5+23 hotfix:ship 后用户实测右键开音乐子窗界面没变 → 浏览器缓存了旧版
    # index.html / app.js / app.css。给 /music-static/* 加 no-store 头强制每次重拉。
    # 主页面已经在 index() handler 里加了 Cache-Control,但 <script src="/music-static/app.js">
    # 走 add_static,默认不带 no-store。
    @web.middleware
    async def _no_cache_static_mw(req, handler):
        resp = await handler(req)
        if req.path.startswith("/music-static/") or req.path == "/music-static":
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
        return resp

    app = web.Application(middlewares=[_no_cache_static_mw])
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/queue", api_queue)
    app.router.add_get("/api/library", api_library)
    app.router.add_get("/api/library/search", api_library_search)
    # P2.5+23(2026-10-03):真歌名池 API
    app.router.add_get("/api/songs", api_songs)
    app.router.add_post("/api/songs/respin", api_songs_respin)
    # N9(2026-10-04):AI 歌单推荐
    app.router.add_get("/api/recommend", api_recommend)
    # P2.5+24(2026-10-03):预取下一首 URL
    app.router.add_get("/api/songs/preload", api_songs_preload)
    app.router.add_get("/api/stream/{track_id}", api_stream)
    app.router.add_post("/api/cmd", api_cmd)
    # P3.3(2026-10-03):长按收藏菜单 — 2 个新路由
    app.router.add_get("/api/favorites", api_favorites)
    app.router.add_post("/api/favorites/remove", api_favorite_remove)
    app.router.add_get("/api/agent/cfg/list", api_agent_cfg_list)
    app.router.add_get("/api/agent/cfg/get", api_agent_cfg_get)
    app.router.add_post("/api/agent/cfg/set", api_agent_cfg_set)
    app.router.add_post("/api/agent/intent", api_agent_intent)
    app.router.add_get("/api/lyric", api_lyric)
    app.router.add_get("/ws/state", ws_state)
    app.router.add_get("/ws/lyrics", ws_lyrics)
    app.router.add_get("/lyrics", lyrics_page)
    app.router.add_get("/", index)
    app.router.add_static("/static/", path=str(STATIC_DIR), show_index=False)
    app.router.add_static("/music-static/", path=str(STATIC_DIR / "music"), show_index=False)
    # P2.5+24(2026-10-03):vite build 产物指向 music-vue/dist(若存在)
    _music_vue_dist = STATIC_DIR / "music-vue" / "dist"
    if _music_vue_dist.exists():
        app.router.add_static("/music-vue/", path=str(_music_vue_dist), show_index=False)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


async def main_async(args: argparse.Namespace) -> None:
    APP.host = args.host
    port = args.port
    if port == 0:
        port = _pc_pick_free_port(0)
    APP.port = port
    # M3.34(2026-09-19):同时写 port_config(统一 HKCU/JSON)+ port_registry(兼容旧 (port,pid) 心跳读取者)。
    _pc_write_port("music", port)
    try:
        write_music_port(port, os.getpid())
    except Exception as e:  # noqa: BLE001 — 旧通道失败不能阻塞启动
        log.warning("[music_web] legacy port_registry write failed: %s", e)
    log.info("[music_web] writing port registry: %d (pid=%d)", port, os.getpid())

    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, APP.host, port)
    await site.start()
    log.info("[music_web] listening on http://%s:%d", APP.host, port)
    print(f"[music_web] READY http://{APP.host}:{port}", flush=True)

    # 常驻
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--root", type=str, default=None,
                        help="music root path (overrides cfg)")
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
