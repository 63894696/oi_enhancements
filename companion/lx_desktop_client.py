# -*- coding: utf-8 -*-
"""
lx_desktop_client.py — M3.28 Phase 2 Path B
aiohttp client for LX Music Desktop Open API @ 127.0.0.1:23330.

LX Music Desktop v2.7.0+ 默认开启 Open API(2025-08+ 默认 bind 127.0.0.1)。
Open API 是 control-only:状态读 + 播放控制 + SSE,无 search/getMusicUrl。

端点(2026-09-18 实测):
  /status                     200 当前曲目 + 进度
  /lyric                      200 LRC 歌词
  /lyric-all                  200 {lyric, tlyric, rlyric, lxlyric}
  /play /pause                200 控制
  /skip-next /skip-prev       200 切歌
  /seek?offset=N              200 跳秒
  /volume?volume=N            200 音量 0-100
  /mute?mute=true|false       200 静音
  /collect /uncollect         200 收藏
  /subscribe-player-status    200 SSE event: status/name/singer/albumName/
                                   progress/duration/playbackRate/lyricLineText

无 token 鉴权、CORS 开放、默认绑 127.0.0.1。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any, AsyncIterator, Dict, Optional

import aiohttp

log = logging.getLogger("lx_desktop")

_DEFAULT_PORT = 23330
_DEFAULT_TIMEOUT = 5.0
_SSE_KEEPALIVE_SEC = 5.0  # LX 5s 无事件就发 :keepalive(实测)


class LxDesktopError(RuntimeError):
    """LX Desktop 调用错(端口没开 / 端点不存在 / 解析失败)"""


class LxDesktopClient:
    """LX Music Desktop Open API 客户端

    用法:
        c = LxDesktopClient()           # 默认 127.0.0.1:23330
        st = await c.status()
        async for ev in c.subscribe_status():
            ...
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = _DEFAULT_PORT,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        self.base_url = f"http://{host}:{port}"
        # aiohttp 总超时:DNS connect + read
        self._timeout = aiohttp.ClientTimeout(total=timeout, connect=2.0, sock_read=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> "LxDesktopClient":
        await self._ensure_session()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ============================================================
    # 通用 GET / POST
    # ============================================================
    async def _get_json(self, path: str, **params: Any) -> Any:
        sess = await self._ensure_session()
        url = self.base_url + path
        try:
            async with sess.get(url, params=params or None) as resp:
                if resp.status != 200:
                    raise LxDesktopError(f"GET {path} status={resp.status}")
                ct = resp.headers.get("Content-Type", "")
                if "json" in ct:
                    return await resp.json()
                # LX 返文本(歌词 / play OK 等)
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise LxDesktopError(f"GET {path} failed: {e}") from e

    async def _get_ok(self, path: str, **params: Any) -> bool:
        """GET 一个端点,要求 200。"""
        try:
            await self._get_json(path, **params)
            return True
        except LxDesktopError:
            return False

    # ============================================================
    # 状态 / 歌词
    # ============================================================
    async def status(self) -> Dict[str, Any]:
        """GET /status — 当前播放状态"""
        return await self._get_json("/status")

    async def lyric(self) -> str:
        """GET /lyric — LRC 文本歌词"""
        v = await self._get_json("/lyric")
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)

    async def lyric_all(self) -> Dict[str, str]:
        """GET /lyric-all — {lyric, tlyric, rlyric, lxlyric}"""
        v = await self._get_json("/lyric-all")
        if isinstance(v, dict):
            return {k: (vv if isinstance(vv, str) else "") for k, vv in v.items()}
        return {}

    async def ping(self) -> bool:
        """轻量探测 LX Desktop 是否在跑"""
        try:
            await self.status()
            return True
        except LxDesktopError:
            return False

    # ============================================================
    # 控制(GET /<action> 即可,无 body)
    # ============================================================
    async def play(self) -> bool:
        return await self._get_ok("/play")

    async def pause(self) -> bool:
        return await self._get_ok("/pause")

    async def stop(self) -> bool:
        # LX 没 /stop;pause + seek 0 等价
        ok = await self.pause()
        if ok:
            await self.seek(0)
        return ok

    async def skip_next(self) -> bool:
        return await self._get_ok("/skip-next")

    async def skip_prev(self) -> bool:
        return await self._get_ok("/skip-prev")

    async def seek(self, offset_s: float) -> bool:
        return await self._get_ok("/seek", offset=offset_s)

    async def volume(self, vol: int) -> bool:
        vol = max(0, min(100, int(vol)))
        return await self._get_ok("/volume", volume=vol)

    async def mute(self, on: bool) -> bool:
        return await self._get_ok("/mute", mute="true" if on else "false")

    async def collect(self) -> bool:
        return await self._get_ok("/collect")

    async def uncollect(self) -> bool:
        return await self._get_ok("/uncollect")

    # ============================================================
    # SSE: /subscribe-player-status
    # ============================================================
    async def subscribe_status(
        self, *, reconnect_delay: float = 5.0
    ) -> AsyncIterator[Dict[str, Any]]:
        """SSE 长连接,实时推 LX 状态变化。

        协议:`event: <type>\\ndata: <json>\\n\\n`
        type ∈ {status, name, singer, albumName, progress, duration,
                playbackRate, lyricLineText}

        异常自动 5s 重连。
        """
        url = self.base_url + "/subscribe-player-status"
        while True:
            try:
                sess = await self._ensure_session()
                async with sess.get(url, headers={"Accept": "text/event-stream"}) as resp:
                    if resp.status != 200:
                        raise LxDesktopError(f"SSE status={resp.status}")
                    buf_event = ""
                    buf_data = ""
                    # LX Desktop SSE 用 \\r\\n\\r\\n 分隔;Linux 是 \\n\\n
                    while True:
                        try:
                            line = await resp.content.readline()
                        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                            raise LxDesktopError(f"SSE readline: {e}") from e
                        if not line:
                            # EOF:服务端主动 close(切歌瞬间常见,会重开新的 SSE)
                            raise LxDesktopError("SSE EOF (server closed, likely track change)")
                        line = line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if not line:
                            # 空行 = 事件结束
                            if buf_event or buf_data:
                                yield self._parse_sse_event(buf_event, buf_data)
                                buf_event = ""
                                buf_data = ""
                            continue
                        if line.startswith("event:"):
                            buf_event = line[6:].strip()
                        elif line.startswith("data:"):
                            buf_data += line[5:].strip()
                        elif line.startswith(":"):
                            # SSE comment(keepalive),忽略
                            pass
                        else:
                            # 未知行,记日志后继续
                            log.debug("[lx_desktop] SSE unknown line: %r", line[:80])
            except LxDesktopError as e:
                log.warning("[lx_desktop] SSE disconnect: %s;reconnect in %.1fs", e, reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                # 重连后放弃累积的 event/data
                continue
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("[lx_desktop] SSE net err: %s;reconnect in %.1fs", e, reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("[lx_desktop] SSE crash: %s", e)
                await asyncio.sleep(reconnect_delay)
                continue

    @staticmethod
    def _parse_sse_event(event: str, data: str) -> Dict[str, Any]:
        """把 SSE event/data 转 dict;data 可能是 JSON 字符串或字面文本"""
        parsed: Any
        if not data:
            parsed = ""
        else:
            try:
                parsed = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                parsed = data
        return {"type": event or "message", "data": parsed}


async def quick_smoke() -> Dict[str, Any]:
    """独立 smoke 测试:python lx_desktop_client.py"""
    async with LxDesktopClient() as c:
        return {
            "ping": await c.ping(),
            "status": await c.status(),
            "lyric_head": (await c.lyric())[:200],
        }


if __name__ == "__main__":
    out = asyncio.run(quick_smoke())
    print(json.dumps(out, ensure_ascii=False, indent=2))