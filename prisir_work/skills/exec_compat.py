"""
prisIr_work/skills/exec_compat.py — Phase 4(2026-09-28)EXEC ↔ tool_use 兼容 + 灰度切换。

定位:新 tool_use 协议 ship 后,老 EXEC `[[EXEC: cap k="v"]]` 标记继续可用 ≥ 1 季度。
本模块做:
  · EXEC marker → SkillCall IR(给新路径调 execute_skill)
  · SkillCall IR → EXEC marker 文本(给老 LLM 喂回,或调试输出)
  · 按 mode 路由:
      mode="exec"      → 只走老 scan_and_exec(给老 LLM / 强制老协议)
      mode="tool_use"  → 只走新 run_loop / replan(强制新协议)
      mode="both"      → 两条都跑,EXEC 优先(显式 EXEC 标记是强信号,tool_use 跳过)

设计:
  · **不动 agent_main_chat_hook** — scan_and_exec / parse_exec_markers 继续工作
  · **不动 phase 2 tool_use** — run_loop / parse_any_tool_calls 继续工作
  · **本模块是薄桥** — 提供互转函数 + mode 路由
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Union

from .. import agent_main_chat_hook as _hook
from ..agent_main_chat_hook import ExecMarker
from .schema import SkillCall

log = logging.getLogger("prisir_work.skills.exec_compat")


ExecCompatMode = Literal["exec", "tool_use", "both"]

DEFAULT_MODE: ExecCompatMode = "both"  # Phase 4 默认兼容两种协议


# ---------------------------------------------------------------------------
# EXEC → SkillCall IR
# ---------------------------------------------------------------------------

def exec_marker_to_skill_call(marker: ExecMarker) -> SkillCall:
    """EXEC 标记 → SkillCall IR。

    - capability_id 直传 skill_id(EXEC 协议在 PrisirAI 里 capability == skill)
    - args 全是 str(LLM 字面量);skill 调用时由 fill_defaults 兜底
    - risk 从 registry 自动补(L0 默认,L1+ 从 desc.index.risk)
    """
    sid = marker.capability
    risk = "L0"
    try:
        from .registry import describe_skill as _ds
        desc = _ds(sid)
        if desc:
            risk = desc.index.risk
    except Exception:  # noqa: BLE001
        pass
    return SkillCall(
        skill_id=sid,
        args=dict(marker.args or {}),
        risk=risk,
        raw=marker.raw,
    )


def parse_exec_to_skill_calls(text: str) -> list[SkillCall]:
    """ai_text → [SkillCall]。直接复用老 parser,再转换。"""
    markers = _hook.parse_exec_markers(text)
    return [exec_marker_to_skill_call(m) for m in markers]


# ---------------------------------------------------------------------------
# SkillCall IR → EXEC 文本
# ---------------------------------------------------------------------------

def skill_call_to_exec_marker(call: SkillCall) -> str:
    """SkillCall → `[[EXEC: <sid> k="v"]]` 文本。

    用于:
      · 把 tool_use 路径生成的结果写回给老 LLM(让它看到 EXEC 格式)
      · 调试输出 / 日志
      · mode=exec 灰度切换(强制老协议)
    """
    args_str = " ".join(
        f'{k}="{_escape(v)}"' for k, v in (call.args or {}).items()
    )
    if args_str:
        return f"[[EXEC: {call.skill_id} {args_str}]]"
    return f"[[EXEC: {call.skill_id}]]"


def skill_calls_to_exec_text(calls: list[SkillCall]) -> str:
    """list[SkillCall] → 多行 EXEC 文本块(用 \\n 分隔)。"""
    return "\n".join(skill_call_to_exec_marker(c) for c in calls)


# ---------------------------------------------------------------------------
# 模式路由
# ---------------------------------------------------------------------------

def route_exec(
    ai_text: str,
    mode: ExecCompatMode = DEFAULT_MODE,
    *,
    tool_calls: list[SkillCall] | None = None,
) -> dict[str, Any]:
    """按 mode 路由 ai_text + tool_calls → 统一返回。

    输入:
      ai_text:    LLM 完整文本响应(可能含 EXEC 标记)
      tool_calls: 已经 parse 出来的 tool_use call list(从 phase 2 run_loop 或 replan)
                  mode="tool_use" 时必传;mode="exec" 时忽略;mode="both" 时优先 EXEC

    返回:
      {
        "mode":       "exec" / "tool_use" / "both" / "none",
        "calls":      [SkillCall, ...],   # 统一 IR(给 execute_skill)
        "exec_markers":[ExecMarker, ...], # 老 EXEC 标记(给老 UI)
        "source":     "exec" / "tool_use" / "both" / "empty",
      }
    """
    out: dict[str, Any] = {
        "mode": mode,
        "calls": [],
        "exec_markers": [],
        "source": "empty",
    }
    if mode == "exec":
        # 强制老协议:tool_calls 忽略
        markers = _hook.parse_exec_markers(ai_text)
        out["exec_markers"] = markers
        out["calls"] = [exec_marker_to_skill_call(m) for m in markers]
        out["source"] = "exec" if markers else "empty"
        return out

    if mode == "tool_use":
        # 强制新协议:ai_text 里的 EXEC 标记不识别(给老 LLM 报错,提醒升级)
        if "EXEC:" in ai_text and not tool_calls:
            log.warning("exec_compat: mode=tool_use but ai_text contains "
                        "[[EXEC:...]] — falling back to parse_any_tool_calls")
        calls = list(tool_calls or [])
        if not calls and ai_text:
            # 兜底:从 ai_text 再 parse 一次(例如 phase 3.5 replan 二次调用走 XML)
            from .tool_use.translate import parse_any_tool_calls
            calls = parse_any_tool_calls(ai_text)
        out["calls"] = calls
        out["source"] = "tool_use" if calls else "empty"
        return out

    # mode == "both"(默认):EXEC 优先 + tool_use 补漏
    markers = _hook.parse_exec_markers(ai_text)
    if markers:
        out["exec_markers"] = markers
        out["calls"] = [exec_marker_to_skill_call(m) for m in markers]
        out["source"] = "exec"
        return out
    # 没 EXEC 标记 → 走 tool_use 兜底
    calls = list(tool_calls or [])
    if not calls and ai_text:
        from .tool_use.translate import parse_any_tool_calls
        calls = parse_any_tool_calls(ai_text)
    out["calls"] = calls
    out["source"] = "tool_use" if calls else "empty"
    return out


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _escape(v: Any) -> str:
    """字符串值转义:把 `"`, `\\`, `\n` 转成 `\"`, `\\\\`, `\\n` 等。"""
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False)
    out = []
    for ch in v:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return "".join(out)


__all__ = [
    "ExecCompatMode",
    "DEFAULT_MODE",
    "exec_marker_to_skill_call",
    "parse_exec_to_skill_calls",
    "skill_call_to_exec_marker",
    "skill_calls_to_exec_text",
    "route_exec",
]