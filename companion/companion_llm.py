# -*- coding: utf-8 -*-
# companion_llm.py — prisiragent-companion LLM 流式调用桥(2026-09-16 M3.6)
#
# 设计:
#   - 复用 fastlane.providers.llm_prisir 的 PriserKeyStore/PriserRouter/failover_candidates
#     (不重新发明密钥管理)
#   - 但**自己实现流式**:httpx.AsyncClient.stream(SSE) → token 实时 yield
#   - 走「同厂商优先」故障转移:失败的平台拉黑,跨厂商风格跳变也兜得住
#   - 用 tls13_client(SiliconFlow 兼容 TLS1.2 已知坑)
#
# 与 router.generate() 差异:
#   - generate() 一次性返 text,伴侣要逐 token → 自己做 SSE 解析
#   - 候选序借用 failover_candidates(同厂商优先已实现)
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

from fastlane.providers.base import tls13_client
from fastlane.providers.llm_prisir import (
    PrisirRouter,
    PrisirKeyStore,
    classify_task,
    vendor_of,
)

log = logging.getLogger("prisiragent-companion.llm")


async def stream_chat(
    messages: list[dict],
    strategy: str = "smart",
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> AsyncIterator[tuple[str, str]]:
    """流式调 LLM。每次 yield (event, data):
       - ("delta", text_chunk)         一个 token/字符片段
       - ("meta", dict)                结束:platform/model/task_type/elapsed
       - ("err", str)                  内部失败(本函数不抛)
    跨平台故障转移:失败的平台拉黑,下一个 candidate 续流(用户能感觉到风格跳变)。
    """
    text_for_classify = " ".join(m.get("content", "") for m in messages[-3:])
    task_type = classify_task(text_for_classify)

    router = PrisirRouter()
    try:
        candidates = router.failover_candidates(strategy=strategy, task_type=task_type)
    except Exception as e:  # noqa: BLE001
        yield ("err", f"无可用模型:{e}")
        return
    if not candidates:
        yield ("err", "无可用模型(已配 key 但 platform_cfg 装配失败)")
        return

    excl: set = set()
    last_err: Optional[Exception] = None
    t0 = time.time()
    for cand in candidates:
        platform = cand["platform"]
        cfg = cand["cfg"]
        if platform in excl:
            continue
        proto = (cfg.get("meta") or {}).get("proto", "")
        use_anthropic = (platform == "anthropic") or (proto == "anthropic")
        try:
            if use_anthropic:
                async for evt, data in _stream_anthropic(cfg, messages,
                                                          temperature, max_tokens):
                    if evt == "err":
                        raise RuntimeError(data)
                    yield (evt, data)
                yield ("meta", {"platform": platform, "model": cfg["model"],
                                 "task_type": task_type,
                                 "elapsed_ms": int((time.time() - t0) * 1000)})
                return
            else:
                async for evt, data in _stream_openai_compat(cfg, messages,
                                                              temperature, max_tokens):
                    if evt == "err":
                        raise RuntimeError(data)
                    yield (evt, data)
                yield ("meta", {"platform": platform, "model": cfg["model"],
                                 "task_type": task_type,
                                 "elapsed_ms": int((time.time() - t0) * 1000)})
                return
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("stream_chat %s failed: %s", platform, e)
            excl.add(platform)
            yield ("err", f"[{platform} 失败,换下一平台] {type(e).__name__}: {str(e)[:60]}")
            continue
    yield ("err", f"全部 {len(candidates)} 平台均失败;最后错:{last_err}")


async def _stream_openai_compat(cfg, messages, temperature, max_tokens):
    """OpenAI 兼容协议 SSE 流(/chat/completions stream=true)。
    SSE 格式:data: {json}\n\n  最后 data: [DONE]
    """
    base = cfg["base_url"].rstrip("/")
    endpoint = f"{base}/chat/completions"
    payload = {"model": cfg["model"], "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens, "stream": True}
    headers = {"Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json"}
    try:
        async with tls13_client(timeout_s=60, endpoint=endpoint) as client:
            async with client.stream("POST", endpoint, json=payload, headers=headers) as r:
                if r.status_code == 400 and "temperature" in (await r.aread()).decode("utf-8", "replace").lower():
                    payload.pop("temperature", None)
                    async with client.stream("POST", endpoint, json=payload, headers=headers) as r2:
                        async for evt, data in _parse_openai_sse(r2):
                            yield evt, data
                    return
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")[:200]
                    yield ("err", f"HTTP {r.status_code}: {body}")
                    return
                async for evt, data in _parse_openai_sse(r):
                    yield evt, data
    except httpx.HTTPStatusError as e:
        yield ("err", f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:  # noqa: BLE001
        yield ("err", f"{type(e).__name__}: {e}")


async def _parse_openai_sse(r):
    """SSE 流式解析。逐行 yield ('delta', text_chunk) 或 ('done', None)。"""
    buffer = ""
    async for chunk in r.aiter_text():
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                yield ("done", "")
                return
            try:
                obj = json.loads(data_str)
            except Exception:  # noqa: BLE001
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield ("delta", content)


async def _stream_anthropic(cfg, messages, temperature, max_tokens):
    """Anthropic Messages API 流式(/v1/messages stream=true)。
    SSE 事件类型 message_start/content_block_start/content_block_delta/
    content_block_stop/message_delta/message_stop。
    我们只关心 content_block_delta(type=text_delta)的 delta.text。
    """
    base = cfg["base_url"].rstrip("/")
    endpoint = base + "/v1/messages"
    system = ""
    msgs = []
    for m in messages:
        if m.get("role") == "system":
            system = m.get("content", "")
        else:
            msgs.append({"role": m["role"], "content": m.get("content", "")})
    payload = {"model": cfg["model"], "max_tokens": max_tokens,
                "temperature": temperature, "messages": msgs, "stream": True}
    if system:
        payload["system"] = system
    headers = {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01",
                "content-type": "application/json"}
    try:
        async with tls13_client(timeout_s=60, endpoint=endpoint) as client:
            async with client.stream("POST", endpoint, json=payload, headers=headers) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")[:200]
                    yield ("err", f"HTTP {r.status_code}: {body}")
                    return
                buffer = ""
                async for chunk in r.aiter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            try:
                                obj = json.loads(data_str)
                            except Exception:  # noqa: BLE001
                                continue
                            evt_type = obj.get("type", "")
                            if evt_type == "content_block_delta":
                                delta = obj.get("delta") or {}
                                txt = delta.get("text", "")
                                if txt:
                                    yield ("delta", txt)
                            elif evt_type == "message_stop":
                                yield ("done", "")
                                return
    except httpx.HTTPStatusError as e:
        yield ("err", f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:  # noqa: BLE001
        yield ("err", f"{type(e).__name__}: {e}")