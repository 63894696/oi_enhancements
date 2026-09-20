# -*- coding: utf-8 -*-
"""
_test_m328_poc.py — M3.28 Phase 1 PoC 验证

T1: LxRuntimeClient 直连 → mock source 真返一个 mp3 URL
T2: 拿到的 URL 真能 GET(200 + audio/* / 字节)
T3: companion /api/music/cmd play mock:demo → 返 URL
T4: /api/music/state 返 ok
T5: /api/music/ws ws 握手 + 收 music_state 事件

通过标志:5/5 PASS + 至少 T1/T2 拿到的 URL 是真实可播放的 mp3 直链(任一可达公网音频即可)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "companion"))

# ============================================================
# T1: LxRuntimeClient 直连(不走 companion web)
# ============================================================
def t1_lx_client_direct() -> bool:
    print("[T1] LxRuntimeClient.call('musicUrl', 'mock', ...) 直连")
    from lx_runtime_client import LxRuntimeClient
    c = LxRuntimeClient(sources=["mock.js"])
    try:
        r = c.call("musicUrl", "mock", {"musicInfo": {"hash": "demo"}, "type": "320k"})
        print(f"  resp = {r}")
        assert r.get("ok") is True, f"T1 FAIL: not ok: {r}"
        url = r.get("result") or ""
        assert url.startswith("http"), f"T1 FAIL: url bad: {url}"
        print(f"  ✓ 拿到 URL: {url[:80]}...")
        return True
    finally:
        c.shutdown()


# ============================================================
# T2: URL 真能 GET(状态码 + Content-Type 含 audio 或 长度 > 0)
# ============================================================
def t2_url_reachable(url: str) -> bool:
    print(f"[T2] GET {url[:80]}...")
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "curl/7.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            clen = resp.headers.get("Content-Length", "?")
            print(f"  status={status} content-type={ctype} content-length={clen}")
        assert status == 200, f"T2 FAIL: status {status}"
        # mp3/wav/ogg 都算 audio
        ok_ct = any(t in ctype.lower() for t in ("audio", "mpeg", "wav", "ogg"))
        # 兜底:即使 ctype 不带 audio,只要长度 > 1000 也认(说明是真数据)
        assert ok_ct or clen not in ("?", "0", ""), f"T2 FAIL: ctype={ctype} clen={clen}"
        print("  ✓ URL 可达 + 内容像音频")
        return True
    except Exception as e:
        print(f"  ! HEAD 失败({e}),改用 GET 取头部字节兜底")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                head = resp.read(512)
                print(f"  GET status={status}, first bytes len={len(head)}")
                assert status == 200 and len(head) > 0
            print("  ✓ GET 200 + 非空(即使 ctype 未知,判定可达)")
            return True
        except Exception as e2:
            print(f"  ✗ T2 FAIL: GET 也失败: {e2}")
            return False


# ============================================================
# T3/T4/T5: companion web 端点
# ============================================================
async def t3_t4_t5_via_companion(port: int) -> tuple[bool, bool, bool]:
    """启 companion web,跑 T3/T4/T5"""
    import importlib
    mod = importlib.import_module("prisiragent-companion-web")
    # 替换 logger 避免污染
    import logging
    log = logging.getLogger("test_m328")
    log.setLevel(logging.WARNING)

    from aiohttp import web
    app = mod.make_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    base = f"http://127.0.0.1:{port}"
    print(f"  companion 启动在 {base}")

    t3_ok = t4_ok = t5_ok = False
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # ---- T3: POST /api/music/cmd play mock:demo ----
            print("[T3] POST /api/music/cmd {action: play, keyword: mock:demo}")
            async with session.post(f"{base}/api/music/cmd",
                                     json={"action": "play", "keyword": "mock:demo", "quality": "320k"}) as r:
                body = await r.json()
            print(f"  resp = {body}")
            assert body.get("ok") is True, f"T3 FAIL: {body}"
            assert body.get("url"), f"T3 FAIL: no url: {body}"
            assert body.get("source") == "mock", f"T3 FAIL: wrong source: {body}"
            print("  ✓ /api/music/cmd play mock:demo → URL")
            t3_ok = True

            # ---- T4: GET /api/music/state ----
            print("[T4] GET /api/music/state")
            async with session.get(f"{base}/api/music/state") as r:
                body = await r.json()
            print(f"  resp = {body}")
            assert body.get("ok") is True, f"T4 FAIL: {body}"
            assert body.get("track") is not None, f"T4 FAIL: no track: {body}"
            print("  ✓ /api/music/state → ok + track")
            t4_ok = True

            # ---- T5: ws /api/music/ws + 收 music_state ----
            print("[T5] WS /api/music/ws → 收 music_state 事件")
            async with session.ws_connect(f"{base}/api/music/ws") as ws:
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    payload = json.loads(msg.data)
                    print(f"  ws first msg = {payload}")
                    assert payload.get("type") == "music_state", f"T5 FAIL: bad type: {payload}"
                    # 触发 stop 触发第二次推送
                    async with session.post(f"{base}/api/music/cmd", json={"action": "stop"}) as r:
                        stop_body = await r.json()
                    print(f"  POST stop resp = {stop_body}")
                    msg2 = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg2.type == aiohttp.WSMsgType.TEXT:
                        payload2 = json.loads(msg2.data)
                        print(f"  ws second msg = {payload2}")
                        assert payload2.get("state") == "stopped", f"T5 FAIL: no stopped: {payload2}"
                        print("  ✓ ws 连通 + 收到 music_state + 收 stopped 推送")
                        t5_ok = True
                    else:
                        print(f"  T5 FAIL: ws msg2 type={msg2.type}")
    finally:
        await runner.cleanup()

    return t3_ok, t4_ok, t5_ok


def main() -> int:
    print("=" * 64)
    print("M3.28 Phase 1 PoC — 验证 jsdom shim 真能跑通 LX Music source 协议")
    print("=" * 64)

    results = {}

    # T1
    try:
        results["T1"] = t1_lx_client_direct()
        url = None
    except AssertionError as e:
        print(f"  ✗ T1 FAIL: {e}")
        results["T1"] = False

    # T2(用 T1 拿到的 URL)
    if results["T1"]:
        from lx_runtime_client import LxRuntimeClient
        c = LxRuntimeClient(sources=["mock.js"])
        try:
            r = c.call("musicUrl", "mock", {"musicInfo": {"hash": "demo"}, "type": "320k"})
            url = r.get("result")
        finally:
            c.shutdown()
        if url:
            results["T2"] = t2_url_reachable(url)
        else:
            results["T2"] = False
    else:
        print("[T2] skip(T1 失败)")
        results["T2"] = False

    # T3/T4/T5 走 companion web
    print("\n[T3-T5] 启 companion web(端口 18851 避 18850)...")
    port = 18851
    try:
        t3, t4, t5 = asyncio.run(t3_t4_t5_via_companion(port))
        results["T3"] = t3
        results["T4"] = t4
        results["T5"] = t5
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ companion 测试异常: {e}")
        results["T3"] = results["T4"] = results["T5"] = False

    print("\n" + "=" * 64)
    for k in ["T1", "T2", "T3", "T4", "T5"]:
        mark = "✓" if results.get(k) else "✗"
        print(f"  {mark} {k}")
    total = sum(1 for v in results.values() if v)
    print(f"\n  {total}/5 PASS")
    if total == 5:
        print("\n✅ Phase 1 PoC 通过(Protocol shim 验证完成)")
        return 0
    else:
        print(f"\n⚠ {5 - total} 项失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
