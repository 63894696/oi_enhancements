"""
tests/test_replan.py — Skills 工作台 Phase 3 两阶段 replan 闸门测试(2026-09-28)。

验证 8 维度:
  1. plan_skill_calls 正常路径 — LLM 返 JSON → SkillCall list
  2. plan_skill_calls 容错 — JSON 内嵌 / ```json 包装 / 缺字段
  3. plan_skill_calls LLM 失败/超时 → []
  4. parse_plan_response 多种 JSON 形态(calls/arguments/calls 别名/直接 list)
  5. _should_replan:replan_enabled=False 永不弹卡
  6. _should_replan:L0 全过不弹卡
  7. _should_replan:L1+ ≤ 阈值自动执行;L1+ > 阈值弹卡
  8. maybe_replan_and_execute 整套流程:auto_execute + need_replan 两条路径
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# module-level register
from prisir_work import poster_capabilities  # noqa: E402,F401
from prisir_work import poster_to_image_capability  # noqa: E402,F401
from prisir_work import free_for_dev_capabilities  # noqa: E402,F401
from prisir_work import agency_capabilities  # noqa: E402,F401

from prisir_work.skills.replan import (  # noqa: E402
    _should_replan,
    plan_skill_calls,
    parse_plan_response,
    maybe_replan_and_execute,
    DEFAULT_AUTO_EXECUTE_L1_THRESHOLD,
)
from prisir_work.skills.schema import SkillCall, SkillResult  # noqa: E402


# ── 1. plan_skill_calls 正常路径 ─────────────────────────────
def test_1_plan_skill_calls_basic():
    async def mock_llm(messages):
        return json.dumps({"calls": [
            {"skill_id": "poster.smart", "args": {"theme": "奶茶"}},
            {"skill_id": "video.info", "args": {"path": "x.mp4"}},
        ]})

    calls = asyncio.run(plan_skill_calls("做个奶茶海报 + 查视频", mock_llm))
    assert len(calls) == 2
    assert calls[0].skill_id == "poster.smart"
    assert calls[0].args["theme"] == "奶茶"
    assert calls[1].skill_id == "video.info"
    # risk 自动补
    assert calls[0].risk == "L0"  # poster.smart
    print(f"✓ plan_skill_calls: 2 calls 解析正确,risk 自动补")


# ── 2. parse_plan_response 多形态 ────────────────────────────
def test_2_parse_plan_response_shapes():
    # 形态 1:标准
    r1 = parse_plan_response('{"calls": [{"skill_id": "video.info", "args": {"path": "a.mp4"}}]}')
    assert len(r1) == 1
    assert r1[0].skill_id == "video.info"

    # 形态 2:```json 包装
    r2 = parse_plan_response('```json\n{"calls": [{"skill_id": "free.find", "args": {"query": "pg"}}]}\n```')
    assert len(r2) == 1
    assert r2[0].skill_id == "free.find"

    # 形态 3:arguments 别名
    r3 = parse_plan_response('{"calls": [{"skill_id": "video.info", "arguments": {"path": "b.mp4"}}]}')
    assert len(r3) == 1
    assert r3[0].args["path"] == "b.mp4"

    # 形态 4:直接 list
    r4 = parse_plan_response('[{"skill_id": "video.info", "args": {"path": "c.mp4"}}]')
    assert len(r4) == 1
    assert r4[0].skill_id == "video.info"

    # 形态 5:plan 别名
    r5 = parse_plan_response('{"plan": [{"skill_id": "video.info", "args": {"path": "d.mp4"}}]}')
    assert len(r5) == 1

    # 失败路径
    assert parse_plan_response("not json") == []
    assert parse_plan_response("") == []
    assert parse_plan_response("```\nplain text\n```") == []
    print(f"✓ parse_plan_response: 5 形态 + 3 失败路径全覆盖")


# ── 3. plan_skill_calls 失败/超时 → [] ───────────────────────
def test_3_plan_skill_calls_failures():
    # 异常
    async def bad_llm(messages):
        raise RuntimeError("llm exploded")

    calls = asyncio.run(plan_skill_calls("query", bad_llm))
    assert calls == [], f"expected [], got {calls}"

    # 超时
    async def slow_llm(messages):
        await asyncio.sleep(2.0)
        return '{"calls": []}'

    calls2 = asyncio.run(plan_skill_calls("query", slow_llm, timeout_s=0.2))
    assert calls2 == []
    print("✓ plan_skill_calls: LLM 异常/超时都返 [] (fail-open)")


# ── 4. _should_replan 决策矩阵 ──────────────────────────────
def test_4_should_replan_decisions():
    # 场景 1:replan_enabled=False → 永不弹卡
    assert _should_replan(
        [SkillCall(skill_id="x", args={}, risk="L2")],
        replan_enabled=False) is False

    # 场景 2:空 calls → 不弹
    assert _should_replan([]) is False

    # 场景 3:全 L0 → 不弹
    calls_l0 = [
        SkillCall(skill_id="video.info", args={}, risk="L0"),
        SkillCall(skill_id="free.find",  args={}, risk="L0"),
    ]
    assert _should_replan(calls_l0) is False

    # 场景 4:L1+ ≤ 阈值 → 不弹(默认阈值=2)
    calls_l1_small = [
        SkillCall(skill_id="video.cut", args={}, risk="L1"),
    ]
    assert _should_replan(calls_l1_small) is False

    # 场景 5:L1+ > 阈值 → 弹
    calls_l1_big = [
        SkillCall(skill_id="video.cut", args={}, risk="L1"),
        SkillCall(skill_id="publish.html", args={}, risk="L2"),
        SkillCall(skill_id="youtube.upload", args={}, risk="L2"),
    ]
    assert _should_replan(calls_l1_big) is True

    # 阈值=0 时,任何 L1+ 都弹
    assert _should_replan(calls_l1_small,
                          auto_execute_l1_threshold=0) is True
    print(f"✓ _should_replan: 5 决策场景全对(阈值={DEFAULT_AUTO_EXECUTE_L1_THRESHOLD})")


# ── 5. maybe_replan_and_execute 整套(auto_execute 路径) ──────
def test_5_maybe_replan_auto_execute():
    """L0 + L1 ≤ 2 → 自动执行,executed 返 SkillResult list"""
    async def mock_llm(messages):
        return json.dumps({"calls": [
            {"skill_id": "video.info", "args": {"path": "x.mp4"}},
            {"skill_id": "free.find",  "args": {"query": "pg"}},
        ]})

    # mock video.info / free.find handlers
    from prisir_work import endpoints as _ep
    real_v = _ep._REGISTRY["/video/info"]
    real_f = _ep._REGISTRY["/free/find"]
    h_video = mock.MagicMock(return_value=({"ok": True, "duration": 60}, 200))
    h_free = mock.MagicMock(return_value=({"ok": True, "results": []}, 200))
    real_v["handler"] = h_video
    real_f["handler"] = h_free
    try:
        out = asyncio.run(maybe_replan_and_execute(
            "查视频 + 找免费 PG",
            mock_llm,
            replan_enabled=True,
            auto_execute_l1_threshold=2,
        ))
    finally:
        pass

    assert out["need_replan"] is False
    assert out["reason"] == "auto_executed"
    assert len(out["calls"]) == 2
    assert len(out["executed"]) == 2
    assert all(r.ok for r in out["executed"])
    assert h_video.call_count == 1
    assert h_free.call_count == 1
    print(f"✓ maybe_replan auto_execute: 2 calls, executed=2 OK")


# ── 6. maybe_replan_and_execute (need_replan 路径) ──────────
def test_6_maybe_replan_need_replan():
    """L1+ > 2 → need_replan=True,executed=None"""
    async def mock_llm(messages):
        return json.dumps({"calls": [
            {"skill_id": "video.cut",       "args": {"path": "x.mp4", "start": "0", "end": "10"}},
            {"skill_id": "publish.html",    "args": {"title": "t", "html": "<p/>"}},
            {"skill_id": "youtube.upload",  "args": {"path": "x.mp4", "title": "T"}},
        ]})

    fired: list[list[SkillCall]] = []

    def on_confirm(calls):
        fired.append(calls)

    out = asyncio.run(maybe_replan_and_execute(
        "剪视频 + 发公众号 + 传 YouTube",
        mock_llm,
        replan_enabled=True,
        auto_execute_l1_threshold=2,
        on_plan_need_confirm=on_confirm,
    ))
    assert out["need_replan"] is True
    assert out["executed"] is None
    assert out["reason"] == "l1_count_exceeds_threshold"
    assert len(out["calls"]) == 3
    # on_plan_need_confirm 调了
    assert len(fired) == 1
    assert len(fired[0]) == 3
    print(f"✓ maybe_replan need_replan: 3 L1+ calls → 弹卡回调触发")


# ── 7. maybe_replan_and_execute (LLM 失败 → empty_plan) ──────
def test_7_maybe_replan_empty_plan():
    async def bad_llm(messages):
        raise RuntimeError("LLM 炸了")

    out = asyncio.run(maybe_replan_and_execute("query", bad_llm))
    assert out["reason"] == "empty_plan"
    assert out["calls"] == []
    assert out["need_replan"] is False
    print("✓ maybe_replan empty_plan: LLM 异常 → empty_plan,need_replan=False")


# ── 8. replan_enabled=False 时不走 replan 路径 ──────────────
def test_8_replan_disabled_always_false():
    async def mock_llm(messages):
        return json.dumps({"calls": [
            {"skill_id": "publish.html", "args": {"title": "t", "html": "<p/>"}},
            {"skill_id": "youtube.upload", "args": {"path": "x", "title": "T"}},
            {"skill_id": "video.cut", "args": {"path": "x", "start": "0", "end": "10"}},
        ]})

    fired: list = []

    out = asyncio.run(maybe_replan_and_execute(
        "query", mock_llm,
        replan_enabled=False,
        on_plan_need_confirm=lambda c: fired.append(c),
    ))
    # replan_enabled=False → need_replan=False,但仍然自动执行(LLM 决定的就跑)
    assert out["need_replan"] is False
    assert out["reason"] == "auto_executed"
    assert len(out["calls"]) == 3
    assert len(out["executed"]) == 3
    assert fired == [], "replan 关 → on_confirm 不触发"
    print("✓ replan_enabled=False: need_replan=False, but still auto_executed")


if __name__ == "__main__":
    test_1_plan_skill_calls_basic()
    test_2_parse_plan_response_shapes()
    test_3_plan_skill_calls_failures()
    test_4_should_replan_decisions()
    test_5_maybe_replan_auto_execute()
    test_6_maybe_replan_need_replan()
    test_7_maybe_replan_empty_plan()
    test_8_replan_disabled_always_false()
    print("\n所有 8 组断言通过 ✅")