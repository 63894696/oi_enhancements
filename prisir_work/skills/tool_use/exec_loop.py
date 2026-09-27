"""
prisIr_work/skills/tool_use/exec_loop.py — tool_use 完整循环执行器(Phase 2, 2026-09-28)。

定位:对接任意 LLM vendor,完整跑:
  1. LLM 首次响应 → parse tool_call → SkillCall list
  2. 顺序 execute_skill → SkillResult list
  3. 把 SkillResult 喂回 LLM(转 tool_result / role="tool" / 自由文本)
  4. LLM 续生成 → 直到不再 emit tool_call / 达 max_steps

输入:
  user_text    : 用户原始输入
  llm_call     : async (messages, tools) → response_content
  protocol     : "anthropic" | "openai" | "auto"
  max_steps    : 最大循环步数(默认 8,防死循环)
  tools        : tools 数组(None 时从 skills registry 全量生成)
  initial_messages : 可选初始 messages(默认 [{user: user_text}])

返回:
  {
    "calls":     [SkillCall, ...]    所有执行的 call(按步)
    "results":   [SkillResult, ...]  对应结果
    "messages":  [dict, ...]         完整 messages 历史
    "final_response": str|dict       LLM 最终文本响应
    "steps":     int                 实际循环步数
    "stopped_reason": str            "no_more_tool_call" / "max_steps" / "llm_error"
  }

设计:
  · **协议无关** — tools 数组 + response 解析都按 protocol 参数走
  · **不破坏 Phase 1.5 EXEC 路径** — llm_call 不支持 tools 时,直接 L0 直发一次返回
  · **fail-soft** — 任何 LLM 错误/parse 失败/skill not found 都 catch 继续走
  · **run_loop_sync** — 给测试/CLI 用的同步包装
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from ..schema import SkillCall, SkillResult
from .translate import parse_any_tool_calls

log = logging.getLogger("prisir_work.skills.tool_use.exec_loop")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tools_for(protocol: str) -> list[dict[str, Any]]:
    """protocol → tools 数组(全量 skill)。"""
    if protocol == "anthropic":
        from .anthropic import anthropic_tools_for
        return anthropic_tools_for()
    if protocol == "openai":
        from .openai import openai_tools_for
        return openai_tools_for()
    # auto 默认 openai(更通用)
    from .openai import openai_tools_for
    return openai_tools_for()


def _feed_back(call: SkillCall, result: SkillResult,
               protocol: str) -> dict[str, Any]:
    """SkillResult → 喂回 LLM 的消息 dict。"""
    if protocol == "anthropic":
        from .anthropic import to_anthropic_tool_result
        return {"role": "user",
                "content": [to_anthropic_tool_result(call, result)]}
    # openai / auto:用 role=tool 消息
    from .openai import to_openai_tool_message
    return to_openai_tool_message(call, result)


async def _execute_calls(calls: list[SkillCall]) -> list[SkillResult]:
    """顺序 execute_skill 一组 calls,返对应结果。"""
    from ..loader import execute_skill
    results: list[SkillResult] = []
    for c in calls:
        try:
            r = execute_skill(c.skill_id, dict(c.args or {}), force=True)
        except Exception as exc:  # noqa: BLE001
            r = SkillResult(skill_id=c.skill_id, ok=False,
                            error=f"loop_exec_error:{type(exc).__name__}:{exc}")
        results.append(r)
    return results


# ---------------------------------------------------------------------------
# 主 loop
# ---------------------------------------------------------------------------

# llm_call signature:async (messages: list[dict], tools: list[dict]) → response_content
LLMCall = Callable[[list[dict], list[dict]], Awaitable[Any]]


async def run_loop(
    user_text: str,
    llm_call: LLMCall,
    *,
    protocol: str = "auto",
    max_steps: int = 8,
    tools: list[dict[str, Any]] | None = None,
    initial_messages: list[dict] | None = None,
) -> dict[str, Any]:
    """完整 tool_use 循环。

    返回 dict 含 calls / results / messages / final_response / steps /
    stopped_reason。LLM 错误或达到 max_steps 时 stopped_reason 非空。
    """
    if max_steps < 1:
        max_steps = 1
    tools = tools if tools is not None else _tools_for(protocol)
    messages: list[dict] = list(initial_messages or [])
    if not messages:
        messages = [{"role": "user", "content": user_text}]
    elif user_text and not any(m.get("role") == "user"
                                for m in messages):
        messages.append({"role": "user", "content": user_text})

    all_calls: list[SkillCall] = []
    all_results: list[SkillResult] = []
    final_response: Any = None
    stopped_reason = "max_steps"

    for step in range(1, max_steps + 1):
        # 调 LLM
        try:
            response = await llm_call(messages, tools)
        except Exception as exc:  # noqa: BLE001
            log.warning("exec_loop step %d: llm_call raised: %s", step, exc)
            stopped_reason = f"llm_error:{type(exc).__name__}"
            break
        final_response = response

        # parse tool_calls
        calls = parse_any_tool_calls(response)
        # 兼容:OpenAI 返回里有 assistant message 字段含 tool_calls 但 response 整体不是 dict
        if not calls and isinstance(response, dict):
            # 兜底:OpenAI response.choices[0].message.tool_calls
            tcs = (((response.get("choices") or [{}])[0])
                   .get("message", {})
                   .get("tool_calls"))
            if tcs:
                from .openai import parse_openai_tool_calls
                calls = parse_openai_tool_calls(tcs)
        if not calls:
            stopped_reason = "no_more_tool_call"
            break

        # execute
        results = await _execute_calls(calls)
        all_calls.extend(calls)
        all_results.extend(results)

        # 把 assistant message 和 tool result 喂回
        # assistant message(把 tool_calls 加进去给 LLM 上下文)
        if protocol == "openai" and isinstance(response, dict):
            choices = response.get("choices") or [{}]
            if choices:
                msg = choices[0].get("message") or {}
                if msg:
                    messages.append({
                        "role":       msg.get("role", "assistant"),
                        "content":    msg.get("content", ""),
                        "tool_calls": msg.get("tool_calls", []),
                    })
        for c, r in zip(calls, results):
            messages.append(_feed_back(c, r, protocol))
        # 下轮

    return {
        "calls":           all_calls,
        "results":         all_results,
        "messages":        messages,
        "final_response":  final_response,
        "steps":           step if stopped_reason == "max_steps"
                           else (all_calls and len(all_results) // max(1, len(all_results) // max(1, len(all_calls) - len(all_results) + 1)) or step),
        "stopped_reason":  stopped_reason,
    }


def run_loop_sync(user_text: str, llm_call: LLMCall, **kw) -> dict[str, Any]:
    """同步包装(测试用)。"""
    return asyncio.run(run_loop(user_text, llm_call, **kw))


__all__ = ["run_loop", "run_loop_sync", "LLMCall"]