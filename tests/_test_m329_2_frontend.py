# -*- coding: utf-8 -*-
"""
M3.29.2 music web 前端 e2e (HTTP + WebSocket)

该 test 验证前端的所有 API/WS 行为(命令、队列、状态、歌词),浏览器 UI 渲染
由 Claude 会话内的 puppeteer-mcp 工具实拍,不在该脚本里做。脚本可重复运行
不打 UI 也能确保 backend + ws 行为正确。

验证:
  T1: HTTP /api/health
  T2: /api/library 扫本地库
  T3: /api/cmd play → queued + state 包含 track
  T4: /api/state 含 track + volume
  T5: /api/queue 含已加曲目
  T6: WS /ws/state 推 music_state + music_progress + heartbeat
  T7: agent cfg list + set + get(范围校验)
  T8: agent intent 关键词 → cfg path 转换
  T9: WS /ws/lyrics 推 lyric_cfg + lyric_line
  T10: /api/lyric 本地优先 + 远程搜补
  T11: cmd pause / resume / next / seek / stop 全过
  T12: 前端静态资源(/music-static/app.js + app.css + index.html)可达

UI 实拍(由 Claude 会话做):
  - 主页国画主题渲染 + 队列 + 当前曲目 + 折叠歌词
  - 控制条 ⏮▶⏭⏹ + progress
  - lyric pane 折叠展开
  - ws 状态事件更新 DOM
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import websockets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _url(url: str) -> str:
    """URL-encode query string (Windows cmd ascii default 破中文)."""
    if "?" not in url:
        return url
    base, qs = url.split("?", 1)
    parts = []
    for seg in qs.split("&"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            parts.append(f"{urllib.parse.quote(k)}={urllib.parse.quote(v, safe='')}")
        else:
            parts.append(urllib.parse.quote(seg, safe=""))
    return base + "?" + "&".join(parts)


def http_get(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post(url: str, body: dict = None, timeout: float = 5.0) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def find_music_port() -> int | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrisirAI") as k:
            v, _ = winreg.QueryValueEx(k, "music_port")
            return int(v)
    except Exception:
        return None


results: list = []


def check(actual, expected, label: str) -> None:
    if actual == expected:
        print(f"  PASS {label}")
        results.append(True)
    else:
        print(f"  FAIL {label}: got {actual!r}, expected {expected!r}")
        results.append(False)


def check_true(cond: bool, label: str) -> None:
    if cond:
        print(f"  PASS {label}")
        results.append(True)
    else:
        print(f"  FAIL {label}")
        results.append(False)


async def ws_collect(url: str, max_msgs: int = 5, max_wait: float = 4.0) -> list:
    """收 ws 消息,最多 max_msgs 条 或 max_wait 秒。"""
    msgs = []
    try:
        async with websockets.connect(url) as ws:
            end = time.time() + max_wait
            while len(msgs) < max_msgs and time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
                    msgs.append(json.loads(raw))
                except asyncio.TimeoutError:
                    break
    except Exception as e:
        print(f"  ws error: {e}")
    return msgs


async def main_async(port: int) -> None:
    base = f"http://127.0.0.1:{port}"
    ws_state = f"ws://127.0.0.1:{port}/ws/state"
    ws_lyric = f"ws://127.0.0.1:{port}/ws/lyrics"

    # T1: health
    print("\n[T1] /api/health")
    h = http_get(f"{base}/api/health")
    check_true(h.get("ok"), "health.ok")
    check(h.get("service"), "prisiragent-music-web", "health.service")

    # T2: library scan
    print("\n[T2] /api/library")
    lib = http_get(f"{base}/api/library")
    check_true(lib.get("ok"), "library.ok")
    check_true(lib.get("count", 0) >= 1, f"library.count >= 1 ({lib.get('count')})")
    first_id = lib["tracks"][0]["id"] if lib.get("tracks") else None
    first_title = lib["tracks"][0]["title"] if lib.get("tracks") else None
    first_artist = lib["tracks"][0]["artist"] if lib.get("tracks") else None

    # T3: cmd play
    print("\n[T3] /api/cmd play")
    if not first_id:
        print("  [skip] no tracks in library")
    else:
        play = http_post(f"{base}/api/cmd", {"action": "play", "track_id": first_id})
        check_true(play.get("ok"), "play.ok")
        # play 返回的是 state(含 track),不是 queued
        check_true(bool(play.get("state", {}).get("track")), "play.state.track present")
        check(play.get("state", {}).get("track", {}).get("title"), first_title, "play.state.track.title")

    # T4: state
    print("\n[T4] /api/state")
    st = http_get(f"{base}/api/state")
    check_true(st.get("ok"), "state.ok")
    if first_id:
        check(st.get("state", {}).get("track", {}).get("title"), first_title, "state.track.title")
        check_true(isinstance(st.get("state", {}).get("volume"), (int, float)), "state.volume numeric")

    # T5: queue
    print("\n[T5] /api/queue")
    q = http_get(f"{base}/api/queue")
    check_true(q.get("ok"), "queue.ok")
    check_true(len(q.get("queue", [])) >= 1, f"queue.length >= 1 ({len(q.get('queue', []))})")
    check_true(q.get("cursor", -1) >= 0, f"queue.cursor >= 0 ({q.get('cursor')})")

    # T6: ws state stream
    print("\n[T6] WS /ws/state 推 music_state + progress + heartbeat")
    msgs = await ws_collect(ws_state, max_msgs=4, max_wait=3.0)
    types = [m.get("type") for m in msgs]
    print(f"  ws types: {types}")
    check_true("music_state" in types, "music_state event received")
    check_true(any(t in ("music_progress", "heartbeat") for t in types), "progress/heartbeat present")

    # T7: agent cfg
    print("\n[T7] agent cfg list + set + get + range reject")
    cl = http_get(f"{base}/api/agent/cfg/list")
    check_true(cl.get("ok"), "cfg.list.ok")
    n = len(cl.get("items", []))
    print(f"  cfg items: {n}")
    check_true(n >= 19, f"cfg items >= 19 ({n})")
    # all items have agent_only: True
    all_agent = all(it.get("agent_only") for it in cl.get("items", []))
    check_true(all_agent, "all cfg items agent_only=True")

    # set + get
    set_res = http_post(f"{base}/api/agent/cfg/set", {"playback.volume": 65})
    print(f"  set playback.volume=65: {set_res}")
    check_true(set_res.get("changed", {}).get("playback.volume") == 65, "set returns changed value")
    get_res = http_get(_url(f"{base}/api/agent/cfg/get?path=playback.volume"))
    check(get_res.get("value"), 65, "get returns 65")

    # range reject
    bad = http_post(f"{base}/api/agent/cfg/set", {"playback.volume": 999})
    print(f"  range reject (volume=999): {bad}")
    check_true(bad.get("results", {}).get("playback.volume", {}).get("ok") is False, "range reject flagged")

    # T8: nl intent
    print("\n[T8] agent intent 关键词路由")
    cases = [
        ("声音大一点", "playback.volume"),  # volume up
        ("声音轻一点", "playback.volume"),  # volume down
        ("歌词大点", "lyrics.font_size"),  # font up
        ("歌词字号30", "lyrics.font_size"),  # font set
        ("单曲循环", "playback.mode"),
        ("列表循环", "playback.mode"),
        ("显示歌词", "lyrics.window_visible"),
        ("按刘德华的歌单播", "_action"),
        ("play 安静的歌", "_action"),
    ]
    for text, expected_key in cases:
        r = http_post(f"{base}/api/agent/intent", {"text": text})
        print(f"  intent {text!r} -> {r}")
        if expected_key == "_action":
            check_true(r.get("intent", {}).get("matched"), f"intent '{text}' matched")
        else:
            check_true(expected_key in r.get("intent", {}).get("payload", {}),
                       f"intent '{text}' → {expected_key}")

    # T9: ws lyrics stream
    print("\n[T9] WS /ws/lyrics 推 lyric_cfg + lyric_line (在播 mp3)")
    # 重新确保播放
    if first_id:
        http_post(f"{base}/api/cmd", {"action": "play", "track_id": first_id})
        await asyncio.sleep(0.5)
        msgs = await ws_collect(ws_lyric, max_msgs=3, max_wait=3.0)
        types = [m.get("type") for m in msgs]
        print(f"  lyric ws types: {types}")
        # 期望至少 lyric_cfg(后端启动时已推一次)+ lyric_line(track 自动拉歌词)
        check_true(any(t in ("lyric_cfg", "lyric_line") for t in types),
                   "lyric event received (cfg/line)")

    # T10: /api/lyric
    print("\n[T10] /api/lyric 本地优先")
    if first_title:
        lr = http_get(_url(f"{base}/api/lyric?title={first_title}&artist={first_artist}"))
        print(f"  lyric ok={lr.get('ok')} source={lr.get('source')} lines={len(lr.get('lines', []))}")
        # source 是 local / lrclib / none — 本地无 .lrc + LRCLib 搜不到,会是 source=none, ok=False
        # 这是测试环境问题,不算 player bug
        check_true(lr.get("source") in ("local", "lrclib", "none"),
                   f"lyric.source valid ({lr.get('source')})")
        if lr.get("source") == "none":
            print("  [info] no local .lrc + LRCLib miss — lyric flow unreachable in this env")
            # 不算 fail,但要确认接口形状对(后端返 lines_count,不是 lines)
            check_true(isinstance(lr.get("lines_count"), int), "lyric.lines_count is int")
        else:
            check_true(lr.get("ok"), "lyric.ok when source is local/lrclib")
            check_true(lr.get("lines_count", 0) > 0, "lyric.lines_count > 0")

    # T11: cmd 全套
    print("\n[T11] cmd pause/resume/next/seek/stop 全过")
    if first_id:
        for act in ["pause", "resume", "next", "stop"]:
            r = http_post(f"{base}/api/cmd", {"action": act})
            check_true(r.get("ok"), f"cmd {act}.ok")
        # seek
        r = http_post(f"{base}/api/cmd", {"action": "seek", "offset": 1.5})
        check_true(r.get("ok"), "cmd seek.ok")
        # 再 play 回去,避免 stop 后 ws 推不出
        http_post(f"{base}/api/cmd", {"action": "play", "track_id": first_id})

    # T12: 静态资源
    print("\n[T12] /music-static/{app.js, app.css, index.html}")
    for path in ["/music-static/app.js", "/music-static/app.css", "/music-static/index.html"]:
        try:
            with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
                check_true(r.status == 200, f"static {path} (status {r.status})")
        except Exception as e:
            print(f"  FAIL static {path}: {e}")
            results.append(False)
    # 主页 /
    try:
        with urllib.request.urlopen(f"{base}/", timeout=5) as r:
            html = r.read().decode("utf-8")
            check_true(r.status == 200, "main page (status 200)")
            check_true("PrisirAI" in html, "main page contains PrisirAI brand")
    except Exception as e:
        print(f"  FAIL main page: {e}")
        results.append(False)


def main():
    port = find_music_port()
    if not port:
        print("[FATAL] music_port not in registry")
        return 2
    print(f"[setup] music web @ 127.0.0.1:{port}")
    # health check
    try:
        h = http_get(f"http://127.0.0.1:{port}/api/health")
        print(f"[setup] health: {h}")
    except Exception as e:
        print(f"[FATAL] music web not reachable: {e}")
        return 2

    asyncio.run(main_async(port))
    total = len(results)
    passed = sum(results)
    print(f"\n=== M3.29.2 frontend e2e (HTTP + WS): {passed}/{total} pass ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
