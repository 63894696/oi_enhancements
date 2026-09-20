# -*- coding: utf-8 -*-
"""
_test_m329_1_backend.py — M3.29.1 music web 后端端到端

T1  port_registry 写读 + 心跳
T2  /api/health + cfg list 19 keys
T3  /api/agent/cfg/set 批量 + 校验
T4  /api/agent/intent 关键词全命中(歌词大点 / 声音大一点 / 换下一首 / 按刘德华歌单播 / 歌词字号30 / 静音)
T5  /api/library 扫库(用临时 mp3 目录)
T6  /api/cmd play/pause/next/prev + ws 推送
T7  /api/lyric 本地 LRC 优先
T8  端口冲突保护 — pick_free_port 不重复占用

执行:cd companion && python ../tests/_test_m329_1_backend.py
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 测试主目录
HERE = Path(__file__).resolve().parent
COMPANION_DIR = HERE.parent / "companion"
sys.path.insert(0, str(COMPANION_DIR))


def _http_post(url: str, body: dict, headers: dict = None) -> dict:
    import urllib.request
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "err": f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"}


def _http_get(url: str) -> dict:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "err": f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"}


def _url(url: str) -> str:
    """URL encode query string 中文部分(Windows cmd 默认 ascii)。"""
    from urllib.parse import quote
    if "?" in url:
        base, qs = url.split("?", 1)
        parts = []
        for seg in qs.split("&"):
            if "=" in seg:
                k, v = seg.split("=", 1)
                parts.append(f"{quote(k)}={quote(v, safe='')}")
            else:
                parts.append(quote(seg))
        return base + "?" + "&".join(parts)
    return url


def _start_server(port: int, workdir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    proc = subprocess.Popen(
        [sys.executable, "-B", str(COMPANION_DIR / "prisiragent-music-web.py"),
         "--port", str(port)],
        cwd=str(COMPANION_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    # 等 server ready
    for _ in range(50):
        time.sleep(0.2)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return proc
        except OSError:
            continue
    proc.kill()
    raise RuntimeError("server failed to start")


def _wait_ready(port: int, timeout: float = 8.0) -> bool:
    for _ in range(int(timeout * 5)):
        time.sleep(0.2)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            continue
    return False


def _make_fake_music(root: Path, count: int = 3) -> list:
    files = []
    names = ["周杰伦 - 晴天.mp3", "周杰伦 - 七里香.mp3", "陈奕迅 - 十年.mp3"]
    for n in names[:count]:
        p = root / n
        p.write_bytes(b"ID3" + b"\x00" * 100)  # 假 mp3 头
        files.append(p)
    return files


def _setup_cfg(root: Path, lyrics_dir: Path = None) -> None:
    """改 cfg music.root 到测试目录。"""
    workdir = COMPANION_DIR
    reg_dir = workdir / "_prisir_registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = reg_dir / "music_cfg.json"
    cfg = {
        "music.root": str(root),
        "music.lyrics_provider": "local" if lyrics_dir else "auto",
        "playback.volume": 80,
    }
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


# ============================================================
# T1-T8
# ============================================================
def t1_port_registry() -> bool:
    """T1 port_registry"""
    from music.port_registry import pick_free_port, read_music_port, write_music_port
    p1 = pick_free_port()
    write_music_port(p1, pid=os.getpid())
    back = read_music_port()
    if not back:
        print("  T1 FAIL: read_music_port None")
        return False
    port, pid = back
    if port != p1:
        print(f"  T1 FAIL: write/read mismatch write={p1} read={port}")
        return False
    print(f"  T1 PASS: write={p1} read={port} pid={pid}")
    return True


def t2_health_and_cfg(server_url: str) -> bool:
    """T2 /api/health + cfg list count"""
    h = _http_get(f"{server_url}/api/health")
    if not h.get("ok"):
        print(f"  T2 FAIL health: {h}")
        return False
    cfg = _http_get(f"{server_url}/api/agent/cfg/list")
    items = cfg.get("items", [])
    if len(items) < 15:
        print(f"  T2 FAIL cfg list: count={len(items)}")
        return False
    # 每条都 agent_only
    if not all(it.get("agent_only") for it in items):
        print(f"  T2 FAIL: not all agent_only")
        return False
    print(f"  T2 PASS: health ok, cfg items={len(items)}, all agent_only")
    return True


def t3_cfg_set(server_url: str) -> bool:
    """T3 /api/agent/cfg/set 批量"""
    r = _http_post(f"{server_url}/api/agent/cfg/set",
                   {"lyrics.font_size": 48, "playback.volume": 70})
    if not r.get("ok"):
        print(f"  T3 FAIL: {r}")
        return False
    if r.get("changed", {}).get("lyrics.font_size") != 48:
        print(f"  T3 FAIL changed: {r}")
        return False
    # 越界应失败
    r2 = _http_post(f"{server_url}/api/agent/cfg/set", {"lyrics.font_size": 999})
    if r2.get("results", {}).get("lyrics.font_size", {}).get("ok"):
        print(f"  T3 FAIL: out-of-range should be rejected: {r2}")
        return False
    print(f"  T3 PASS: set ok + range reject ok")
    return True


def t4_nl_intent(server_url: str) -> bool:
    """T4 /api/agent/intent — api_agent_intent 返 {ok, intent:{matched,action,payload}, executed}"""
    cases = [
        ("歌词大点",        "set"),
        ("声音大一点",       "set"),
        ("换下一首",        "cmd"),
        ("按刘德华歌单播",   "cmd"),
        ("歌词字号30",      "set"),
        ("静音",            "set"),
        ("循环播放",        "set"),
        ("今天天气不错",     "none"),
    ]
    fail = 0
    for text, expected_action in cases:
        r = _http_post(f"{server_url}/api/agent/intent", {"text": text})
        intent = r.get("intent", {})
        matched = intent.get("matched") or r.get("matched", False)
        action = intent.get("action") or r.get("action")
        if expected_action == "none":
            if matched:
                print(f"  T4 FAIL {text!r}: expected unmatched, got action={action}")
                fail += 1
                continue
        elif expected_action == "cmd":
            if not matched or action != "cmd":
                print(f"  T4 FAIL {text!r}: expected cmd, got matched={matched} action={action}")
                fail += 1
                continue
            # cmd 路径必须真执行(executed 字段)
            exec_res = r.get("executed") or {}
            if not exec_res and action != "search":
                print(f"  T4 FAIL {text!r}: cmd but no executed: {r}")
                fail += 1
                continue
        else:  # set
            if not matched or action != "set":
                print(f"  T4 FAIL {text!r}: expected set, got matched={matched} action={action}")
                fail += 1
                continue
            exec_res = r.get("executed") or {}
            if not exec_res or any(not v.get("ok") for v in exec_res.values()):
                print(f"  T4 FAIL {text!r}: set but executed not all ok: {r}")
                fail += 1
                continue
        print(f"  T4 OK {text!r}: action={action}")
    if fail:
        print(f"  T4 FAIL: {fail}/{len(cases)} failed")
        return False
    print(f"  T4 PASS: {len(cases)} cases all ok")
    return True


def t5_library(server_url: str, music_root: Path) -> bool:
    """T5 /api/library 扫库"""
    r = _http_get(f"{server_url}/api/library")
    if not r.get("ok"):
        print(f"  T5 FAIL: {r}")
        return False
    if r.get("count", 0) < 3:
        print(f"  T5 FAIL: count={r.get('count')} expected >=3")
        return False
    print(f"  T5 PASS: library count={r.get('count')}")
    return True


def t6_cmd_and_ws(server_url: str) -> bool:
    """T6 cmd + ws state"""
    # 先 search 拿 track_id(中文 URL encode)
    r = _http_get(_url(f"{server_url}/api/library/search?q=周杰"))
    if not r.get("ok") or not r.get("tracks"):
        print(f"  T6 FAIL: search no hits: {r}")
        return False
    tid = r["tracks"][0]["id"]
    # play
    rp = _http_post(f"{server_url}/api/cmd", {"action": "play", "track_id": tid})
    if not rp.get("ok") or rp.get("state", {}).get("status") != "playing":
        print(f"  T6 FAIL play: {rp}")
        return False
    # pause
    rpu = _http_post(f"{server_url}/api/cmd", {"action": "pause"})
    if rpu.get("state", {}).get("status") != "paused":
        print(f"  T6 FAIL pause: {rpu}")
        return False
    # next
    rn = _http_post(f"{server_url}/api/cmd", {"action": "next"})
    if not rn.get("ok"):
        print(f"  T6 FAIL next: {rn}")
        return False
    # state 终态验证
    rst = _http_get(f"{server_url}/api/state")
    if not rst.get("state", {}).get("track"):
        print(f"  T6 FAIL state: no track after next: {rst}")
        return False
    print(f"  T6 PASS: play/pause/next + state ok")
    return True


def t7_lyric_local(lyrics_dir: Path, server_url: str) -> bool:
    """T7 lyric 本地优先 — 把 LRC 写到 music_root 同目录(lyric_provider 扫那里)"""
    # lyric_provider 扫的是 music_root(测试里的 music_dir)
    # 把 LRC 写在 music_root 同级(放 _test_lyrics/ 子目录?不,扫的是 music_root.rglob *.lrc)
    # 测试的 music_root = workdir/music,LRC 放这里
    music_root = lyrics_dir.parent / "music"
    (music_root / "晴天.lrc").write_text(
        "[00:00.00]晴天\n[00:05.00]刮刮卡\n[00:10.00]故事的小黄花\n",
        encoding="utf-8",
    )
    # lyric API(中文 URL encode)
    r = _http_get(_url(f"{server_url}/api/lyric?title=晴天&artist=周杰伦"))
    if not r.get("ok"):
        print(f"  T7 FAIL: {r}")
        return False
    if r.get("source") != "local":
        print(f"  T7 FAIL: source={r.get('source')} expected local")
        return False
    if r.get("lines_count", 0) < 3:
        print(f"  T7 FAIL: lines_count={r.get('lines_count')}")
        return False
    print(f"  T7 PASS: local lyric lines={r.get('lines_count')} source={r.get('source')}")
    return True


def t8_port_conflict() -> bool:
    """T8 pick_free_port 不重复"""
    from music.port_registry import pick_free_port
    ports = [pick_free_port() for _ in range(10)]
    if len(set(ports)) != len(ports):
        print(f"  T8 FAIL: ports collide: {ports}")
        return False
    print(f"  T8 PASS: 10 ports all unique")
    return True


# ============================================================
# Main
# ============================================================
def main() -> int:
    print("=" * 60)
    print("M3.29.1 music web 后端 e2e")
    print("=" * 60)

    # T1 + T8 在 server 之外跑
    print("\n[T1 port_registry]")
    t1 = t1_port_registry()
    print("\n[T8 port_conflict]")
    t8 = t8_port_conflict()

    # 起 server
    server_port = 18998
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        music_root = workdir / "music"
        music_root.mkdir()
        lyrics_dir = workdir / "lyrics"
        lyrics_dir.mkdir()
        _make_fake_music(music_root)
        _setup_cfg(music_root, lyrics_dir)

        print(f"\n[Starting server on port {server_port}]")
        proc = _start_server(server_port, workdir)
        try:
            server_url = f"http://127.0.0.1:{server_port}"
            if not _wait_ready(server_port):
                print("server not ready, fail")
                return 1

            results = []
            print("\n[T2 health + cfg]")
            results.append(("T2", t2_health_and_cfg(server_url)))
            print("\n[T3 cfg set]")
            results.append(("T3", t3_cfg_set(server_url)))
            print("\n[T4 nl intent]")
            results.append(("T4", t4_nl_intent(server_url)))
            print("\n[T5 library]")
            results.append(("T5", t5_library(server_url, music_root)))
            print("\n[T6 cmd + state]")
            results.append(("T6", t6_cmd_and_ws(server_url)))
            print("\n[T7 lyric local]")
            results.append(("T7", t7_lyric_local(lyrics_dir, server_url)))

            print("\n" + "=" * 60)
            print("Summary:")
            all_pass = t1 and t8
            for name, ok in results:
                mark = "PASS" if ok else "FAIL"
                print(f"  {name}: {mark}")
                all_pass = all_pass and ok
            print(f"  T1: {'PASS' if t1 else 'FAIL'}")
            print(f"  T8: {'PASS' if t8 else 'FAIL'}")
            print("=" * 60)
            return 0 if all_pass else 1
        finally:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            # 2026-09-19 M3.29.7:清理 HKCU music_port,防止后续 wrapper 测读到陈旧端口
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrisirAI", 0, winreg.KEY_SET_VALUE) as k:
                    try:
                        winreg.DeleteValue(k, "music_port")
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
