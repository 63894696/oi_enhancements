# -*- coding: utf-8 -*-
# companion_asr.py — 百炼 Paraformer-realtime-v2 WebSocket ASR 桥(2026-09-16 M3.3)
#
# 设计:
#   - 一通陪聊(session)= 一条 Paraformer 任务 = 一条上行 WSS + 一条下行 WSS 回调
#   - 上行:前端每 100ms 发一条 PCM16k Int16 mono 二进制帧
#   - 下行:百炼返 transcribed_text / sentence(部分/整句)
#   - 收尾:前端 audio_end → 我们发 finish-task → 等 task-finished → 关 ws
#
# 协议(阿里云百炼官方,2026-09-16 现行):
#   - URL: wss://dashscope.aliyuncs.com/api-ws/v1/inference
#   - 鉴权:握手 header Authorization: Bearer ${BAILIAN_API_KEY}
#   - 首条 run-task(header.action=run-task, payload.model=paraformer-realtime-v2,
#     parameters.format=pcm, sample_rate=16000)
#   - 等 task-started 才开始发音频
#   - 客户端二进制:PCM16k Int16 mono
#   - 停止:发 finish-task 消息(header.action=finish-task)
#   - 服务端事件:task-started / result-generated(识别 partial)/ task-finished
#
# 错误兜底:断线 / 鉴权错 / 超时 → on_err 回调抛给上层
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Awaitable, Callable, Optional

import websockets

log = logging.getLogger("prisiragent-companion.asr")

PARAFORMER_WSS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
PARAFORMER_MODEL = "paraformer-realtime-v2"


# ============================================================
# AsrProvider 抽象接口(2026-09-16 M3.10)
# ============================================================
# 任何 Provider(百炼/OpenAI Whisper/本地 SenseVoice)都实现这套。
# CallSession 不感知具体实现,只看到 AsrSession-like 的 start/send_pcm/finish。
# 详见 companion_asr_providers.py 的注册表与工厂。
class AsrProviderProtocol:
    """统一 ASR Provider 接口(duck typing,便于多种实现并存)。
    必实现:start() / send_pcm(pcm_bytes) / finish() / close()
    必暴露:_started(bool) — CallSession ensure_asr_started 等这个变 true
    """

    name: str = "unknown"

    async def start(self) -> None: ...
    async def send_pcm(self, pcm_bytes: bytes) -> None: ...
    async def finish(self) -> None: ...
    async def close(self) -> None: ...


