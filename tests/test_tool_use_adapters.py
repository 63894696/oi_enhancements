"""
tests/test_tool_use_adapters.py — Skills 工作台 Phase 2 tool_use 协议适配层测试(2026-09-28)。

验证 9 维度:
  Anthropic adapter (3):
    1. anthropic_tools_for:80 skill 全量生成 tools 数组,每项含 name/description/input_schema
    2. parse_anthropic_tool_use:content_block.type="tool_use" → SkillCall,字段正确
    3. to_anthropic_tool_result:SkillResult → tool_result block,content 字符串化 payload

  OpenAI adapter (3):
    4. openai_tools_for:80 function dict,内嵌 function.name/description/parameters
    5. parse_openai_tool_calls:message.tool_calls[] → SkillCall list
    6. to_openai_tool_message:SkillResult → role="tool" 消息

  通用 XML / exec_loop (3):
    7. parse_generic_xml_tool_calls:Qwen JSON / Hermes invoke 两种 XML 都识别
    8. parse_any_tool_calls:智能识别协议(dict / list / str)
    9. run_loop:mock LLM 调 2 轮,tools_call 1 次后停止 → calls/results/stopped_reason 全 OK
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 触发 module-level register_all 让 80 skill 都到位
from prisir_work import poster_capabilities  # noqa: E402,F401
from prisir_work import poster_to_image_capability  # noqa: E402,F401
from prisir_work import free_for_dev_capabilities  # noqa: E402,F401
from prisir_work import agency_capabilities  # noqa: E402,F401

from prisir_work.skills.tool_use import (  # noqa: E402
    anthropic_tools_for, parse_anthropic_tool_use,
    to_anthropic_tool_result,
    openai_tools_for, parse_openai_tool_calls,
    to_openai_tool_message,
    parse_generic_xml_tool_calls, parse_any_tool_calls,
    run_loop, run_loop_sync,
)
from prisir_work.skills.schema import SkillCall, SkillResult  # noqa: E402


# ── 1. Anthropic tools 数组 ───────────────────────────────────
def test_1_anthropic_tools_for():
    tools = anthropic_tools_for()
    assert len(tools) >= 80, f"expected ≥80 tools, got {len(tools)}"
    # 每项含 3 必填
    for t in tools[:3]:
        assert "name" in t and "description" in t and "input_schema" in t
    # input_schema 是合法 JSON Schema
    schema = tools[0]["input_schema"]
    assert schema["type"] == "object"
    assert "properties" in schema
    print(f"✓ Anthropic tools: {len(tools)} 项, 每项 ~{sum(len(json.dumps(t, ensure_ascii=False)) for t in tools[:10])//10} 字符(sample)")


# ── 2. parse_anthropic_tool_use ──────────────────────────────
def test_2_parse_anthropic_tool_use():
    block = {
        "type":  "tool_use",
        "id":    "toolu_abc123",
        "name":  "video.info",
        "input": {"path": "/Users/me/clip.mp4"},
    }
    call = parse_anthropic_tool_use(block)
    assert call is not None
    assert call.skill_id == "video.info"
    assert call.args == {"path": "/Users/me/clip.mp4"}
    assert call.risk == "L0"  # video.info 是 L0
    assert "toolu_abc123" in call.raw

    # 非 tool_use 块返 None
    assert parse_anthropic_tool_use({"type": "text", "text": "hi"}) is None
    assert parse_anthropic_tool_use(None) is None
    print(f"✓ parse_anthropic_tool_use: block → SkillCall(risk={call.risk})")


# ── 3. to_anthropic_tool_result ───────────────────────────────
def test_3_to_anthropic_tool_result():
    call = SkillCall(
        skill_id="video.info",
        args={"path": "x.mp4"},
        raw=json.dumps({"id": "toolu_xyz"}),
    )
    result = SkillResult(
        skill_id="video.info",
        ok=True,
        payload={"duration": 60, "resolution": "1920x1080"},
    )
    block = to_anthropic_tool_result(call, result, tool_use_id="toolu_xyz")
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "toolu_xyz"
    assert block["is_error"] is False
    payload = json.loads(block["content"])
    assert payload["duration"] == 60

    # 失败场景
    err_result = SkillResult(skill_id="video.info", ok=False, error="file_not_found")
    err_block = to_anthropic_tool_result(call, err_result, tool_use_id="toolu_xyz")
    assert err_block["is_error"] is True
    assert "file_not_found" in err_block["content"]
    print("✓ to_anthropic_tool_result: ok=True/False 双路径都正确")


# ── 4. OpenAI tools 数组 ──────────────────────────────────────
def test_4_openai_tools_for():
    tools = openai_tools_for()
    assert len(tools) >= 80
    for t in tools[:3]:
        assert t["type"] == "function"
        assert "name" in t["function"]
        assert "description" in t["function"]
        assert "parameters" in t["function"]
    # parameters 是合法 JSON Schema
    params = tools[0]["function"]["parameters"]
    assert params["type"] == "object"
    assert "properties" in params
    print(f"✓ OpenAI tools: {len(tools)} 项, function dict 结构正确")


# ── 5. parse_openai_tool_calls ────────────────────────────────
def test_5_parse_openai_tool_calls():
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id":       "call_abc",
            "type":     "function",
            "function": {
                "name":      "video.info",
                "arguments": '{"path": "/tmp/clip.mp4"}',
            },
        }],
    }
    calls = parse_openai_tool_calls(msg)
    assert len(calls) == 1
    assert calls[0].skill_id == "video.info"
    assert calls[0].args["path"] == "/tmp/clip.mp4"
    assert calls[0].risk == "L0"
    # raw 含 id
    assert "call_abc" in calls[0].raw

    # 整个 response 也接受
    resp = {"choices": [{"message": msg}]}
    calls2 = parse_openai_tool_calls(resp)
    assert len(calls2) == 1
    # 无 tool_calls → []
    assert parse_openai_tool_calls({"role": "user", "content": "hi"}) == []
    print(f"✓ parse_openai_tool_calls: msg + 整个 response 都识别")


# ── 6. to_openai_tool_message ─────────────────────────────────
def test_6_to_openai_tool_message():
    call = SkillCall(
        skill_id="free.find",
        args={"query": "postgres"},
        raw=json.dumps({"id": "call_xyz"}),
    )
    result = SkillResult(
        skill_id="free.find",
        ok=True,
        payload={"results": [{"name": "PostgreSQL"}]},
    )
    msg = to_openai_tool_message(call, result, tool_call_id="call_xyz")
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_xyz"
    payload = json.loads(msg["content"])
    assert payload["results"][0]["name"] == "PostgreSQL"
    print(f"✓ to_openai_tool_message: role=tool, tool_call_id 匹配")


# ── 7. parse_generic_xml_tool_calls ───────────────────────────
def test_7_parse_generic_xml_tool_calls():
    # 形式 1:Qwen JSON 内嵌
    text1 = """我来帮你查。
