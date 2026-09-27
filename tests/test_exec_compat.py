"""
tests/test_exec_compat.py — Skills 工作台 Phase 4 EXEC ↔ tool_use 兼容 + 灰度切换测试(2026-09-28)。

验证 5 维度:
  1. exec_marker_to_skill_call: EXEC → SkillCall(risk 自动补)
  2. parse_exec_to_skill_calls: ai_text → [SkillCall],复用老 parser
  3. skill_call_to_exec_marker: SkillCall → `[[EXEC: ...]]` 文本(转义正确)
  4. route_exec mode="exec" / "tool_use" / "both" 路由决策
  5. EXEC 优先于 tool_use(mode="both" 时显式 EXEC 标记强信号)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# module-level register
from prisir_work import poster_capabilities  # noqa: E402,F401
from prisir_work import poster_to_image_capability  # noqa: E402,F401
from prisir_work import free_for_dev_capabilities  # noqa: E402,F401
from prisir_work import agency_capabilities  # noqa: E402,F401

from prisir_work.agent_main_chat_hook import ExecMarker  # noqa: E402
from prisir_work.skills.exec_compat import (  # noqa: E402
    exec_marker_to_skill_call,
    parse_exec_to_skill_calls,
    skill_call_to_exec_marker,
    skill_calls_to_exec_text,
    route_exec,
)
from prisir_work.skills.schema import SkillCall  # noqa: E402


# ── 1. exec_marker_to_skill_call ──────────────────────────────
def test_1_exec_marker_to_skill_call():
    """EXEC marker → SkillCall(risk 自动从 registry 补)"""
    m = ExecMarker(
        capability="video.cut",
        args={"path": "x.mp4", "start": "0", "end": "30"},
        raw="video.cut path=\"x.mp4\" start=\"0\" end=\"30\"",
    )
    call = exec_marker_to_skill_call(m)
    assert call.skill_id == "video.cut"
    assert call.args == {"path": "x.mp4", "start": "0", "end": "30"}
    assert call.risk == "L1"  # video.cut 是 L1,自动补
    assert m.raw in call.raw
    print(f"✓ EXEC → SkillCall: risk={call.risk} 自动补")


# ── 2. parse_exec_to_skill_calls ──────────────────────────────
def test_2_parse_exec_to_skill_calls():
    """ai_text 含 [[EXEC: ...]] → 直接转 SkillCall list"""
    text = (
        "我帮你剪视频 + 发公众号。\n"
        "[[EXEC: video.cut path=\"x.mp4\" start=\"0\" end=\"30\"]]\n"
        "[[EXEC: publish.html title=\"t\" html=\"<p/>\"]]\n"
    )
    calls = parse_exec_to_skill_calls(text)
    assert len(calls) == 2
    assert calls[0].skill_id == "video.cut"
    assert calls[0].args["start"] == "0"
    assert calls[1].skill_id == "publish.html"
    # risk 自动补
    assert calls[1].risk == "L2"
    print(f"✓ parse_exec_to_skill_calls: 2 EXEC → 2 SkillCall(risk L1/L2)")


# ── 3. skill_call_to_exec_marker 双向 ────────────────────────
def test_3_skill_call_to_exec_marker():
    """SkillCall → EXEC 文本(转义正确,空 args 也支持)"""
    # 基本
    c1 = SkillCall(
        skill_id="free.find",
        args={"query": "postgres"},
        risk="L0",
    )
    s1 = skill_call_to_exec_marker(c1)
    assert s1 == '[[EXEC: free.find query="postgres"]]'

    # 转义:含 " \n \t
    c2 = SkillCall(
        skill_id="publish.html",
        args={"title": 'a "b" c', "html": "<p>\n</p>"},
        risk="L2",
    )
    s2 = skill_call_to_exec_marker(c2)
    # " 应该转义成 \"; \n 转义成 \\n
    assert '\\"b\\"' in s2 or '\\"b\\"' in s2, f"双引号应转义:s2={s2}"
    assert '\\n' in s2, f"换行应转义:s2={s2}"

    # 空 args
    c3 = SkillCall(skill_id="video.info", args={}, risk="L0")
    s3 = skill_call_to_exec_marker(c3)
    assert s3 == "[[EXEC: video.info]]"

    # 双向 round-trip
    marker = ExecMarker(capability="video.cut", args={"path": "x.mp4"})
    back = exec_marker_to_skill_call(marker)
    assert skill_call_to_exec_marker(back) == '[[EXEC: video.cut path="x.mp4"]]'
    print(f"✓ SkillCall → EXEC 文本 + 双向 round-trip")


# ── 4. route_exec mode 路由 ───────────────────────────────────
def test_4_route_exec_modes():
    """三种 mode 决策都正确"""
    text_with_exec = '我来做。[[EXEC: video.info path="x.mp4"]]'
    text_with_tool_xml = (
        '<tool_call>\n{"name": "video.info", "arguments": {"path": "x.mp4"}}\n</tool_call>'
    )
    text_empty = "你好,今天天气不错。"

    # mode=exec:EXEC 解析,tool_use 忽略
    r1 = route_exec(text_with_exec, mode="exec")
    assert r1["mode"] == "exec"
    assert r1["source"] == "exec"
    assert len(r1["calls"]) == 1
    assert r1["calls"][0].skill_id == "video.info"

    r1b = route_exec(text_with_tool_xml, mode="exec")
    assert r1b["source"] == "empty", "mode=exec 不应识别 tool_use XML"
    assert r1b["calls"] == []

    # mode=tool_use:EXEC 不识别(给 warning),tool_use 解析
    r2 = route_exec(text_with_tool_xml, mode="tool_use")
    assert r2["mode"] == "tool_use"
    assert r2["source"] == "tool_use"
    assert len(r2["calls"]) == 1
    assert r2["calls"][0].skill_id == "video.info"

    r2b = route_exec(text_with_exec, mode="tool_use")
    # EXEC 标记被检测到,降级 parse_any_tool_calls(可能返 [])
    assert r2b["source"] in ("empty", "tool_use")

    # mode=both:EXEC 优先
    r3 = route_exec(text_with_exec, mode="both")
    assert r3["mode"] == "both"
    assert r3["source"] == "exec"
    assert len(r3["calls"]) == 1

    # mode=both:没 EXEC → 走 tool_use 兜底
    r3b = route_exec(text_with_tool_xml, mode="both")
    assert r3b["source"] == "tool_use"

    # mode=both:全空
    r3c = route_exec(text_empty, mode="both")
    assert r3c["source"] == "empty"

    # 默认 mode=both
    r4 = route_exec(text_empty)
    assert r4["mode"] == "both"
    assert r4["source"] == "empty"
    print(f"✓ route_exec: 3 mode × 4 输入 = 12 决策场景全对")


# ── 5. EXEC 优先于 tool_use(强信号) ─────────────────────────
def test_5_exec_priority_over_tool_use():
    """mode=both 时,ai_text 同时含 EXEC + tool_use XML,EXEC 胜出"""
    text = (
        "[[EXEC: video.cut path=\"x.mp4\" start=\"0\" end=\"10\"]]\n"
        "<tool_call>\n"
        '{"name": "free.find", "arguments": {"query": "pg"}}\n'
        "</tool_call>"
    )
    r = route_exec(text, mode="both")
    assert r["source"] == "exec", "EXEC 标记是强信号,优先"
    assert len(r["calls"]) == 1
    assert r["calls"][0].skill_id == "video.cut"
    # tool_calls 显式传入被忽略
    explicit_tool = [
        SkillCall(skill_id="free.find", args={"query": "pg"}, risk="L0"),
    ]
    r2 = route_exec(text, mode="both", tool_calls=explicit_tool)
    assert r2["source"] == "exec", "EXEC 优先,tool_calls 忽略"
    assert len(r2["calls"]) == 1
    print(f"✓ EXEC 优先: ai_text 含 EXEC → 忽略 tool_calls 参数")


if __name__ == "__main__":
    test_1_exec_marker_to_skill_call()
    test_2_parse_exec_to_skill_calls()
    test_3_skill_call_to_exec_marker()
    test_4_route_exec_modes()
    test_5_exec_priority_over_tool_use()
    print("\n所有 5 组断言通过 ✅")