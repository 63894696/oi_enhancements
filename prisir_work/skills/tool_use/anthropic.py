"""
prisIr_work/skills/tool_use/anthropic.py — Anthropic tool_use 协议适配器(Phase 2, 2026-09-28)。

定位:SkillDescribe ↔ Anthropic Messages API tools 格式。
     LLM 响应里 content_block.type="tool_use" → SkillCall IR。

Anthropic 协议形态:
  请求体:
    {
      "tools": [{"name": "X", "description": "...", "input_schema": {...}}],
      "messages": [{"role": "user", "content": "..."}]
    }
  响应:
    content_block: {"type": "tool_use", "id": "toolu_xxx", "name": "X", "input": {...}}
    或 文本流(text_delta)。

设计:
  · **skill_id ↔ tool name** — skill_id 跟 tool name 同名(直接对应);冒号/横线替为下划线不必要
  · **input ↔ SkillCall.args** — JSON Schema 的 properties 字段名直接是 args key
  · **tool_result 块** — execute 后返 content_block.type="tool_result",tool_use_id 对应
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..schema import SkillArg, SkillCall, SkillDescribe, SkillResult

log = logging.getLogger("prisir_work.skills.tool_use.anthropic")


# ---------------------------------------------------------------------------
# SkillDescribe → Anthropic tool 格式
# ---------------------------------------------------------------------------

def _arg_to_json_schema(arg: SkillArg) -> dict[str, Any]:
    """单个 SkillArg → JSON Schema property dict。"""
    out: dict[str, Any] = {
        "type": arg.type or "string",
        "description": arg.description or arg.name,
    }
    if arg.enum:
        out["enum"] = list(arg.enum)
    if arg.default is not None:
        out["default"] = arg.default
    return out


def _skill_to_tool(skill: SkillDescribe) -> dict[str, Any]:
    """一个 SkillDescribe → Anthropic 单 tool dict。"""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for a in skill.args:
        properties[a.name] = _arg_to_json_schema(a)
        if a.required:
            required.append(a.name)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return {
        "name":        skill.index.id,
        "description": (skill.description or skill.title or skill.index.name)[:1024],
        "input_schema": schema,
    }


def anthropic_tools_for(skills: list[SkillDescribe] | None = None) -> list[dict[str, Any]]:
    """SkillDescribe 列表 → Anthropic tools 数组。
    skills=None 时自动从 skills.describe_skill() 全量取。
    """
    if skills is None:
        from ..registry import describe_skill as _ds
        from ..registry import list_skills as _ls
        skills = [_ds(s.id) for s in _ls()]
        skills = [s for s in skills if s is not None]
    return [_skill_to_tool(s) for s in skills]


# ---------------------------------------------------------------------------
# 响应解析:Anthropic tool_use block → SkillCall
# ---------------------------------------------------------------------------

def parse_anthropic_tool_use(block: dict[str, Any]) -> SkillCall | None:
    """单个 Anthropic content_block(type="tool_use") → SkillCall。
    字段:
      block.id       — tool_use_id
      block.name     — skill_id
      block.input    — args dict
    失败 → None(不抛栈)。
    """
    if not isinstance(block, dict):
        return None
    if block.get("type") != "tool_use":
        return None
    sid = block.get("name", "")
    if not sid:
        return None
    args = block.get("input") or {}
    if not isinstance(args, dict):
        # input 偶尔被序列化成 string(异常情况)
        try:
            args = json.loads(args)
            if not isinstance(args, dict):
                args = {}
        except Exception:
            args = {}
    # 取 risk 等级(从 registry 反查)
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
        raw=json.dumps(block, ensure_ascii=False),
    )


def parse_anthropic_content(blocks: list[Any]) -> list[SkillCall]:
    """Anthropic content blocks 列表 → SkillCall 列表。
    自动过滤 type != 'tool_use' 的块(text / thinking 等)。
    """
    out: list[SkillCall] = []
    for b in blocks or []:
        c = parse_anthropic_tool_use(b)
        if c is not None:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# 反向:SkillResult → Anthropic tool_result 块(喂回 LLM 续生成)
# ---------------------------------------------------------------------------

def to_anthropic_tool_result(call: SkillCall, result: SkillResult,
                              tool_use_id: str = "") -> dict[str, Any]:
    """SkillCall + SkillResult → Anthropic tool_result content_block。
    给 LLM 当 user 喂回去,LLM 看完整结果继续生成。

    tool_use_id 必须跟前面 tool_use block 的 id 对应;若空,从 call.raw 里取。
    """
    if not tool_use_id:
        try:
            raw = json.loads(call.raw or "{}")
            tool_use_id = raw.get("id", call.skill_id)
        except Exception:
            tool_use_id = call.skill_id
    # content 字段:成功 → JSON payload,失败 → error 字符串
    if result.ok:
        content = json.dumps(result.payload or {}, ensure_ascii=False)
    else:
        content = json.dumps({"ok": False, "error": result.error},
                             ensure_ascii=False)
    return {
        "type":          "tool_result",
        "tool_use_id":   tool_use_id,
        "content":       content[:8192],   # Anthropic 单块上限
        "is_error":      not result.ok,
    }


__all__ = [
    "anthropic_tools_for",
    "parse_anthropic_tool_use",
    "parse_anthropic_content",
    "to_anthropic_tool_result",
]