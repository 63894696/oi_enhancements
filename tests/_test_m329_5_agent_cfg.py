# -*- coding: utf-8 -*-
"""
M3.29.5 agent-only 配置 + 自然语言意图 e2e

覆盖:
  T1: GET /api/agent/cfg/list 返回所有 cfg path + schema
  T2: GET /api/agent/cfg/get(path) 单读
  T3: POST /api/agent/cfg/set 单写 + 校验失败返 ok:False + err
  T4: POST /api/agent/cfg/set 批量 + 范围校验
  T5: 自然语言意图 — 歌词(大点/小点/字号数字/逐字/逐行/隐藏/显示/提前延后)
  T6: 自然语言意图 — 音量(轻/大/数字/静音/取消静音)
  T7: 自然语言意图 — 播放模式(单曲循环/列表循环/随机)
  T8: 自然语言意图 — 控制(下一首/上一首/暂停/继续/停止)
  T9: 自然语言意图 — 搜歌(按 X 歌单/放 X/听 X/播 X/搜 X/找 X/play X)
  T10: 不匹配的 NL 文本 → matched:False + fallback_to_llm:True
  T11: 颜色 cfg 校验(hex color)
  T12: enum cfg 校验(playback.mode 必须是 sequential/shuffle/repeat_one)
  T13: range cfg 校验(font_size 必须 [12,96])
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def find_music_port() -> int | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PrisirAI") as k:
            v, _ = winreg.QueryValueEx(k, "music_port")
            return int(v)
    except Exception:
        return None


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

    # T1: cfg list
    print("\n[T1] GET /api/agent/cfg/list")
    s, body = http_get(f"{base}/api/agent/cfg/list")
    j = json.loads(body) if s == 200 else {}
    check_true(j.get("ok"), f"list ok (status={s})")
    items = j.get("items") or []
    check_true(isinstance(items, list) and len(items) >= 14, f"items list >= 14 (got {len(items) if isinstance(items, list) else 'N/A'})")
    paths = [it.get("path") for it in items if isinstance(it, dict)]
    expected_paths = ["lyrics.font_size", "lyrics.color", "lyrics.mode",
                      "lyrics.delay_ms", "lyrics.opacity",
                      "playback.volume", "playback.mode", "playback.muted",
                      "music.root", "music.source", "music.lyrics_provider",
                      "playlist.auto_seed", "playlist.seed_max",
                      "lyrics.font_family"]
    for p in expected_paths:
        check_true(p in paths, f"cfg path {p}")
    # 检查每个 item 都含 agent_only=True(关键:不暴露 UI)
    for it in items:
        if isinstance(it, dict):
            check_true(it.get("agent_only") is True, f"agent_only=True on {it.get('path')}")

    # T2: cfg get
    print("\n[T2] GET /api/agent/cfg/get?path=lyrics.font_size")
    s, body = http_get(f"{base}/api/agent/cfg/get?path=lyrics.font_size")
    j = json.loads(body) if s == 200 else {}
    check_true(j.get("ok"), "get ok")
    check_true("value" in j, "value field present")

    # T3: set + 校验失败
    print("\n[T3] POST /api/agent/cfg/set 校验")
    # 合法
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.font_size": 48})
    check_true(j.get("ok"), "set valid ok")
    check(j.get("changed", {}).get("lyrics.font_size"), 48, "font_size=48")
    # 越界 — endpoint 返 ok=True 但 per-key results[...]['ok']=False
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.font_size": 200})
    res = j.get("results", {}).get("lyrics.font_size", {})
    check_true(res.get("ok") is False, "set out-of-range rejected (per-key)")
    check_true(bool(res.get("err")), "out-of-range err msg present")
    # 不存在 path
    s, j = http_post(f"{base}/api/agent/cfg/set", {"fake.path": 1})
    res = j.get("results", {}).get("fake.path", {})
    check_true(res.get("ok") is False, "set unknown path rejected (per-key)")

    # T4: 批量 + range
    print("\n[T4] POST 批量 + range")
    s, j = http_post(f"{base}/api/agent/cfg/set", {
        "lyrics.font_size": 36,
        "lyrics.color": "#c14d3a",
        "lyrics.opacity": 0.7,
    })
    check_true(j.get("ok"), "batch ok")
    for k, v in [("lyrics.font_size", 36), ("lyrics.color", "#c14d3a"), ("lyrics.opacity", 0.7)]:
        check(j.get("changed", {}).get(k), v, f"batch {k}={v}")

    # T5: NL 歌词
    print("\n[T5] 自然语言意图 - 歌词")
    lyrics_phrases = [
        ("歌词大点", "set", "lyrics.font_size"),
        ("字小一些", "set", "lyrics.font_size"),
        ("字号 64", "set", "lyrics.font_size"),
        ("逐字模式", "set", "lyrics.mode"),
        ("逐行模式", "set", "lyrics.mode"),
        ("隐藏歌词", "set", "lyrics.window_visible"),
        ("显示歌词", "set", "lyrics.window_visible"),
        ("歌词提前 500", "set", "lyrics.delay_ms"),
    ]
    for text, action, key in lyrics_phrases:
        s, j = http_post(f"{base}/api/agent/intent", {"text": text})
        if not j.get("ok"):
            results.append(False); print(f"  FAIL [{text}]: not ok"); continue
        intent = j.get("intent", {})
        check_true(intent.get("matched"), f"NL [{text}] matched")
        check(intent.get("action"), action, f"NL [{text}] action={action}")
        if action == "set":
            check_true(key in intent.get("payload", {}), f"NL [{text}] payload 含 {key}")

    # T6: NL 音量
    print("\n[T6] 自然语言意图 - 音量")
    vol_phrases = [
        ("声音轻一点", "set", "playback.volume"),
        ("音量 70", "set", "playback.volume"),
        ("静音", "set", "playback.muted"),
        ("取消静音", "set", "playback.muted"),
    ]
    for text, action, key in vol_phrases:
        s, j = http_post(f"{base}/api/agent/intent", {"text": text})
        intent = j.get("intent", {})
        check_true(intent.get("matched"), f"NL [{text}] matched")
        check_true(key in intent.get("payload", {}), f"NL [{text}] payload 含 {key}")

    # T7: NL 播放模式
    print("\n[T7] 自然语言意图 - 播放模式")
    mode_phrases = [
        ("单曲循环", "repeat_one"),
        ("列表循环", "sequential"),
        ("随机播放", "shuffle"),
    ]
    for text, expected_mode in mode_phrases:
        s, j = http_post(f"{base}/api/agent/intent", {"text": text})
        intent = j.get("intent", {})
        check_true(intent.get("matched"), f"NL [{text}] matched")
        check(intent.get("payload", {}).get("playback.mode"), expected_mode, f"NL [{text}] → {expected_mode}")

    # T8: NL 控制
    print("\n[T8] 自然语言意图 - 控制")
    ctrl_phrases = [
        ("换下一首", "next"),
        ("上一首", "prev"),
        ("暂停", "pause"),
        ("继续", "resume"),
        ("停止", "stop"),
    ]
    for text, expected_cmd in ctrl_phrases:
        s, j = http_post(f"{base}/api/agent/intent", {"text": text})
        intent = j.get("intent", {})
        check_true(intent.get("matched"), f"NL [{text}] matched")
        check(intent.get("payload", {}).get("action"), expected_cmd, f"NL [{text}] → {expected_cmd}")

    # T9: NL 搜歌
    print("\n[T9] 自然语言意图 - 搜歌")
    search_phrases = [
        ("放周杰伦的晴天", "周杰伦的晴天"),
        ("放七里香", "七里香"),
        ("听月光", "月光"),
        ("播稻香", "稻香"),
        ("搜歌 一路向北", "一路向北"),
        ("找一首夜曲", "一首夜曲"),
    ]
    for text, expected_q in search_phrases:
        s, j = http_post(f"{base}/api/agent/intent", {"text": text})
        intent = j.get("intent", {})
        check_true(intent.get("matched"), f"NL [{text}] matched")
        check(intent.get("payload", {}).get("query"), expected_q, f"NL [{text}] query={expected_q}")

    # T10: NL no-match
    print("\n[T10] 不匹配的 NL → fallback_to_llm")
    s, j = http_post(f"{base}/api/agent/intent", {"text": "今天天气不错"})
    # 当 matched=False 时,intent 字段可能不存在;fallback_to_llm 字段存在
    check_true(j.get("fallback_to_llm") is True, "fallback_to_llm=True")
    intent_obj = j.get("intent")
    if intent_obj is not None:
        check_true(not intent_obj.get("matched", True), "no-match → matched=False")
    else:
        check_true(j.get("ok") is not False, "endpoint ok when no-match (fallback path)")

    # T11: 颜色校验
    print("\n[T11] color 校验")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.color": "#f6f1e7"})
    check_true(j.get("results", {}).get("lyrics.color", {}).get("ok"), "valid color ok (per-key)")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.color": "red"})
    res = j.get("results", {}).get("lyrics.color", {})
    check_true(res.get("ok") is False, "invalid color rejected (per-key)")

    # T12: enum 校验
    print("\n[T12] enum 校验")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"playback.mode": "sequential"})
    check_true(j.get("results", {}).get("playback.mode", {}).get("ok"), "valid enum ok (per-key)")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"playback.mode": "garbage"})
    res = j.get("results", {}).get("playback.mode", {})
    check_true(res.get("ok") is False, "invalid enum rejected (per-key)")

    # T13: range 校验
    print("\n[T13] range 校验(font_size)")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.font_size": 12})
    check_true(j.get("results", {}).get("lyrics.font_size", {}).get("ok"), "font_size=12 (lower bound) ok")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.font_size": 96})
    check_true(j.get("results", {}).get("lyrics.font_size", {}).get("ok"), "font_size=96 (upper bound) ok")
    s, j = http_post(f"{base}/api/agent/cfg/set", {"lyrics.font_size": 11})
    res = j.get("results", {}).get("lyrics.font_size", {})
    check_true(res.get("ok") is False, "font_size=11 rejected (per-key)")

    total = len(results)
    passed = sum(results)
    print(f"\n=== M3.29.5 agent-only cfg + NL: {passed}/{total} pass ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())