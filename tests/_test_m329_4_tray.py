# -*- coding: utf-8 -*-
"""
M3.29.4 Tauri 托盘菜单集成 e2e

覆盖:
  T1: GET /api/music/dispatch 返回 {ok, source, self_port, self_url, self_lyrics_url, tauri_dispatch_hint}
  T2: POST /api/music/start spawn 新 music web(若已存在则返回已有 port,无 spawn 副作用)
  T3: 陪聊 index.html 含 btnMusicLauncher + musicLaunchToast
  T4: 陪聊 app.js 含 dispatchMusic + showMusicToast + /api/music/dispatch + /api/music/start
  T5: tauri.conf.json lyrics-window 配置(transparent + alwaysOnTop + decorations:false)
  T6: lib.rs 含 start_music_cmd / open_lyrics_cmd / close_lyrics_cmd / music_status_cmd 4 个 Tauri command
  T7: lib.rs 含 tray menu item 'music' + 'lyrics'
  T8: lib.rs RunEvent::ExitRequested 调用 music::kill_music
  T9: Cargo.toml 含 winreg Windows-only 依赖
  T10: music.rs 含 start_music / open_lyrics_window / close_lyrics_window / music_status / read_music_port

Tauri shell 二进制(cargo build --release)必须已编译 → prisirai-shell.exe
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def http_get(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def http_post(url: str, body: dict, timeout: float = 5.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {"ok": False, "err": e.read().decode("utf-8")}


def find_companion_port() -> int:
    """陪聊 web 端口默认 18850,可被 --port 覆盖"""
    return 18850


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
    cport = find_companion_port()
    base = f"http://127.0.0.1:{cport}"
    print(f"[setup] companion @ {base}, music @ HKCU registry")
    print(f"[setup] current music_port = {find_music_port()}")

    # T1: GET /api/music/dispatch
    print("\n[T1] GET /api/music/dispatch")
    s, body = http_get(f"{base}/api/music/dispatch")
    j = json.loads(body) if s == 200 else {}
    check(j.get("ok"), True, f"dispatch ok (got {j.get('ok')})")
    check(j.get("source") in ("self", "lx", "none"), True,
          f"source ∈ self/lx/none (got {j.get('source')!r})")
    if j.get("source") == "self":
        check_true(j.get("self_running"), "self_running true when source=self")
        check_true(isinstance(j.get("self_port"), int), "self_port is int")
        check_true(j.get("self_url", "").startswith("http://"), "self_url http:// prefix")
        check_true(j.get("self_lyrics_url", "").endswith("/lyrics"), "self_lyrics_url ends with /lyrics")
        check_true(isinstance(j.get("tauri_dispatch_hint"), dict), "tauri_dispatch_hint is dict")

    # T2: POST /api/music/start(应 OK + 返 port + url)
    print("\n[T2] POST /api/music/start")
    s, j2 = http_post(f"{base}/api/music/start", {})
    check(j2.get("ok"), True, f"start ok (got {j2.get('ok')})")
    check_true(isinstance(j2.get("port"), int) and j2.get("port") > 0, f"port>0 (got {j2.get('port')})")
    check_true(j2.get("url", "").startswith("http://"), "url http:// prefix")

    # T3: 语伴 index.html — M3.29.8 后音乐 UI 已完全解耦
    #     头部不再有 btnMusicLauncher / 底部不再有 music-bar
    #     启动入口只在 PrisirAI Tauri 托盘菜单
    print("\n[T3] 语伴 index.html 不再有音乐 UI(已解耦)")
    s, html = http_get(f"{base}/static/index.html")
    check(s, 200, f"index.html status (got {s})")
    check_true('id="btnStart"' in html, "btnStart 启动按钮存在")
    check_true('id="btnMusicLauncher"' not in html, "已移除 btnMusicLauncher")
    check_true('id="music-bar"' not in html, "已移除 music-bar")
    check_true('id="musicLaunchToast"' not in html, "已移除 musicLaunchToast")
    check_true('语伴' in html, "title 已改名 语伴")
    check_true('陪聊' not in html.split('<header>')[1].split('</header>')[0] if '<header>' in html else True,
               "header 区无 陪聊 字样")
    check_true('(M3.24)' not in html, "settings 标题无 (M3.24)")
    check_true('(M3.27)' not in html, "settings 标题无 (M3.27)")
    check_true('M3.24 设置' not in html and 'M3.27 设置' not in html,
               "按钮文案无 M3.24/M3.27 命名")

    # T4: 语伴 app.js — 不再有 dispatchMusic / showMusicToast
    #     启动/挂断由 btnStart 二态按钮控制
    print("\n[T4] 语伴 app.js 不再有音乐分发函数")
    s, js = http_get(f"{base}/static/app.js")
    check(s, 200, f"app.js status (got {s})")
    check_true("dispatchMusic" not in js, "已移除 dispatchMusic 函数")
    check_true("showMusicToast" not in js, "已移除 showMusicToast 函数")
    check_true("setupMusicBar" not in js, "已移除 setupMusicBar 函数")
    check_true("startSession" in js and "stopSession" in js, "startSession/stopSession 存在")
    check_true('id="btnStart"' in html or "btnStart" in js, "btnStart 绑定到 start/stopSession")

    # T5: tauri.conf.json lyrics-window 配置
    print("\n[T5] tauri.conf.json lyrics-window")
    conf_path = ROOT / "prisiragent-tauri/src-tauri/tauri.conf.json"
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    wins = conf.get("app", {}).get("windows", [])
    lyrics_win = next((w for w in wins if w.get("label") == "lyrics-window"), None)
    check_true(bool(lyrics_win), "lyrics-window defined")
    if lyrics_win:
        check(lyrics_win.get("transparent"), True, "transparent=true")
        check(lyrics_win.get("alwaysOnTop"), True, "alwaysOnTop=true")
        check(lyrics_win.get("decorations"), False, "decorations=false")
        check(lyrics_win.get("skipTaskbar"), True, "skipTaskbar=true")
        check(lyrics_win.get("backgroundColor"), "#00000000", "backgroundColor=#00000000")
        check_true("lyrics" in lyrics_win.get("url", ""), "url contains lyrics")

    # T6: lib.rs 注册 4 个 Tauri commands
    print("\n[T6] lib.rs 4 个 music Tauri commands")
    lib_rs = (ROOT / "prisiragent-tauri/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    for cmd in ["start_music_cmd", "open_lyrics_cmd", "close_lyrics_cmd", "music_status_cmd"]:
        check_true(f"fn {cmd}" in lib_rs, f"fn {cmd} defined")
    check_true("start_music_cmd, open_lyrics_cmd, close_lyrics_cmd, music_status_cmd" in lib_rs,
              "all 4 commands in invoke_handler")

    # T7: tray menu items
    print("\n[T7] lib.rs tray menu items music + lyrics")
    check_true('"music"' in lib_rs, "tray item 'music'")
    check_true('"lyrics"' in lib_rs, "tray item 'lyrics' toggle")

    # T8: RunEvent ExitRequested 调用 kill_music
    print("\n[T8] lib.rs RunEvent::ExitRequested → kill_music")
    check_true("ExitRequested" in lib_rs, "ExitRequested handler")
    check_true("music::kill_music" in lib_rs, "kill_music called on exit")

    # T9: Cargo.toml 含 winreg
    print("\n[T9] Cargo.toml winreg Windows-only 依赖")
    cargo = (ROOT / "prisiragent-tauri/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    check_true('winreg' in cargo, "winreg in Cargo.toml")
    check_true("cfg(windows)" in cargo, "cfg(windows) target guard")

    # T10: music.rs 模块函数
    print("\n[T10] music.rs 模块")
    music_rs = (ROOT / "prisiragent-tauri/src-tauri/src/music.rs").read_text(encoding="utf-8")
    for fn_name in ["start_music", "kill_music", "open_lyrics_window", "close_lyrics_window",
                    "music_status", "read_music_port"]:
        check_true(f"fn {fn_name}" in music_rs, f"fn {fn_name} defined")

    # T11: Tauri shell 二进制已编译
    print("\n[T11] Tauri shell 已编译")
    shell_exe = ROOT / "prisiragent-tauri/src-tauri/target/release/prisirai-shell.exe"
    check_true(shell_exe.exists(), f"prisirai-shell.exe exists")
    if shell_exe.exists():
        size_mb = shell_exe.stat().st_size / (1024 * 1024)
        print(f"  size: {size_mb:.1f} MB")

    total = len(results)
    passed = sum(results)
    print(f"\n=== M3.29.4 Tauri 托盘菜单集成: {passed}/{total} pass ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())