# -*- coding: utf-8 -*-
"""
lx_bridge.py — M3.28 Phase 2 Path B
LX Bridge:把 LX Music Desktop Open API 包装成 agent 接口。

状态:
  lx_alive        bool     LX Desktop 是否可达
  status          dict     最新 /status
  lyric_lines     List[LyricLine]  LRC 解析后
  lyric_raw       str      原始 LRC
  current_line    int      SSE/highlight 当前行 index(-1 = 无)
  last_error      str      最近一次错

事件订阅:
  bridge.subscribe(queue):把 LX 状态变化 / 歌词变化 push 到 queue
  bridge.event_loop():后台 task,常驻消费 LX SSE + status poll
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import sys as _sys
from pathlib import Path as _P

# 让 lyric_loader(同目录)和 lx_desktop_client(companion/ 根)都能 import
_here = _P(__file__).resolve().parent
if str(_here) not in _sys.path:
    _sys.path.insert(0, str(_here))
_companion_dir = str(_here.parent)
if _companion_dir not in _sys.path:
    _sys.path.insert(0, _companion_dir)

from lyric_loader import LyricLine, find_line_at, parse_lrc  # noqa: E402
from lx_desktop_client import LxDesktopClient, LxDesktopError  # noqa: E402

log = logging.getLogger("lx_bridge")


# ws event type 常量
EV_MUSIC_STATE = "music_state"     # 通用状态(track/play/progress/duration)
EV_MUSIC_PROGRESS = "music_progress"  # 进度小步进(节流,避免高频推)
EV_MUSIC_LYRIC = "music_lyric"     # 歌词全量 / 当前行
EV_MUSIC_HEALTH = "music_health"   # LX Desktop 连通性变化


@dataclass
class MusicState:
    name: str = ""
    singer: str = ""
    album: str = ""
    status: str = "stopped"      # playing / paused / stopped / unknown
    progress: float = 0.0         # 秒
    duration: float = 0.0         # 秒
    playback_rate: float = 1.0
    lyric_line_text: str = ""     # SSE 推的当前行文本(优先用)
    lyric_lines: List[LyricLine] = field(default_factory=list)
    lyric_current_idx: int = -1
    lx_alive: bool = False
    last_error: str = ""
    updated_at: int = 0          # ms 时间戳

    def to_dict(self) -> Dict[str, Any]:
        # 歌词行只返文本 + time_ms(给前端轻量)
        return {
            "name": self.name,
            "singer": self.singer,
            "album": self.album,
            "status": self.status,
            "progress": self.progress,
            "duration": self.duration,
            "playback_rate": self.playback_rate,
            "lyric_line_text": self.lyric_line_text,
            "lyric_current_idx": self.lyric_current_idx,
            "lx_alive": self.lx_alive,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }


class LxBridge:
    """LX 桥接器:agent 接口 ↔ LX Desktop 23330"""

    def __init__(self, host: str = "127.0.0.1", port: int = 23330):
        self.client = LxDesktopClient(host=host, port=port)
        self.state = MusicState()
        self._subscribers: List[asyncio.Queue] = []
        self._sse_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._stop_evt: Optional[asyncio.Event] = None
        self._lock = asyncio.Lock()

    # ============================================================
    # 生命周期
    # ============================================================
    async def start(self) -> None:
        self._stop_evt = asyncio.Event()
        # 启动 SSE 消费 + 进度兜底 poll(防 SSE 漏进度)
        self._sse_task = asyncio.create_task(self._sse_loop(), name="lx_bridge.sse")
        self._poll_task = asyncio.create_task(self._poll_loop(), name="lx_bridge.poll")

    async def stop(self) -> None:
        if self._stop_evt:
            self._stop_evt.set()
        for t in (self._sse_task, self._poll_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        await self.client.close()
        self._sse_task = None
        self._poll_task = None

    async def __aenter__(self) -> "LxBridge":
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    # ============================================================
    # 订阅者(陪聊 web 启动时注册一个 queue)
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
            except Exception as e:  # noqa: BLE001
                log.warning("[lx_bridge] publish err: %s", e)
                dead.append(q)
        if dead:
            async with self._lock:
                for d in dead:
                    try:
                        self._subscribers.remove(d)
                    except ValueError:
                        pass

    # ============================================================
    # cmd 转发(action ↔ LX endpoint)
    # ============================================================
    async def cmd(self, action: str, **kw: Any) -> Dict[str, Any]:
        """action ∈ play/pause/resume/next/prev/stop/seek/volume/mute
        返回 {ok, action, state:[optional snapshot]}
        """
        action = (action or "").strip().lower()
        try:
            if action == "play":
                ok = await self.client.play()
            elif action == "pause":
                ok = await self.client.pause()
            elif action == "resume":
                # LX resume = play
                ok = await self.client.play()
            elif action in ("next", "skip_next"):
                ok = await self.client.skip_next()
            elif action in ("prev", "skip_prev"):
                ok = await self.client.skip_prev()
            elif action == "stop":
                ok = await self.client.stop()
            elif action == "seek":
                offset = float(kw.get("offset", 0))
                ok = await self.client.seek(offset)
            elif action == "volume":
                vol = int(kw.get("volume", 0))
                ok = await self.client.volume(vol)
            elif action == "mute":
                on = bool(kw.get("on", True))
                ok = await self.client.mute(on)
            elif action == "collect":
                ok = await self.client.collect()
            elif action == "uncollect":
                ok = await self.client.uncollect()
            elif action == "status":
                # 仅读不写
                st = await self.client.status()
                await self._absorb_status(st)
                return {"ok": True, "action": action, "state": self.state.to_dict()}
            else:
                return {"ok": False, "err": f"unknown action: {action}"}
            if not ok:
                return {"ok": False, "action": action, "err": "LX endpoint returned non-200"}
            # 控制后立刻拉一次 status 同步
            try:
                st = await self.client.status()
                await self._absorb_status(st)
            except LxDesktopError as e:
                log.warning("[lx_bridge] post-cmd status fail: %s", e)
            await self._publish(EV_MUSIC_STATE, self.state.to_dict())
            return {"ok": True, "action": action, "state": self.state.to_dict()}
        except LxDesktopError as e:
            log.warning("[lx_bridge] cmd %s fail: %s", action, e)
            self.state.lx_alive = False
            self.state.last_error = str(e)
            return {"ok": False, "action": action, "err": str(e)}

    # ============================================================
    # 内部状态吸收(把 LX 返回值合到 self.state)
    # ============================================================
    async def _refresh_lyric(self, *, name_event: bool = False) -> None:
        """拉取 LRC + 解析 + 发布 lyric event。不阻塞调用方(SSE 循环)。"""
        try:
            lrc = await self.client.lyric()
        except LxDesktopError as e:
            log.debug("[lx_bridge] lyric pull fail: %s", e)
            return
        self.state.lyric_raw = lrc
        self.state.lyric_lines = parse_lrc(lrc)
        self.state.lyric_current_idx = -1
        await self._publish(EV_MUSIC_LYRIC, {
            "lines": [
                {"time_ms": ll.time_ms, "text": ll.text}
                for ll in self.state.lyric_lines
            ],
            "current_idx": -1,
        })

    async def _absorb_status(self, raw: Dict[str, Any]) -> None:
        if not isinstance(raw, dict):
            return
        # 字段映射(LX 返回 camelCase)
        old_alive = self.state.lx_alive
        old_name = self.state.name
        old_duration = self.state.duration

        self.state.lx_alive = True
        self.state.last_error = ""
        self.state.status = raw.get("status", self.state.status) or "unknown"
        self.state.name = raw.get("name", "") or ""
        self.state.singer = raw.get("singer", "") or ""
        self.state.album = raw.get("albumName", "") or ""
        self.state.progress = float(raw.get("progress", 0.0) or 0.0)
        self.state.duration = float(raw.get("duration", 0.0) or 0.0)
        self.state.playback_rate = float(raw.get("playbackRate", 1.0) or 1.0)
        self.state.lyric_line_text = raw.get("lyricLineText", "") or ""
        self.state.updated_at = int(time.time() * 1000)

        # 切歌(name 变 / duration 变)→ 拉一次 LRC
        track_changed = (
            self.state.name != old_name
            or (self.state.duration > 0 and self.state.duration != old_duration)
        )
        if track_changed:
            asyncio.create_task(self._refresh_lyric())

        # 当前行推进(用 self.state.progress 找行)
        if self.state.lyric_lines and self.state.progress > 0:
            idx = find_line_at(self.state.lyric_lines, int(self.state.progress * 1000))
            if idx != self.state.lyric_current_idx:
                self.state.lyric_current_idx = idx
                await self._publish(EV_MUSIC_LYRIC, {
                    "current_idx": idx,
                    "current_text": self.state.lyric_lines[idx].text if idx >= 0 else "",
                    "lyric_line_text_sse": self.state.lyric_line_text,
                })

        if not old_alive:
            await self._publish(EV_MUSIC_HEALTH, {"lx_alive": True, "err": ""})

    # ============================================================
    # SSE 消费:实时推 status / name / lyricLineText
    # ============================================================
    async def _sse_loop(self) -> None:
        while not (self._stop_evt and self._stop_evt.is_set()):
            try:
                async for ev in self.client.subscribe_status():
                    if self._stop_evt and self._stop_evt.is_set():
                        break
                    await self._handle_sse(ev)
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                log.warning("[lx_bridge] sse_loop top: %s", e)
                await asyncio.sleep(2)

    async def _handle_sse(self, ev: Dict[str, Any]) -> None:
        t = ev.get("type", "")
        d = ev.get("data")
        if t == "status":
            self.state.status = str(d) if d is not None else self.state.status
            await self._publish(EV_MUSIC_STATE, self.state.to_dict())
        elif t in ("name", "singer", "albumName"):
            if t == "name":
                self.state.name = str(d) if d is not None else ""
                # name 变 → 触发 lyric 拉取(不阻塞 SSE 循环)
                asyncio.create_task(self._refresh_lyric(name_event=True))
            elif t == "singer":
                self.state.singer = str(d) if d is not None else ""
            elif t == "albumName":
                self.state.album = str(d) if d is not None else ""
            self.state.updated_at = int(time.time() * 1000)
            await self._publish(EV_MUSIC_STATE, self.state.to_dict())
        elif t == "duration":
            try:
                self.state.duration = float(d) if d is not None else 0.0
            except (TypeError, ValueError):
                pass
            self.state.updated_at = int(time.time() * 1000)
            await self._publish(EV_MUSIC_STATE, self.state.to_dict())
        elif t == "progress":
            try:
                self.state.progress = float(d) if d is not None else 0.0
            except (TypeError, ValueError):
                return
            self.state.updated_at = int(time.time() * 1000)
            # 节流:每 1s 推一次进度
            now_ms = self.state.updated_at
            last = getattr(self, "_last_progress_pub_ms", 0)
            if now_ms - last >= 1000:
                self._last_progress_pub_ms = now_ms
                await self._publish(EV_MUSIC_PROGRESS, {
                    "progress": self.state.progress,
                    "duration": self.state.duration,
                    "status": self.state.status,
                    "lyric_line_text": self.state.lyric_line_text,
                })
            # 同步推进歌词行
            if self.state.lyric_lines and self.state.progress > 0:
                idx = find_line_at(self.state.lyric_lines, int(self.state.progress * 1000))
                if idx != self.state.lyric_current_idx:
                    self.state.lyric_current_idx = idx
                    await self._publish(EV_MUSIC_LYRIC, {
                        "current_idx": idx,
                        "current_text": self.state.lyric_lines[idx].text if idx >= 0 else "",
                        "lyric_line_text_sse": self.state.lyric_line_text,
                    })
        elif t == "playbackRate":
            try:
                self.state.playback_rate = float(d) if d is not None else 1.0
            except (TypeError, ValueError):
                pass
            self.state.updated_at = int(time.time() * 1000)
            await self._publish(EV_MUSIC_STATE, self.state.to_dict())
        elif t == "lyricLineText":
            self.state.lyric_line_text = str(d) if d is not None else ""
            self.state.updated_at = int(time.time() * 1000)
            # lyricLineText 是 LX 推的「当前行」,比进度驱动准
            # 找到匹配行(若有)
            idx = -1
            if self.state.lyric_line_text and self.state.lyric_lines:
                for i, ll in enumerate(self.state.lyric_lines):
                    if ll.text == self.state.lyric_line_text:
                        idx = i
                        break
            if idx != self.state.lyric_current_idx:
                self.state.lyric_current_idx = idx
            await self._publish(EV_MUSIC_LYRIC, {
                "current_idx": idx,
                "current_text": self.state.lyric_line_text,
                "lyric_line_text_sse": self.state.lyric_line_text,
            })

    # ============================================================
    # 兜底 poll:SSE 漏时拉一次 status(防 SSE 断)
    # ============================================================
    async def _poll_loop(self) -> None:
        while not (self._stop_evt and self._stop_evt.is_set()):
            try:
                st = await self.client.status()
                await self._absorb_status(st)
            except LxDesktopError as e:
                if self.state.lx_alive:
                    self.state.lx_alive = False
                    self.state.last_error = str(e)
                    await self._publish(EV_MUSIC_HEALTH, {"lx_alive": False, "err": str(e)})
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                log.warning("[lx_bridge] poll_loop: %s", e)
            # 每 3s 兜底
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass

    # ============================================================
    # 同步 helper(给 priritragent web 在 startup 时调用)
    # ============================================================
    async def warmup(self) -> bool:
        """启动时拉一次 status + lyric;返 LX 是否可达"""
        try:
            st = await self.client.status()
            await self._absorb_status(st)
            return True
        except LxDesktopError as e:
            self.state.lx_alive = False
            self.state.last_error = str(e)
            log.warning("[lx_bridge] warmup fail: %s", e)
            return False


async def quick_smoke() -> Dict[str, Any]:
    async with LxBridge() as b:
        await b.warmup()
        return {"warmup_ok": b.state.lx_alive, "state": b.state.to_dict()}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(quick_smoke()), ensure_ascii=False, indent=2))