<tool_call>
{"name": "video.info", "arguments": {"path": "x.mp4"}}
</tool_call>
"""
    calls1 = parse_generic_xml_tool_calls(text1)
    assert len(calls1) >= 1
    assert calls1[0].skill_id == "video.info"
    assert calls1[0].args["path"] == "x.mp4"

    # 形式 3:Hermes invoke
    text2 = """<invoke name="free.find"><query>postgres</query></invoke>"""
    calls2 = parse_generic_xml_tool_calls(text2)
    assert len(calls2) == 1
    assert calls2[0].skill_id == "free.find"
    assert calls2[0].args["query"] == "postgres"

    # 混合 — 一次响应里两个 tool call
    text3 = text1 + text2
    calls3 = parse_generic_xml_tool_calls(text3)
    sids = sorted(c.skill_id for c in calls3)
    assert "video.info" in sids and "free.find" in sids
    print(f"✓ parse_generic_xml_tool_calls: Qwen JSON + Hermes invoke 双格式都识别")


# ── 8. parse_any_tool_calls 智能协议 ──────────────────────────
def test_8_parse_any_tool_calls():
    # dict → OpenAI
    msg = {"role": "assistant", "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "video.info", "arguments": '{"path": "y.mp4"}'}}]}
    calls = parse_any_tool_calls(msg)
    assert len(calls) == 1
    assert calls[0].skill_id == "video.info"

    # list → Anthropic content blocks
    blocks = [{"type": "text", "text": "好"},
              {"type": "tool_use", "id": "t1", "name": "video.info",
               "input": {"path": "z.mp4"}}]
    calls2 = parse_any_tool_calls(blocks)
    assert len(calls2) == 1
    assert calls2[0].skill_id == "video.info"

    # str → XML fallback
    txt = '<tool_call>{"name": "agency.search", "arguments": {"query": "React"}}</tool_call>'
    calls3 = parse_any_tool_calls(txt)
    assert len(calls3) == 1
    assert calls3[0].skill_id == "agency.search"

    # 都不是 → []
    assert parse_any_tool_calls(None) == []
    print(f"✓ parse_any_tool_calls: dict/list/str 三协议自动选")


# ── 9. run_loop 完整循环 ──────────────────────────────────────
def test_9_run_loop():
    """mock 一个 LLM,第一轮吐 tool_call(调 agency.search),第二轮返纯文本结束。"""
    state = {"step": 0}

    async def mock_llm(messages, tools):
        state["step"] += 1
        if state["step"] == 1:
            # 第一轮:LLM 决定调 agency.search
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "我先搜一下 React 工程师角色",
                        "tool_calls": [{
                            "id": "call_l1",
                            "type": "function",
                            "function": {
                                "name": "agency.search",
                                "arguments": '{"query": "React"}',
                            },
                        }],
                    },
                }],
            }
        # 第二轮:LLM 拿到结果,生成文本回复(无 tool_call)
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "找到了 React 工程师角色:engineering-frontend-developer",
                    "tool_calls": None,
                },
            }],
        }

    # mock agency.search handler(避免真读 JSON)
    from prisir_work import endpoints as _ep
    real_entry = _ep._REGISTRY["/agency/search"]
    sentinel = mock.MagicMock(return_value=(
        {"ok": True, "results": [{"slug": "engineering-frontend-developer",
                                   "name": "Frontend Developer"}],
         "total": 1, "returned": 1}, 200))
    real_entry["handler"] = sentinel
    try:
        result = run_loop_sync(
            user_text="给我一个 React 工程师角色",
            llm_call=mock_llm,
            protocol="openai",
            max_steps=4,
        )
    finally:
        pass

    assert len(result["calls"]) == 1
    assert result["calls"][0].skill_id == "agency.search"
    assert result["calls"][0].args["query"] == "React"
    assert len(result["results"]) == 1
    assert result["results"][0].ok is True
    assert result["stopped_reason"] == "no_more_tool_call"
    assert sentinel.call_count == 1
    # messages 含 user + assistant(tool_call)+ tool
    roles = [m.get("role") for m in result["messages"]]
    assert "tool" in roles, f"messages 缺 role=tool, 实际 {roles}"
    print(f"✓ run_loop: 1 step tool_call + 1 step 文本, calls={len(result['calls'])}, "
          f"results={len(result['results'])}, reason={result['stopped_reason']}")


if __name__ == "__main__":
    test_1_anthropic_tools_for()
    test_2_parse_anthropic_tool_use()
    test_3_to_anthropic_tool_result()
    test_4_openai_tools_for()
    test_5_parse_openai_tool_calls()
    test_6_to_openai_tool_message()
    test_7_parse_generic_xml_tool_calls()
    test_8_parse_any_tool_calls()
    test_9_run_loop()
    print("\n所有 9 组断言通过 ✅")