"""
prisIr_work/skills/tool_use/openai.py — OpenAI function-calling 协议适配器(Phase 2, 2026-09-28)。

定位:SkillDescribe ↔ OpenAI Chat Completions tools 格式。
     LLM 响应里 choices[].message.tool_calls[] → SkillCall IR。

OpenAI 协议形态:
  请求体:
    {
      "tools": [{"type": "function",
                 "function": {"name": "X", "description": "...", "parameters": {...}}}],
      "messages": [{"role": "user", "content": "..."}]
    }
  响应:
    choices[0].message.tool_calls = [
      {"id": "call_xxx", "type": "function",
       "function": {"name": "X", "arguments": "{...json...}"}}
    ]

设计:
  · **tool name ↔ skill_id** — 直接对应
  · **arguments 字符串 JSON** — OpenAI 这里是字符串,需要 json.loads 解析
  · **tool message 喂回** — role="tool",tool_call_id 对应
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..schema import SkillArg, SkillCall, SkillDescribe, SkillResult

log = logging.getLogger("prisir_work.skills.tool_use.openai")


# ---------------------------------------------------------------------------
# SkillDescribe → OpenAI tool 格式
# ---------------------------------------------------------------------------

def _skill_to_function(skill: SkillDescribe) -> dict[str, Any]:
    """一个 SkillDescribe → OpenAI 单 function dict。"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for a in skill.args:
        properties[a.name] = {
            "type": a.type or "string",
            "description": a.description or a.name,
        }
        if a.enum:
            properties[a.name]["enum"] = list(a.enum)
        if a.default is not None:
            properties[a.name]["default"] = a.default
        if a.required:
            required.append(a.name)
    params: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        params["required"] = required
    return {
        "type":     "function",
        "function": {
            "name":        skill.index.id,
            "description": (skill.description or skill.title or skill.index.name)[:1024],
            "parameters":  params,
        },
    }


def openai_tools_for(skills: list[SkillDescribe] | None = None) -> list[dict[str, Any]]:
    """SkillDescribe 列表 → OpenAI tools 数组。
    skills=None 时自动从 skills.describe_skill() 全量取。
    """
    if skills is None:
        from ..registry import describe_skill as _ds
        from ..registry import list_skills as _ls
        skills = [_ds(s.id) for s in _ls()]
        skills = [s for s in skills if s is not None]
    return [_skill_to_function(s) for s in skills]


# ---------------------------------------------------------------------------
# 响应解析:OpenAI tool_calls → SkillCall list
# ---------------------------------------------------------------------------

def parse_openai_tool_call(tc: dict[str, Any]) -> SkillCall | None:
    """单个 OpenAI tool_call dict → SkillCall。
    字段:
      tc.id               — tool_call_id
      tc.function.name    — skill_id
      tc.function.arguments — JSON 字符串
    """
    if not isinstance(tc, dict):
        return None
    if tc.get("type") != "function":
        return None
    fn = tc.get("function") or {}
    if not isinstance(fn, dict):
        return None
    sid = fn.get("name", "")
    if not sid:
        return None
    args_str = fn.get("arguments", "{}")
    if isinstance(args_str, dict):
        args = args_str
    else:
        try:
            args = json.loads(args_str)
            if not isinstance(args, dict):
                args = {}
        except Exception:
            args = {}
    risk = "L0"
    try:
        from ..registry import describe_skill as _ds
        desc = _ds(sid)
        if desc:
            risk = desc.index.risk
    except Exception:
        pass
    raw = json.dumps({"id": tc.get("id", ""), **fn}, ensure_ascii=False)
    return SkillCall(
        skill_id=str(sid),
        args=dict(args),
        risk=risk,
        raw=raw,
    )


def parse_openai_tool_calls(content: Any) -> list[SkillCall]:
    """OpenAI chat.completions 响应 message → SkillCall 列表。

    支持多种入参:
      - 直接 message dict(从 response.choices[0].message)
      - choices 数组
      - 整个 response 对象
    """
    out: list[SkillCall] = []
    # 转成 message dict
    msg = content
    if isinstance(content, dict) and "choices" in content:
        msg = content["choices"][0].get("message", {}) if content.get("choices") else {}
    if isinstance(msg, list) and msg and isinstance(msg[0], dict):
        msg = msg[0]
    if not isinstance(msg, dict):
        return out
    for tc in msg.get("tool_calls") or []:
        c = parse_openai_tool_call(tc)
        if c is not None:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# 反向:SkillResult → OpenAI role="tool" 消息
# ---------------------------------------------------------------------------

def to_openai_tool_message(call: SkillCall, result: SkillResult,
                            tool_call_id: str = "") -> dict[str, Any]:
    """SkillCall + SkillResult → OpenAI tool 角色消息(喂回 messages[] 让 LLM 续生成)。

    tool_call_id 必须跟前面 tool_call 的 id 对应;若空,从 call.raw 里取。
    """
    if not tool_call_id:
        try:
            raw = json.loads(call.raw or "{}")
            tool_call_id = raw.get("id", call.skill_id)
        except Exception:
            tool_call_id = call.skill_id
    if result.ok:
        content = json.dumps(result.payload or {}, ensure_ascii=False)
    else:
        content = json.dumps({"ok": False, "error": result.error},
                             ensure_ascii=False)
    return {
        "role":         "tool",
        "tool_call_id": tool_call_id,
        "content":      content[:8192],
    }


__all__ = [
    "openai_tools_for",
    "parse_openai_tool_call",
    "parse_openai_tool_calls",
    "to_openai_tool_message",
]