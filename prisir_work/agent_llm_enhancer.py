"""prisir_work/agent_llm_enhancer.py — LLM 增强意图抽取(P3j T15-B)。

定位:regex 模式抽不出 / 歧义消解 / 抽取更复杂的 args 时,fallback 调 LLM。
设计:
  · 把 12 个 video/youtube capability 渲染成 JSON Schema 喂给 LLM
  · LLM 返 {"capability": "...", "args": {...}, "missing": [...], "confidence": 0.0-1.0}
  · 流式拼回完整 JSON,从 stream_chat 收 token
  · **降级而非崩溃** — LLM 没起 / 超时 / JSON 解析失败 → 返 None,上
    层 parse_intent 走 capability.search 兜底
  · 短 prompt + 单 JSON 即可,不强求结构化输出 API

用例:
  from prisir_work.agent_llm_enhancer import enhance_with_llm
  r = enhance_with_llm("帮我做个 9:16 短视频,主题是 AI")
  if r: print(r)  # IntentResult(...)
  else: print("LLM 没抽出来,regex 兜底")
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

__all__ = ["enhance_with_llm", "build_llm_prompt", "parse_llm_json_response"]


log = logging.getLogger("prisir_work.agent_llm")


# ---------------------------------------------------------------------------
# Prompt 构造
# ---------------------------------------------------------------------------

_INTENT_JSON_SCHEMA = """\
{
  "capability": "<one of: video.list, video.create, video.tts, video.asr, video.cut, video.bgm, video.burn, video.info, video.analyze, youtube.upload, youtube.list, youtube.status>",
  "args": { ... },         // 抽取到的字段(键见下面每个 capability 的说明)
  "missing": ["topic", ...],  // 必填但没抽到的字段名
  "confidence": 0.0-1.0   // 你对这个解析的确信度
}
"""


def build_llm_prompt(query: str, capability_hints: list[dict[str, Any]]) -> str:
    """渲染 LLM prompt:用户 query + 12 个 capability 的 title/keywords。

    capability_hints: [{id, title, keywords: [...]}] 来自 capability.list_capabilities() 过滤 video/youtube
    """
    cap_list = "\n".join(
        f"  · {h['id']}: {h['title']}\n"
        f"    关键词: {', '.join(h['keywords'][:8])}"
        for h in capability_hints
    )
    return f"""你是 PrisirAI 视频/YouTube 操作意图路由器。
用户会用中文自然语言描述想做什么。你必须判断应该走哪个 capability。

# 可选 capability(共 {len(capability_hints)} 个):
{cap_list}

# 用户 query:
{query}

# 输出格式(只返 JSON,不要其它文字):
{_INTENT_JSON_SCHEMA}

字段抽取规则:
- 视频路径(.mp4/.mov/.avi/.mkv/.srt/.vtt/.ass) → input / output / video / sub / music 之一(按 capability 语义判)
- 时间 HH:MM:SS 或 MM:SS → start / end
- 9:16 / 16:9 / 1:1 → aspect_ratio
- 「主题是 X」「文案是 X」 → video.create 的 topic / script
- 「公开 / public / 不公开 / unlisted / 私密 / private」 → youtube.upload 的 privacy
- 「最佳时段 / 标签效果 / 增长 / 内容类型」 → video.analyze 的 mode

只返 JSON,不要解释。"""


# ---------------------------------------------------------------------------
# LLM 调用(走 companion.companion_llm.stream_chat,串成完整 JSON)
# ---------------------------------------------------------------------------

def _collect_stream_text(prompt: str, *, timeout: float = 20.0,
                        temperature: float = 0.2, max_tokens: int = 600
                        ) -> Optional[str]:
    """调 stream_chat,串成完整文本。失败/超时 → None,不抛栈。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 已在 event loop 里 → 起一个 task
            return asyncio.run_coroutine_threadsafe(
                _collect_async(prompt, timeout, temperature, max_tokens),
                loop).result(timeout=timeout + 5.0)
        return loop.run_until_complete(
            _collect_async(prompt, timeout, temperature, max_tokens))
    except Exception as e:  # noqa: BLE001
        log.warning("LLM collect failed: %s: %s", type(e).__name__, e)
        return None


async def _collect_async(prompt: str, timeout: float, temperature: float,
                        max_tokens: int) -> Optional[str]:
    try:
        from companion.companion_llm import stream_chat
    except ImportError:
        return None
    msgs = [{"role": "user", "content": prompt}]
    parts: list[str] = []
    deadline = time.monotonic() + timeout
    async for ev, data in stream_chat(msgs, temperature=temperature,
                                      max_tokens=max_tokens):
        if time.monotonic() > deadline:
            log.warning("LLM collect timeout (%.1fs)", timeout)
            return None
        if ev == "delta":
            parts.append(data)
        elif ev == "err":
            log.warning("LLM stream err: %s", data)
            return None
        elif ev == "meta":
            break
    return "".join(parts).strip() if parts else None


# ---------------------------------------------------------------------------
# 解析 LLM 返的 JSON(鲁棒:容 markdown 围栏 / 前缀废话)
# ---------------------------------------------------------------------------

def parse_llm_json_response(text: str) -> Optional[dict[str, Any]]:
    """从 LLM 返的字符串里抠 JSON。容 markdown 围栏 / 前缀 / 后缀。"""
    if not text:
        return None
    # 1) 去掉 ```json ... ``` 围栏
    if "```" in text:
        try:
            inner = text.split("```", 2)[1]
            if inner.startswith("json"):
                inner = inner[4:]
            text = inner.split("```", 1)[0].strip()
        except IndexError:
            pass
    # 2) 找第一个 { 和最后一个 } 平衡
    start = text.find("{")
    if start < 0:
        return None
    # 平衡括号
    depth = 0
    end = -1
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 入口:enhance_with_llm(query) → IntentResult | None
# ---------------------------------------------------------------------------

def enhance_with_llm(query: str, *, timeout: float = 20.0) -> Optional[Any]:
    """LLM 增强意图抽取。返 IntentResult(同 agent_natural_video) 或 None。

    None 含义:LLM 没抽出来(超时/JSON 解析失败/不可用)→ 上层 parse_intent 兜底。
    """
    from . import capability as _cap
    hints = [c for c in _cap.list_capabilities()
             if c["id"].startswith("video.") or c["id"].startswith("youtube.")]
    prompt = build_llm_prompt(query, hints)

    text = _collect_stream_text(prompt, timeout=timeout)
    if not text:
        return None

    parsed = parse_llm_json_response(text)
    if not parsed:
        log.warning("LLM 返非 JSON: %s", text[:200])
        return None

    cap = parsed.get("capability", "")
    if cap not in {h["id"] for h in hints}:
        log.warning("LLM 返未知 capability: %r", cap)
        return None

    from .agent_natural_video import IntentResult
    args = parsed.get("args") or {}
    missing = parsed.get("missing") or []
    confidence = float(parsed.get("confidence", 0.0))
    return IntentResult(
        ok=True,
        capability=cap,
        args=args,
        missing=missing,
        confidence=max(0.0, min(1.0, confidence)),
        error="llm_enhanced",
    )