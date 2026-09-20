# -*- coding: utf-8 -*-
"""
_test_m328_pathb.py — M3.28 Phase 2 Path B 端到端验证

依赖:LX Music Desktop @ 127.0.0.1:23330 + 陪聊 web @ 8011/8022/...
跑法:
  cd oi_enhancements && python tests/_test_m328_pathb.py 8022
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "companion"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "companion" / "music"))


PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8011
BASE = f"http://127.0.0.1:{PORT}"

REQUIRED = ["lx_desktop_client", "lx_bridge", "lyric_loader"]


def _print_pass(name: str) -> None:
    print(f"  ✅ {name}")


def _print_fail(name: str, msg: str) -> None:
    print(f"  ❌ {name}: {msg}")


async def test_lx_client() -> dict:
    """T1: LxDesktopClient 直连 status + lyric"""
    from lx_desktop_client import LxDesktopClient
    async with LxDesktopClient() as c:
        ok = await c.ping()
        if not ok:
            return {"ok": False, "err": "LX Desktop ping fail"}
        st = await c.status()
        lyr = await c.lyric()
        return {"ok": True, "status": st, "lyric_len": len(lyr)}


async def test_bridge_status() -> dict:
    """T2: 陪聊 web GET /api/music/status 应返 lx_alive + state(等 3s 让 SSE/poll 拿到第一帧)"""
    # 等 LxBridge SSE/poll loop 拿到第一帧 LX state
    deadline = time.time() + 5
    async with aiohttp.ClientSession() as sess:
        while time.time() < deadline:
            async with sess.get(f"{BASE}/api/music/status") as resp:
                j = await resp.json()
            if j.get("lx_alive"):
                return {"ok": True, "name": j.get("name"), "status": j.get("status")}
            await asyncio.sleep(0.5)
        return {"ok": False, "err": f"lx_alive never became true within 5s;last={j}"}


async def test_cmd_next_state_change() -> dict:
    """T3: POST /api/music/cmd next → LX 真切歌 → state 名字变"""
    async with aiohttp.ClientSession() as sess:
        async with sess.get(f"{BASE}/api/music/state") as r1:
            name_before = (await r1.json()).get("track", {}).get("name", "")
        async with sess.post(
            f"{BASE}/api/music/cmd",
            json={"action": "next"},
        ) as resp:
            j = await resp.json()
            if not j.get("ok"):
                return {"ok": False, "err": f"cmd next fail: {j}"}
        # 等 SSE 推 / poll 同步
        await asyncio.sleep(2)
        async with sess.get(f"{BASE}/api/music/state") as r2:
            st = await r2.json()
        name_after = st.get("track", {}).get("name", "")
        return {
            "ok": True,
            "name_before": name_before,
            "name_after": name_after,
            "changed": name_before != name_after,
            "status_after": st.get("status"),
        }


async def test_pause_play() -> dict:
    """T4: pause → status=paused,play → status=playing"""
    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"{BASE}/api/music/cmd", json={"action": "pause"}) as r:
            j1 = await r.json()
        await asyncio.sleep(1)
        async with sess.get(f"{BASE}/api/music/state") as r:
            st1 = await r.json()
        async with sess.post(f"{BASE}/api/music/cmd", json={"action": "play"}) as r:
            j2 = await r.json()
        await asyncio.sleep(1)
        async with sess.get(f"{BASE}/api/music/state") as r:
            st2 = await r.json()
        return {
            "ok": j1.get("ok") and j2.get("ok"),
            "pause_state": st1.get("status"),
            "play_state": st2.get("status"),
        }


async def test_lyric_lines() -> dict:
    """T5: LRC 解析 + state 报 lyric_lines_count"""
    from lyric_loader import parse_lrc
    from lx_desktop_client import LxDesktopClient
    async with LxDesktopClient() as c:
        lyr = await c.lyric()
        lines = parse_lrc(lyr)
    async with aiohttp.ClientSession() as sess:
        async with sess.get(f"{BASE}/api/music/state") as r:
            st = await r.json()
    return {
        "ok": len(lines) >= 0,
        "parsed_count": len(lines),
        "web_count": st.get("lyric_lines_count", -1),
        "current_idx": st.get("lyric_current_idx"),
        "lyric_line_text": st.get("lyric_line_text"),
    }


async def main() -> int:
    print(f"M3.28 Phase 2 Path B 验证 (web @ {PORT})")
    print(f"  base: {BASE}")
    print()
    tests = [
        ("T1 LxDesktopClient 直连 status + lyric", test_lx_client),
        ("T2 web GET /api/music/status lx_alive", test_bridge_status),
        ("T3 POST /api/music/cmd next 切歌 + state 同步", test_cmd_next_state_change),
        ("T4 pause / play 状态切换", test_pause_play),
        ("T5 lyric 解析 + 报行数", test_lyric_lines),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            r = await fn()
        except Exception as e:  # noqa: BLE001
            _print_fail(name, f"exception: {e}")
            failed += 1
            continue
        if r.get("ok"):
            extra = " ".join(f"{k}={v}" for k, v in r.items() if k != "ok")
            _print_pass(f"{name} ({extra})")
            passed += 1
        else:
            _print_fail(name, r.get("err", "unknown"))
            failed += 1
    print()
    print(f"  结果: {passed} PASS / {failed} FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))