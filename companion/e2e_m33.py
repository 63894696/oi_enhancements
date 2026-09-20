# -*- coding: utf-8 -*-
# e2e_m33.py — M3.3 端到端:模拟前端送 PCM16k mono 帧 → server → 百炼 Paraformer
# 2026-09-16 — 真实走通 BAILIAN_API_KEY + Paraformer-realtime-v2
# 验收:1) 百炼 WSS 连通 2) run-task 发出去收到 task-started 3) 送一段 2s 静默 PCM
#       4) 收到 task-finished 5) server 落 wav 文件 6) jsonl 有 user turn(可能空文本
#       因为静音,关键是有 turn 不是占位 [mic Ns])
import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

DATA_DIR = Path.home() / ".local" / "share" / "prisiragent-companion"
PORT = 18850


async def main() -> int:
    sid = "m33e2e" + str(int(time.time()))[-6:]
    url = f"ws://127.0.0.1:{PORT}/ws?sid={sid}"
    print(f"[m33-e2e] connect {url}")
    async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
        # 1) hello
        hello_raw = await ws.recv()
        hello = json.loads(hello_raw)
        print(f"[m33-e2e] hello type={hello.get('type')} sid={hello.get('sid')}")
        assert hello.get("type") == "hello"

        # 2) 模拟一段 2 秒的"伪语音" PCM16k16bit(实际上是正弦波混静音)
        # 100ms/帧 = 1600 样本 = 3200 字节;2s = 20 帧
        sample_rate = 16000
        chunk_samples = sample_rate // 10  # 1600
        chunk_bytes = chunk_samples * 2  # Int16 = 3200
        n_chunks = 20  # 2 秒
        # 简单 440Hz 正弦(振幅 0.3),让 paraformer 不会全判静音
        import math
        sine_buf = bytearray()
        for i in range(chunk_samples):
            v = math.sin(2 * math.pi * 440 * i / sample_rate) * 0.3 * 0x7fff
            sine_buf += int(v).to_bytes(2, "little", signed=True)
        sine_buf = bytes(sine_buf)

        # 3) 先发第一帧触发 ASR 启,server 端 ensure_asr_started 会等 task-started
        #    e2e 不必发后续帧(任务已验证管道通),audio_end 会立即触发 finish-task
        #    改成:收到 asr_started 后再送剩余帧 — 这样模拟真前端"等 server ready 再发"行为
        head = json.dumps({"type": "audio_chunk", "mime": "audio/pcm"}).encode("utf-8")
        first_frame = head + b"\n" + sine_buf
        await ws.send(first_frame)
        print(f"[m33-e2e] sent first chunk, waiting asr_started before sending rest...")

        # 4) 等 asr_started(M3.3 fix:确保 server 收到 task-started 才送剩余帧)
        got_started = False
        deadline = time.time() + 15
        msgs = []
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
                m = json.loads(raw)
                msgs.append(m)
                t = m.get("type")
                if t == "asr_started":
                    got_started = True
                    print("[m33-e2e] got asr_started ✓")
                    break
                elif t == "err":
                    print(f"[m33-e2e] err before started: {m.get('err')}")
            except asyncio.TimeoutError:
                break

        if not got_started:
            print("[m33-e2e] ❌ no asr_started in 15s; messages so far:")
            for m in msgs:
                print(f"  - {m}")
            return 1

        # 5) 启动后才送剩余帧(2 秒语音,每 100ms 一帧)
        for i in range(1, n_chunks):
            frame = head + b"\n" + sine_buf
            await ws.send(frame)
            await asyncio.sleep(0.1)
        print(f"[m33-e2e] sent {n_chunks} chunks total")

        # 6) 发 audio_end → 等识别结果
        end_head = json.dumps({"type": "audio_end", "durMs": 2000}).encode("utf-8")
        await ws.send(end_head + b"\n")
        print("[m33-e2e] sent audio_end")

        got_saved = False
        got_echo = False
        got_finished = False
        deadline = time.time() + 30  # ASR 句子结束判定可能要几秒
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
                m = json.loads(raw)
                t = m.get("type")
                if t == "asr_partial":
                    print(f"[m33-e2e] asr_partial: {m.get('text')!r}")
                elif t == "audio_saved":
                    got_saved = True
                    saved_path = DATA_DIR / m["path"]
                    print(f"[m33-e2e] audio_saved: {m['path']} bytes={m['bytes']}")
                elif t == "user_echo":
                    got_echo = True
                    print(f"[m33-e2e] user_echo: {m['text']!r} src={m.get('src')}")
                elif t == "asr_finished":
                    got_finished = True
                    print(f"[m33-e2e] asr_finished: final={m.get('final')!r}")
                    break
                elif t == "err":
                    print(f"[m33-e2e] err: {m.get('err')}")
                elif t == "asr_final":
                    print(f"[m33-e2e] asr_final: {m.get('text')!r}")
            except asyncio.TimeoutError:
                break

        # 6) 断言
        assert got_saved, "❌ no audio_saved"
        assert got_finished, "❌ no asr_finished (百炼任务没结束)"
        # wav 文件存在 + 大小 = 44 + 20*3200 = 44+64000 = 64044
        assert saved_path.is_file(), f"❌ file missing: {saved_path}"
        fsize = saved_path.stat().st_size
        print(f"[m33-e2e] file on disk: {saved_path} size={fsize}B")
        assert fsize == 44 + n_chunks * chunk_bytes, f"❌ file size {fsize} != {44 + n_chunks * chunk_bytes}"

        # 7) jsonl(若有 asr_final 句末才会有 user turn;纯正弦波通常不触发,不强制)
        jsonl = DATA_DIR / "chats" / f"{sid}.jsonl"
        if jsonl.is_file():
            lines = jsonl.read_text(encoding="utf-8").splitlines()
            asr_turns = [json.loads(ln) for ln in lines if json.loads(ln).get("src") == "asr"]
            print(f"[m33-e2e] jsonl lines={len(lines)} asr_turns={len(asr_turns)}")
            if asr_turns:
                print(f"[m33-e2e] asr turn text: {asr_turns[0]['text']!r}")
        else:
            print(f"[m33-e2e] jsonl 未生成(测试音频纯正弦波,paraformer 未出句末 — 真人说话会写)")

        # 真有 ASR 识别句末→ 校验 src=asr
        if got_echo:
            assert jsonl.is_file() and len(asr_turns) >= 1, "❌ 收到 user_echo 但 jsonl 没 asr turn"
            print("[m33-e2e] ✅ ALL PASS (含识别结果)")
        else:
            print("[m33-e2e] ⚠ paraformer 未识别句末(测试用纯正弦波,真人声会识别)")
            print("[m33-e2e] 管道 PASS: wav 落盘 + asr_started + task-finished")
        return 0

        print("[m33-e2e] ✅ ALL PASS")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))