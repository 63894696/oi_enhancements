"""
prisIr_work/skills/tool_use/translate.py — 通用 IR 转译 + Qwen/Hermes XML 协议(Phase 2, 2026-09-28)。

定位:把 LLM 输出的非标准 tool_use 形态(Qwen XML / Hermes 风格 / 自由文本)转 SkillCall IR。

Phase 1.7 实测 openrouter/free 模型常用 XML 变种:
  <tool_call>
  {"name": "video.info", "arguments": {"path": "x.mp4"}}
  </tool_call>

或更老的 Qwen 风格:
  <tool_call>name<arg_key>path</arg_key><arg_value>x.mp4</arg_value></tool_call>

或 Hermes/Qwen3 alpha:
  <invoke name="X"><path>x.mp4</path></invoke>

设计:
  · **多协议 fallback** — parse_any_tool_calls 自动试三种格式
  · **regex 解析容错** — 多余空格 / 换行 / 编码噪音都接受
  · **失败返 []** — 绝不抛栈
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..schema import SkillCall

log = logging.getLogger("prisir_work.skills.tool_use.translate")


# ---------------------------------------------------------------------------
# 通用 XML 解析(Qwen / Hermes 风格)
# ---------------------------------------------------------------------------

# 形式 1:Qwen JSON 风格 — <tool_call>{"name":..., "arguments":...}</tool_call>
_RE_JSON_TOOL_CALL = re.compile(
    r"<\s*tool_call\s*>(.*?)<\s*/\s*tool_call\s*>",
    re.IGNORECASE | re.DOTALL,
)

# 形式 2:Qwen 参数对风格 — <tool_call>name<arg_key>key</arg_key><arg_value>val</arg_value>...</tool_call>
_RE_QWEN_ARGS = re.compile(
    r"<\s*arg_key\s*>(.*?)<\s*/\s*arg_key\s*>\s*<\s*arg_value\s*>(.*?)<\s*/\s*arg_value\s*>",
    re.IGNORECASE | re.DOTALL,
)

# 形式 3:Hermes invoke 风格 — <invoke name="X"><key>val</key>...</invoke>
_RE_INVOKE = re.compile(
    r"<\s*invoke\s+name\s*=\s*\"([^\"]+)\"\s*>(.*?)<\s*/\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_RE_INVOKE_ARG = re.compile(
    r"<\s*(\w+)\s*>([^<]*)<\s*/\s*\1\s*>",
    re.DOTALL,
)


def parse_generic_xml_tool_calls(text: str) -> list[SkillCall]:
    """任意 LLM 文本 → XML 风格 tool_call → SkillCall list。
    失败 / 不匹配 → []。
    """
    out: list[SkillCall] = []
    if not text or not isinstance(text, str):
        return out

    # ── 形式 1:JSON 内嵌 ──
    for m in _RE_JSON_TOOL_CALL.finditer(text):
        body = m.group(1).strip()
        try:
            obj = json.loads(body)
            if not isinstance(obj, dict):
                continue
            sid = obj.get("name", "")
            args = obj.get("arguments", {})
            if not sid:
                continue
            if not isinstance(args, dict):
                args = {}
            out.append(_mk_call(sid, args, raw=body))
        except Exception:
            # JSON 解析失败,尝试按 Qwen arg_key/arg_value 走形式 2
            sid_match = re.search(r'"name"\s*:\s*"([^"]+)"', body)
            if sid_match:
                sid = sid_match.group(1)
                args: dict[str, Any] = {}
                for am in _RE_QWEN_ARGS.finditer(body):
                    args[am.group(1).strip()] = am.group(2).strip()
                if args or sid:
                    out.append(_mk_call(sid, args, raw=body))

    # ── 形式 3:Hermes <invoke name="X"><key>val</key>...</invoke> ──
    for m in _RE_INVOKE.finditer(text):
        sid = m.group(1).strip()
        args: dict[str, Any] = {}
        for am in _RE_INVOKE_ARG.finditer(m.group(2)):
            args[am.group(1).strip()] = am.group(2).strip()
        out.append(_mk_call(sid, args, raw=m.group(0)))

    return out


def _mk_call(sid: str, args: dict[str, Any], raw: str = "") -> SkillCall:
    """构造 SkillCall,自动补 risk。"""
    risk = "L0"
    try:
        from ..registry import describe_skill as _ds
        desc = _ds(sid)
        if desc:
            risk = desc.index.risk
    except Exception:
        pass
    return SkillCall(
        skill_id=str(sid),
        args=dict(args),
        risk=risk,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# 自动选协议
# ---------------------------------------------------------------------------

def parse_any_tool_calls(content: Any) -> list[SkillCall]:
    """智能识别协议并 parse,返 SkillCall 列表。

    输入形态支持:
      - dict(OpenAI choices[0].message 或完整 response)
      - list(content blocks,Anthropic)
      - str(纯文本,可能含 XML 工具调用)
    """
    if content is None:
        return []
    # 1) dict 走 OpenAI
    if isinstance(content, dict):
        # 试 OpenAI tool_calls
        calls = parse_openai_tool_calls(content)
        if calls:
            return calls
        # 试 Anthropic content blocks list
        if "content" in content and isinstance(content["content"], list):
            from .anthropic import parse_anthropic_content
            calls = parse_anthropic_content(content["content"])
            if calls:
                return calls
        # OpenRouter 等有时把 tool_calls 放在 message 字段
        msg = content.get("message") or {}
        if isinstance(msg, dict) and msg.get("tool_calls"):
            from .openai import parse_openai_tool_call
            return [c for c in (parse_openai_tool_call(tc)
                                for tc in msg["tool_calls"]) if c]
        return []
    # 2) list 走 Anthropic content blocks
    if isinstance(content, list):
        from .anthropic import parse_anthropic_content
        return parse_anthropic_content(content)
    # 3) str 走 XML fallback
    if isinstance(content, str):
        return parse_generic_xml_tool_calls(content)
    return []


# 避免循环 import
from .openai import parse_openai_tool_calls, parse_openai_tool_call  # noqa: E402


__all__ = [
    "parse_generic_xml_tool_calls",
    "parse_any_tool_calls",
]