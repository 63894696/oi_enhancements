# -*- coding: utf-8 -*-
"""sherpa_onnx_local_server.py — 本地 streaming-zipformer-zh-14M WebSocket server。

替代方案(2026-09-17 装机笔记):
- FunASR 1.3.30 + Paraformer-zh-streaming: 848MB PyTorch 模型下载极慢(20+ 小时未完)
  + 每次 reset 后 4MB/min 反复重下,综合不划算
- sherpa-onnx 1.13.7 + streaming-zipformer-zh-14M: 70MB ONNX,CPU 实时因子 ~0.1
  + pip 装好即用,无 torch 依赖

协议跟 companion/companion_asr_providers.py:415-528 的 LocalFunAsrSession 完全兼容,
也跟 funasr_local_server.py 一致(测试代码可以共用)。

启动:
    python sherpa_onnx_local_server.py \\
        --model-dir companion/asr_models/zh14m \\
        --port 10096

模型目录结构(解压 tar.bz2 后):
    zh14m/
      tokens.txt
      encoder.onnx        (fp32 约 60MB,int8 约 16MB)
      decoder.onnx        (fp32 约 1MB)
      joiner.onnx         (fp32 约 1MB)

协议:
    client → server: 原始 PCM 字节流 (16kHz, 16bit, mono)
    client → server: {"end": true} 结束
    server → client:
        {"mode": "ready"}                  服务就绪
        {"mode": "2pass-online", "text": "..."}  partial
        {"mode": "2pass-offline", "text": "..."} final
        {"mode": "finished"}              识别结束
        {"mode": "error", "text": "..."}   异常
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import websockets

# 注:sherpa_onnx 模块级 import 只加载 wrapper,真正加载 onnx 模型在
# OnlineRecognizer.from_transducer() 时才发生。如果同一进程再开
# sherpa_onnx session 不会段错误(跟 funasr 不同)。

log = logging.getLogger("sherpa-onnx-local-server")

# 跟 funasr_local_server 协议对齐(单位都是 16kHz samples)
SR = 16000
SAMPLE_WIDTH = 2  # 16-bit
CHANNELS = 1
# sherpa-onnx zipformer streaming 是流式,无需外部 chunk 触发 — accept_waveform
# 每次累加样本,内部框架自动 decode_streams() 拿 partial。但为了让前端协议
# 跟 LocalFunAsrSession 完全一致(每 600ms 才有 partial 输出),我们仍然按
# chunk 切,喂够一个 chunk 就 decode_streams 拿 partial。
CHUNK_MS = 600
CHUNK_SAMPLES = SR * CHUNK_MS // 1000  # 9600


class SessionState:
    """每个 WebSocket 连接独立一份 state。"""

    def __init__(self, recognizer, tokens_lines: list[str]):
        self.recognizer = recognizer
        self.tokens_lines = tokens_lines
        self.stream = recognizer.create_stream()
        self.buf = bytearray()
        self.sample_buf = np.empty((0,), dtype=np.float32)
        self.last_partial: str = ""

    def feed_pcm(self, pcm_bytes: bytes) -> list[dict]:
        """吃一段 PCM bytes,内部按 chunk_size 切,跑 decode_streams 拿 partial。"""
        # bytes → float32 [-1, +1]
        new_samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self.sample_buf = np.concatenate([self.sample_buf, new_samples])
        results = []
        while len(self.sample_buf) >= CHUNK_SAMPLES:
            chunk = self.sample_buf[: CHUNK_SAMPLES]
            self.sample_buf = self.sample_buf[CHUNK_SAMPLES:]
            # zipformer streaming transducer,喂一段就跑
            self.stream.accept_waveform(SR, chunk)
            self.recognizer.decode_streams([self.stream])
            # API 1.13:结果要从 recognizer.get_result(stream) 拿
            text = self.recognizer.get_result(self.stream).strip()
            if text and text != self.last_partial:
                results.append({"text": text})
                self.last_partial = text
        return results

    def finalize(self) -> list[dict]:
        """end=True:输入尾段空样本触发尾部 decode。"""
        if len(self.sample_buf) > 0:
            chunk = self.sample_buf
            self.sample_buf = np.empty((0,), dtype=np.float32)
            self.stream.accept_waveform(SR, chunk)
        # tail padding 触发 zipformer 内部输入帧对齐
        self.stream.accept_waveform(SR, np.zeros(int(SR * 0.3), dtype=np.float32))
        self.stream.input_finished()  # 标记流结束
        self.recognizer.decode_streams([self.stream])
        text = self.recognizer.get_result(self.stream).strip()
        if not text:
            return []
        return [{"text": text}]

    def reset(self):
        """识别完一段后,重新开 stream(避免上一段 cache 串到下一段)。"""
        self.stream = self.recognizer.create_stream()
        self.last_partial = ""


def _find_onnx_files(model_dir: Path) -> tuple[str, str, str, str]:
    """在 model_dir 下找 tokens.txt + encoder/decoder/joiner。

    模型文件命名可能是 'encoder.onnx' 也可能是 'encoder-epoch-99-avg-1.onnx'
    (zipformer-14M mobile 走的是 epoch-99 后缀)。优先 int8,再 fp32,再 glob 通配。
    """
    tokens = model_dir / "tokens.txt"
    if not tokens.exists():
        raise FileNotFoundError(f"tokens.txt not found in {model_dir}")

    def pick(prefix: str) -> Path:
        # 先 exact:encoder.onnx / encoder.int8.onnx
        for ext in (".int8.onnx", ".onnx"):
            cand = model_dir / f"{prefix}{ext}"
            if cand.exists():
                return cand
        # 再 glob:encoder-epoch-* + .int8.onnx / .onnx
        for ext in (".int8.onnx", ".onnx"):
            matches = sorted(model_dir.glob(f"{prefix}-*{ext}"))
            if matches:
                return matches[0]
        raise FileNotFoundError(
            f"{prefix}(.int8.onnx|.onnx) 不在 {model_dir} 下"
        )

    encoder = pick("encoder")
    decoder = pick("decoder")
    joiner = pick("joiner")
    return str(tokens), str(encoder), str(decoder), str(joiner)


def load_recognizer(model_dir: Path, num_threads: int = 2):
    """从目录里加载 sherpa-onnx OnlineRecognizer(transducer)。"""
    # 延迟 import,跟 funasr_local_server 一致 — 让模块级 import 不触发 onnx 加载
    import sherpa_onnx

    tokens, encoder, decoder, joiner = _find_onnx_files(model_dir)
    log.info("loading model: tokens=%s encoder=%s decoder=%s joiner=%s",
             tokens, encoder, decoder, joiner)
    log.info("model sizes: encoder=%.1fMB decoder=%.1fMB joiner=%.1fMB",
             Path(encoder).stat().st_size / 1024 / 1024,
             Path(decoder).stat().st_size / 1024 / 1024,
             Path(joiner).stat().st_size / 1024 / 1024)

    t0 = time.time()
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=tokens,
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        num_threads=num_threads,
        sample_rate=SR,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
    )
    log.info("model loaded in %.2fs", time.time() - t0)
    return recognizer


async def handle_conn(ws, recognizer):
    state = SessionState(recognizer, [])
    try:
        await ws.send(json.dumps({"mode": "ready"}))
        log.info("session opened")

        async for raw in ws:
            if isinstance(raw, str):
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("end") is True:
                    finals = state.finalize()
                    for r in finals:
                        txt = r.get("text", "")
                        if txt:
                            await ws.send(json.dumps({"mode": "2pass-offline", "text": txt}))
                    await ws.send(json.dumps({"mode": "finished"}))
                    return
                continue

            results = state.feed_pcm(bytes(raw))
            for r in results:
                txt = r.get("text", "")
                if txt:
                    await ws.send(json.dumps({"mode": "2pass-online", "text": txt}))

    except websockets.ConnectionClosed:
        log.info("client closed")
    except Exception as e:
        log.exception("session error")
        try:
            await ws.send(json.dumps({"mode": "error", "text": f"{type(e).__name__}: {e}"}))
        except Exception:
            pass


async def main_async(args):
    model_dir = Path(args.model_dir)
    if not model_dir.is_dir():
        log.error("model_dir not found: %s", model_dir)
        sys.exit(1)

    recognizer = load_recognizer(model_dir, num_threads=args.threads)

    async def on_conn(ws):
        await handle_conn(ws, recognizer)

    log.info("starting WebSocket server on ws://0.0.0.0:%d/", args.port)
    async with websockets.serve(on_conn, "0.0.0.0", args.port, max_size=8 * 1024 * 1024):
        log.info("listening, ctrl+C to stop")
        await asyncio.Future()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10096,
                    help="WebSocket port (跟 funasr 默认 10095 错开,避免本地双开冲突)")
    ap.add_argument("--model-dir", default="asr_models/zh14m",
                    help="解压后的模型目录(含 tokens.txt + encoder/decoder/joiner .onnx)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        log.info("stopped by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