class BailianAsrSession:
    """一通陪聊里的一条百炼 ASR 会话。

    callbacks(全部可选):
      on_partial(text): 拿到一句中间识别结果(句子中间)
      on_final(text):   拿到整句识别结果(可作为最终 user_text 送 LLM)
      on_started():     task-started 收到
      on_finished():    task-finished 收到(可清理)
      on_err(msg):      任何错误
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        on_partial: Optional[Callable[[str], Awaitable[None]]] = None,
        on_final: Optional[Callable[[str], Awaitable[None]]] = None,
        on_started: Optional[Callable[[], Awaitable[None]]] = None,
        on_finished: Optional[Callable[[], Awaitable[None]]] = None,
        on_err: Optional[Callable[[str], Awaitable[None]]] = None,
        sample_rate: int = 16000,
        language_hints: Optional[list[str]] = None,
        model: Optional[str] = None,
        audio_format: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("BAILIAN_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("BAILIAN_API_KEY 未配置")
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_started = on_started
        self.on_finished = on_finished
        self.on_err = on_err
        self.sample_rate = sample_rate
        self.language_hints = language_hints or ["zh", "en"]
        self.model = model or PARAFORMER_MODEL
        self.audio_format = audio_format or "pcm"
        self._ws = None  # type: ignore[assignment]
        self._reader_task: Optional[asyncio.Task] = None
        self._started = False
        self._closed = False
        self._final_text: list[str] = []

    async def start(self) -> None:
        headers = [("Authorization", f"Bearer {self.api_key}")]
        log.info("[asr] connecting %s ...", PARAFORMER_WSS_URL)
        # websockets 15.x:握手 header 用 additional_headers(旧 extra_headers 已弃)
        self._ws = await websockets.connect(
            PARAFORMER_WSS_URL,
            additional_headers=headers,
            max_size=8 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        )
        run_msg = {
            "header": {
                "action": "run-task",
                "task_id": uuid.uuid4().hex,
                "streaming": "duplex",
            },
            "payload": {
                "task_group": "audio",
                "task": "asr",
                "function": "recognition",
                "model": self.model,
                "input": {},
                "parameters": {
                    "format": self.audio_format,
                    "sample_rate": self.sample_rate,
                    "disfluency_removal_enabled": False,
                    "language_hints": self.language_hints,
                },
            },
        }
        await self._ws.send(json.dumps(run_msg))
        log.info("[asr] sent run-task model=%s format=%s",
                 self.model, self.audio_format)
        # 启动后台 reader 收下行
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def send_pcm(self, pcm_bytes: bytes) -> None:
        """客户端发一段 PCM 二进制。**必须等 on_started 之后调用**。"""
        if not self._started:
            log.warning("[asr] send_pcm before started, drop %d bytes", len(pcm_bytes))
            return
        if self._closed or not self._ws:
            return
        await self._ws.send(pcm_bytes)

    async def finish(self) -> None:
        if self._closed or not self._ws:
            return
        try:
            await self._ws.send(json.dumps({
                "header": {"action": "finish-task", "task_id": uuid.uuid4().hex,
                            "streaming": "duplex"},
                "payload": {"input": {}},
            }))
            log.info("[asr] sent finish-task")
        except Exception as e:  # noqa: BLE001
            log.warning("[asr] finish send err: %s", e)
        # 等 reader 自然退出
        if self._reader_task:
            try:
                await asyncio.wait_for(self._reader_task, timeout=8)
            except asyncio.TimeoutError:
                log.warning("[asr] reader task timeout, cancel")
                self._reader_task.cancel()
        await self.close()

    async def close(self) -> None:
        self._closed = True
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    async def _reader_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                # Paraformer 下行是 text json(事件)或二进制(不应出现)
                try:
                    msg = json.loads(raw)
                except Exception:  # noqa: BLE001
                    log.warning("[asr] non-json frame %d bytes", len(raw))
                    continue
                evt = (msg.get("header") or {}).get("event") or \
                      (msg.get("header") or {}).get("action")
                log.info("[asr] recv event=%s payload_keys=%s",
                         evt, list((msg.get("payload") or {}).keys()))
                if evt == "task-started":
                    self._started = True
                    if self.on_started:
                        await self.on_started()
                elif evt == "result-generated":
                    # 实际字段在 payload.output.sentence.text(sentence_end=true/false)
                    payload = msg.get("payload") or {}
                    out = payload.get("output") or {}
                    sent = out.get("sentence") or {}
                    text = sent.get("text", "")
                    is_final = bool(sent.get("sentence_end", False))
                    if text:
                        if is_final:
                            self._final_text.append(text)
                            if self.on_final:
                                await self.on_final(text)
                        else:
                            if self.on_partial:
                                await self.on_partial(text)
                elif evt == "task-finished":
                    if self.on_finished:
                        await self.on_finished()
                    break
                elif evt == "task-failed":
                    err_msg = (msg.get("payload") or {}).get("error_message") or "asr failed"
                    log.error("[asr] task-failed: %s", err_msg)
                    if self.on_err:
                        await self.on_err(err_msg)
                    break
                else:
                    log.info("[asr] unknown event: %s", evt)
        except websockets.ConnectionClosed as e:
            log.info("[asr] connection closed: %s", e)
        except Exception as e:  # noqa: BLE001
            log.exception("[asr] reader crashed: %s", e)
            if self.on_err:
                await self.on_err(f"reader: {type(e).__name__}: {e}")
        # 注意:不在 finally 把 _started 设回 False。
        # 一次 task-started 已确认 server 端起来,这个状态对上层(Caller)有意义。
        # 关闭 ws 是常态(reader 因 ConnectionClosed 自然退出),不应抹除 started 标记。