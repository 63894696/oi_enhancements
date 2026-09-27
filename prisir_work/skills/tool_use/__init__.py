"""
prisIr_work/skills/tool_use/__init__.py — Skills 工作台 Phase 2 tool_use 适配层入口(2026-09-28)。

定位:LLM 原生 tool_calls / function_calls 协议转 Skills IR(SkillCall/SkillResult),
     让 LLM 用 industry-standard 协议调 skill,放弃 EXEC 标记语法。

支持协议:
  - Anthropic  tools=[{name, description, input_schema}] + 响应里 content_block.type=tool_use
  - OpenAI     tools=[{type: "function", function: {...}}] + 响应里 tool_calls[].function
  - 通用 XML   <tool_call>X<arg_key>... (Qwen/Hermes 变种,Phase 1.7 实测常用)

关键 API:
  anthropic_tools_for(skills)        → List[dict]      Anthropic 协议 tools 数组
  openai_tools_for(skills)           → List[dict]      OpenAI 协议 tools 数组
  parse_anthropic_tool_use(block)    → SkillCall       单 block → IR
  parse_openai_tool_calls(content)   → List[SkillCall] 整 content → IR list
  parse_generic_xml_tool_calls(text) → List[SkillCall] Qwen/Hermes XML → IR list
  parse_any_tool_calls(content)      → List[SkillCall] 自动选协议 parse
  to_anthropic_tool_result(call, result) → dict        IR → tool_result 块
  to_openai_tool_message(call, result)    → dict        IR → role="tool" 消息
  run_loop(user_text, llm_call, ...) → dict            完整 loop,直到 LLM 不再 emit tool_call

复用:
  - prisIr_work.skills.describe_skill(skill_id)    Phase 1 已 ship
  - prisIr_work.skills.execute_skill(call)         Phase 1 已 ship(L1+ confirm 闸门在)
  - prisIr_work.skills.schema.SkillCall / SkillResult Phase 1 已 ship
"""
from __future__ import annotations

from .anthropic import (
    anthropic_tools_for,
    parse_anthropic_tool_use,
    to_anthropic_tool_result,
)
from .openai import (
    openai_tools_for,
    parse_openai_tool_calls,
    to_openai_tool_message,
)
from .translate import (
    parse_generic_xml_tool_calls,
    parse_any_tool_calls,
)
from .exec_loop import run_loop, run_loop_sync


__all__ = [
    # Anthropic
    "anthropic_tools_for",
    "parse_anthropic_tool_use",
    "to_anthropic_tool_result",
    # OpenAI
    "openai_tools_for",
    "parse_openai_tool_calls",
    "to_openai_tool_message",
    # Generic XML (Qwen / Hermes)
    "parse_generic_xml_tool_calls",
    "parse_any_tool_calls",
    # Loop
    "run_loop",
    "run_loop_sync",
]