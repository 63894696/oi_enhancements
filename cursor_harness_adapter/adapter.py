"""prisiragent / Claude 整合 adapter

2026-07-04 v0.0.1

提供 2 个工厂函数:
- get_harness_for_oi():用 DashScope qwen-max(OI agent 主力模型)
- get_harness_for_claude():用 Anthropic Claude(我的主力)

两个都返回 CursorHarness 实例,API 一致。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 把 D:/cursor-harness/src 加到 sys.path(如果没 pip install)
_HARNESS_SRC = Path("D:/cursor-harness/src")
if _HARNESS_SRC.exists() and str(_HARNESS_SRC) not in sys.path:
    sys.path.insert(0, str(_HARNESS_SRC))

from cursor_harness import (
    CursorHarness, DefaultStopPolicy, PathSandbox,
    shell_tool, read_tool, glob_tool, grep_tool, write_tool,
)


def _default_tools(ripgrep_path=None):
    return [
        shell_tool(timeout_s=30),
        read_tool(),
        glob_tool(ripgrep_path=ripgrep_path),
        grep_tool(ripgrep_path=ripgrep_path),
        write_tool(),
    ]


def get_harness_for_oi(
    model: str = "qwen-max",
    api_key: str | None = None,
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
) -> CursorHarness:
    """给 OI agent 用 — DashScope qwen-max(OpenAI 兼容)"""
    return CursorHarness(
        model=model,
        api_key=api_key or os.environ.get("BAILIAN_API_KEY", ""),
        base_url=base_url,
        tools=_default_tools(),
        stop_policy=DefaultStopPolicy(),
        sandbox=PathSandbox(allowed_dirs=[
            "C:/Users/Administrator/voice_input",
            "C:/Users/Administrator/voice_input_ghostline",
            "C:/Users/Administrator/oi_enhancements",
        ]),
        system_prompt=(
            "你是 OI agent 的代码审查 worker(基于 cursor-harness)。\n"
            "- 任务:对指定仓根跑真审查,输出严重度分级 + 文件路径行号 + 修复建议 + Top 3\n"
            "- 走 plan → parallel tool calls → observe → loop,直到 stop_policy 触发\n"
            "- 默认 qwen-max(DashScope),OpenAI 兼容协议\n"
            "- 不擅自 commit/push,只读 + 报告\n"
        ),
    )


def get_harness_for_claude(
    model: str = "claude-opus-4-8",
    api_key: str | None = None,
    base_url: str | None = None,
) -> CursorHarness:
    """给我(Claude Code)用 — Anthropic Claude Opus 4.8"""
    return CursorHarness(
        model=model,
        api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        base_url=base_url,
        tools=_default_tools(),
        stop_policy=DefaultStopPolicy(),
        sandbox=PathSandbox(allowed_dirs=[
            "C:/Users/Administrator/voice_input",
            "C:/Users/Administrator/voice_input_ghostline",
            "C:/Users/Administrator/oi_enhancements",
        ]),
        system_prompt=(
            "你是 Claude Code 的 cursor-harness 包装(类似 Cursor Cloud Agent)。\n"
            "- 任务:对指定仓根跑真审查,输出严重度分级 + 文件路径行号 + 修复建议 + Top 3\n"
            "- 走 plan → parallel tool calls → observe → loop\n"
            "- Claude Opus 4.8 + Anthropic SDK\n"
            "- 不擅自 commit/push,只读 + 报告\n"
        ),
    )
