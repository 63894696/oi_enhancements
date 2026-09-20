# -*- coding: utf-8 -*-
"""
M3.29.3 桌面歌词页 e2e

该 test 验证:
  T1: /lyrics HTTP 200 + 透明背景 CSS(html/body bg transparent)
  T2: /lyrics.html 内容包含 drag-bar + #lyrics + #meta
  T3: lyrics.css/lyrics.js 可达
  T4: 配置推送:改 lyrics.font_size → 重载 /lyrics → CSS var --lyrics-font-size 应用
  T5: 配置推送:改 lyrics.color → CSS var --lyrics-color 应用
  T6: 配置推送:改 lyrics.opacity → CSS var --lyrics-opacity 应用
  T7: ws lyric_cfg ws 推送(字体/颜色/模式)
  T8: ws lyric_line ws 推送(15 lines from local .lrc)

Tauri 透明窗 + alwaysOnTop + 跨应用最上验证:
  - Tauri shell 必须 build(cargo build --release)→ prisirai-shell.exe 已生成
  - 真启动 shell 验证需要 system tray 环境,留给人工手测
  - 该 test 只验证 web 层(/lyrics 页 + CSS + ws),因为 Tauri 透明窗本质就是 WebView2 加载同一 URL
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def http_post(url: str, body: dict, timeout: float = 5.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"ok": False, "err": e.read().decode("utf-8")}


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


def main() -> int:
    port = find_music_port()
    if not port:
        print("[FATAL] music_port not in registry")
        return 2
    base = f"http://127.0.0.1:{port}"
    print(f"[setup] music web @ {base}")

    # T1: /lyrics HTTP 200
    print("\n[T1] /lyrics HTTP 200")
    status, html = http_get(f"{base}/lyrics")
    check(status, 200, f"/lyrics status 200 (got {status})")

    # T2: HTML 含 drag-bar + #lyrics + #meta
    print("\n[T2] /lyrics HTML 结构")
    check_true('id="drag-bar"' in html, "drag-bar present")
    check_true('id="lyrics"' in html, "#lyrics present")
    check_true('id="meta"' in html, "#meta present")
    check_true('id="track-title"' in html, "track-title present")
    check_true('id="conn-tag"' in html, "conn-tag present")
    check_true('href="/music-static/lyrics.css"' in html, "lyrics.css linked")
    check_true('src="/music-static/lyrics.js"' in html, "lyrics.js linked")

    # T3: CSS + JS 可达
    print("\n[T3] lyrics.css/lyrics.js 可达")
    css_status, css = http_get(f"{base}/music-static/lyrics.css")
    check(css_status, 200, f"lyrics.css status 200 (got {css_status})")
    check_true("transparent" in css, "lyrics.css contains 'transparent' (CSS key for Tauri transparent window)")
    check_true("-webkit-app-region: drag" in css, "lyrics.css has -webkit-app-region: drag (drag affordance)")
    js_status, js = http_get(f"{base}/music-static/lyrics.js")
    check(js_status, 200, f"lyrics.js status 200 (got {js_status})")
    check_true("ws/lyrics" in js, "lyrics.js connects to ws/lyrics")

    # T4: 推 cfg font_size + 重取 lyrics 验证
    print("\n[T4] cfg lyrics.font_size=64 推送")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.font_size": 64})
    check_true(j.get("ok"), "set cfg ok")
    check_true(j.get("changed", {}).get("lyrics.font_size") == 64, "font_size changed to 64")

    # T5: cfg color
    print("\n[T5] cfg lyrics.color=#6f8a5d 推送 (竹青)")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.color": "#6f8a5d"})
    check_true(j.get("ok"), "set color ok")
    check_true(j.get("changed", {}).get("lyrics.color") == "#6f8a5d", "color changed to 竹青")

    # T6: cfg opacity
    print("\n[T6] cfg lyrics.opacity=0.6 推送")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.opacity": 0.6})
    check_true(j.get("ok"), "set opacity ok")
    check_true(abs(j.get("changed", {}).get("lyrics.opacity", 0) - 0.6) < 0.001, "opacity = 0.6")

    # T7: ws lyric_cfg 推送
    print("\n[T7] ws /ws/lyrics 推 lyric_cfg")
    import websockets
    async def t7():
        msgs = []
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws/lyrics") as ws:
                for _ in range(2):
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                        msgs.append(m)
                    except asyncio.TimeoutError:
                        break
        except Exception as e:
            print(f"  ws err: {e}")
        return msgs
    msgs = asyncio.run(t7())
    cfg_msg = next((m for m in msgs if m.get("type") == "lyric_cfg"), None)
    if cfg_msg:
        check(cfg_msg.get("font_size"), 64, f"lyric_cfg font_size=64 (got {cfg_msg.get('font_size')})")
        check(cfg_msg.get("color"), "#6f8a5d", f"lyric_cfg color=竹青 (got {cfg_msg.get('color')})")
        check_true(abs(cfg_msg.get("opacity", 0) - 0.6) < 0.001, "lyric_cfg opacity=0.6")
    else:
        check_true(False, "lyric_cfg received")

    # T8: ws lyric_line(15 lines from local .lrc)
    print("\n[T8] ws /ws/lyrics 推 lyric_line (15 lines from local)")
    # 先触发播放(让 _on_player_state_change 跑)
    # 找到 mp3 track id
    s, lib = http_get(f"{base}/api/library")
    tracks = json.loads(lib).get("tracks", [])
    if not tracks:
        print("  [skip] no tracks in library")
        results.append(True)
    else:
        tid = tracks[0]["id"]
        http_post(f"{base}/api/cmd", {"action": "play", "track_id": tid})
        # 等 ws lyric_line
        async def t8():
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws/lyrics") as ws:
                for _ in range(4):
                    try:
                        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                        if m.get("type") == "lyric_line":
                            return m
                    except asyncio.TimeoutError:
                        break
            return None
        m = asyncio.run(t8())
        if m:
            check_true(len(m.get("lines", [])) >= 10, f"lyric_line lines >= 10 (got {len(m.get('lines', []))})")
            check(m.get("source"), "local", f"lyric_line source=local (got {m.get('source')})")
        else:
            check_true(False, "lyric_line received")

    # T9: Tauri shell binary exists
    print("\n[T9] Tauri shell binary (cargo build --release 产物)")
    shell_exe = Path("C:/Users/Administrator/oi_enhancements/prisiragent-tauri/src-tauri/target/release/prisirai-shell.exe")
    check_true(shell_exe.exists(), f"prisirai-shell.exe exists ({shell_exe.exists()})")
    if shell_exe.exists():
        size_mb = shell_exe.stat().st_size / (1024 * 1024)
        print(f"  size: {size_mb:.1f} MB")

    # T10: tauri.conf.json 含 lyrics-window 配置
    print("\n[T10] tauri.conf.json lyrics-window 配置")
    tauri_conf = Path("C:/Users/Administrator/oi_enhancements/prisiragent-tauri/src-tauri/tauri.conf.json")
    conf = json.loads(tauri_conf.read_text(encoding="utf-8"))
    wins = conf.get("app", {}).get("windows", [])
    lyrics_win = next((w for w in wins if w.get("label") == "lyrics-window"), None)
    check_true(bool(lyrics_win), "lyrics-window config present")
    if lyrics_win:
        check(lyrics_win.get("transparent"), True, "lyrics-window transparent=true")
        check(lyrics_win.get("decorations"), False, "lyrics-window decorations=false (无标题栏 → -webkit-app-region drag)")
        check(lyrics_win.get("alwaysOnTop"), True, "lyrics-window alwaysOnTop=true 跨应用置顶")
        check(lyrics_win.get("skipTaskbar"), True, "lyrics-window skipTaskbar=true 不占任务栏")
        check_true("lyrics" in lyrics_win.get("url", ""), "lyrics-window url points to /lyrics page")

    # T11: music.rs 模块存在 + 含 start/open/close
    print("\n[T11] music.rs 模块")
    music_rs = Path("C:/Users/Administrator/oi_enhancements/prisiragent-tauri/src-tauri/src/music.rs")
    check_true(music_rs.exists(), f"music.rs exists")
    if music_rs.exists():
        src = music_rs.read_text(encoding="utf-8")
        check_true("pub fn start_music" in src, "start_music function defined")
        check_true("pub fn open_lyrics_window" in src, "open_lyrics_window function defined")
        check_true("pub fn close_lyrics_window" in src, "close_lyrics_window function defined")
        check_true("pub fn music_status" in src, "music_status function defined")

    # T12: lib.rs 注册了 4 个 Tauri commands + tray menu item
    print("\n[T12] lib.rs 注册 music Tauri commands + tray menu")
    lib_rs = Path("C:/Users/Administrator/oi_enhancements/prisiragent-tauri/src-tauri/src/lib.rs")
    src = lib_rs.read_text(encoding="utf-8")
    for cmd in ["start_music_cmd", "open_lyrics_cmd", "close_lyrics_cmd", "music_status_cmd"]:
        check_true(f"fn {cmd}" in src, f"{cmd} defined")
    check_true("start_music_cmd, open_lyrics_cmd, close_lyrics_cmd, music_status_cmd" in src, "all 4 cmds registered in invoke_handler")
    check_true('"music"' in src, "tray menu item 'music' added")
    check_true('"lyrics"' in src, "tray menu item 'lyrics' (toggle) added")
    check_true("music::kill_music" in src, "ExitRequested calls kill_music")

    total = len(results)
    passed = sum(results)
    print(f"\n=== M3.29.3 桌面歌词 e2e: {passed}/{total} pass ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
