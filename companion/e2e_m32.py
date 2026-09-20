# -*- coding: utf-8 -*-
# e2e_m32.py — M3.2 麦克风管道端到端验证(2026-09-16)
# 模拟:连 WS → 发 3 段 audio_chunk(虚构 webm bytes)→ 发 audio_end
# 验收:1) server 返 audio_saved 2) media/{sid}/ 下有非零文件 3) jsonl 多一条 src=mic turn
import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

DATA_DIR = Path.home() / ".local" / "share" / "prisiragent-companion"
PORT = 18850


async def main() -> int:
    sid = "m32e2e" + str(int(time.time()))[-6:]
    url = f"ws://127.0.0.1:{PORT}/ws?sid={sid}"
    print(f"[m32-e2e] connect {url}")
    async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
        # 1) hello
        hello_raw = await ws.recv()
        hello = json.loads(hello_raw)
        print(f"[m32-e2e] hello type={hello.get('type')} sid={hello.get('sid')} continue.hint={hello.get('continue', {}).get('hint', '')[:40]}")
        assert hello.get("type") == "hello"
        assert hello.get("sid") == sid

        # 2) 发 3 段 audio_chunk(每段 2KB 虚构 webm bytes)
        for i in range(3):
            payload = bytes((j * (i + 1) + 17) & 0xFF for j in range(2048))
            head = json.dumps({"type": "audio_chunk", "mime": "audio/webm;codecs=opus"}).encode("utf-8")
            frame = head + b"\n" + payload
            await ws.send(frame)
            print(f"[m32-e2e] sent audio_chunk #{i} bytes={len(payload)}")
            await asyncio.sleep(0.05)

        # 3) 发 audio_end(durMs 3000,bytes 已累计,服务端自己重算)
        end_head = json.dumps({"type": "audio_end", "durMs": 600}).encode("utf-8")
        await ws.send(end_head + b"\n")
        print("[m32-e2e] sent audio_end")

        # 4) 等 server 返 audio_saved + user_echo
        got_saved = False
        got_echo = False
        deadline = time.time() + 5
        while time.time() < deadline and not (got_saved and got_echo):
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            m = json.loads(raw)
            print(f"[m32-e2e] recv type={m.get('type')} keys={list(m.keys())}")
            if m.get("type") == "audio_saved":
                got_saved = True
                saved_path = DATA_DIR / m["path"]
                print(f"[m32-e2e] audio_saved: {m['path']} durMs={m['durMs']} bytes={m['bytes']}")
            elif m.get("type") == "user_echo":
                got_echo = True
                print(f"[m32-e2e] user_echo: {m['text'][:60]}")

        assert got_saved, "没收到 audio_saved"
        assert got_echo, "没收到 user_echo"

        # 5) 验文件存在 + 大小
        assert saved_path.is_file(), f"音频文件不存在: {saved_path}"
        file_size = saved_path.stat().st_size
        print(f"[m32-e2e] file on disk: {saved_path} size={file_size}B")
        assert file_size == 3 * 2048, f"文件大小不符: {file_size} != 6144"

        # 6) 验 jsonl 多一条 src=mic
        jsonl = DATA_DIR / "chats" / f"{sid}.jsonl"
        assert jsonl.is_file(), f"jsonl 不存在: {jsonl}"
        lines = jsonl.read_text(encoding="utf-8").splitlines()
        print(f"[m32-e2e] jsonl lines: {len(lines)}")
        mic_turns = [json.loads(ln) for ln in lines if json.loads(ln).get("src") == "mic"]
        print(f"[m32-e2e] mic turns: {len(mic_turns)}")
        assert len(mic_turns) == 1, f"应有 1 条 mic turn,实际 {len(mic_turns)}"
        t = mic_turns[0]
        print(f"[m32-e2e] mic turn: text={t['text'][:50]} dur_ms={t.get('dur_ms')} bytes={t.get('bytes')} path={t.get('audio_path')}")
        assert t.get("bytes") == 6144
        assert t.get("mime", "").startswith("audio/webm")

        print("[m32-e2e] ✅ ALL PASS")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
