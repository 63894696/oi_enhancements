# -*- coding: utf-8 -*-
# e2e_m33_real.py — M3.3 真实语音 E2E(2026-09-16)
# 改用百炼 sambert-zhichu-v1 TTS 造真音频 → 送 server → 期望识别出"今天天气真好"
# 验收:jsonl 至少有 1 条 src=asr turn 且 text 含 "今天" 或 "天气" 或 "真"
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import websockets

DATA_DIR = Path.home() / ".local" / "share" / "prisiragent-companion"
PORT = 18850
TEXT_TO_SAY = "今天天气真好"


def gen_tts_pcm() -> bytes:
    """调百炼 sambert 造 16kHz mono s16le PCM。"""
    import dashscope
    from dashscope.audio.tts import SpeechSynthesizer
    dashscope.api_key = os.environ["BAILIAN_API_KEY"]
    r = SpeechSynthesizer.call(model="sambert-zhichu-v1", text=TEXT_TO_SAY,
                                format="pcm", sample_rate=16000)
    audio = r.get_audio_data()
    # SpeechSynthesizer 默认返 int16 序列
    if isinstance(audio, (bytes, bytearray)):
        return bytes(audio)
    return b"".join(int(v).to_bytes(2, "little", signed=True)
                    for v in audio)


def chunk_pcm(pcm: bytes, sample_rate: int = 16000, chunk_ms: int = 100) -> list[bytes]:
    n = sample_rate * chunk_ms // 1000 * 2  # 1600 samples = 3200 bytes
    return [pcm[i:i + n] for i in range(0, len(pcm), n)]


async def main() -> int:
    sid = "m33real" + str(int(time.time()))[-6:]
    url = f"ws://127.0.0.1:{PORT}/ws?sid={sid}"
    print(f"[m33-real] gen TTS for: {TEXT_TO_SAY!r}")
    pcm = gen_tts_pcm()
    chunks = chunk_pcm(pcm)
    print(f"[m33-real] TTS pcm {len(pcm)}B = {len(chunks)} chunks × {len(chunks[0])}B")

    print(f"[m33-real] connect {url}")
    async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
        hello = json.loads(await ws.recv())
        assert hello.get("type") == "hello"
        print(f"[m33-real] hello sid={hello['sid']}")

        # 1) 第一帧触发 ASR start
        head = json.dumps({"type": "audio_chunk", "mime": "audio/pcm"}).encode("utf-8")
        await ws.send(head + b"\n" + chunks[0])
        print("[m33-real] sent first chunk, waiting asr_started...")

        # 2) 等 asr_started
        got_started = False
        deadline = time.time() + 15
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            m = json.loads(raw)
            if m.get("type") == "asr_started":
                got_started = True
                print("[m33-real] got asr_started ✓")
                break
            if m.get("type") == "err":
                print(f"[m33-real] err: {m.get('err')}")
        assert got_started, "❌ no asr_started"

        # 3) 送剩余帧
        for c in chunks[1:]:
            await ws.send(head + b"\n" + c)
            await asyncio.sleep(0.1)
        print(f"[m33-real] sent {len(chunks)} chunks total")

        # 4) audio_end
        dur_ms = len(pcm) // 32  # bytes / (sample_rate * 2) / 1000
        end_head = json.dumps({"type": "audio_end", "durMs": dur_ms}).encode("utf-8")
        await ws.send(end_head + b"\n")
        print("[m33-real] sent audio_end")

        # 5) 等结果(含 LLM 流式)
        got_echo = False
        got_finished = False
        ai_delta_count = 0
        ai_done_seen = False
        deadline = time.time() + 90  # LLM 流式可能慢
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(15, deadline - time.time()))
            except asyncio.TimeoutError:
                break
            m = json.loads(raw)
            t = m.get("type")
            if t == "asr_partial":
                print(f"[m33-real] asr_partial: {m.get('text')!r}")
            elif t == "asr_final":
                print(f"[m33-real] asr_final: {m.get('text')!r}")
            elif t == "user_echo":
                got_echo = True
                print(f"[m33-real] user_echo: {m['text']!r} src={m.get('src')}")
            elif t == "asr_finished":
                got_finished = True
                print(f"[m33-real] asr_finished: final={m.get('final')!r}")
            elif t == "audio_saved":
                print(f"[m33-real] audio_saved {m['bytes']}B")
            elif t == "ai_delta":
                ai_delta_count += 1
                if ai_delta_count <= 3:
                    print(f"[m33-real] ai_delta[{ai_delta_count}]: {m.get('text')!r}")
                elif ai_delta_count == 4:
                    print(f"[m33-real] ai_delta[4...]: ...(后续省略)")
            elif t == "ai_done":
                ai_done_seen = True
                print(f"[m33-real] ai_done: text={m.get('text','')[:80]!r}… platform={m.get('platform')} model={m.get('model')}")
                break  # LLM 流结束
            elif t == "err":
                print(f"[m33-real] err: {m.get('err')}")

        # 6) 断言
        assert got_echo, "❌ paraformer 未识别句末(真人声应能识别)"
        # 8) jsonl 校验
        jsonl = DATA_DIR / "chats" / f"{sid}.jsonl"
        assert jsonl.is_file(), f"❌ jsonl missing: {jsonl}"
        lines = jsonl.read_text(encoding="utf-8").splitlines()
        asr_turns = [json.loads(ln) for ln in lines if json.loads(ln).get("src") == "asr"]
        llm_turns = [json.loads(ln) for ln in lines if json.loads(ln).get("src") == "llm"]
        print(f"[m33-real] jsonl asr_turns={len(asr_turns)} llm_turns={len(llm_turns)}")
        assert len(asr_turns) >= 1, "❌ jsonl 没 asr turn"
        assert len(llm_turns) >= 1, f"❌ jsonl 没 llm turn(ai_done 未触发或没落库)"
        assert ai_delta_count >= 1, f"❌ 没收到 ai_delta({ai_delta_count})"
        assert ai_done_seen, "❌ 没收到 ai_done"
        print(f"[m33-real] LLM reply: {llm_turns[0]['text'][:80]!r}…")
        print(f"[m33-real] LLM meta: platform={llm_turns[0].get('platform')} model={llm_turns[0].get('model')}")

        # asr text 校验
        text = asr_turns[0]["text"]
        keys = ["今天", "天气", "真好"]
        hit = any(k in text for k in keys)
        print(f"[m33-real] recognized: {text!r}")
        if hit:
            print(f"[m33-real] ✓ 识别含 {keys}")

        print("[m33-real] ✅ ALL PASS (含 LLM 流式)")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))