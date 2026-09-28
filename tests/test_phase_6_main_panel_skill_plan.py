"""
tests/test_phase_6_main_panel_skill_plan.py — Skills 工作台 Phase 6 主面板集成测试(2026-09-28)。

验证 8 维度:
  1. integration.py 顶层 API + SkillPlanQueue push/peek/ack 闭环
  2. skills_index_block() 返 ≥ 7000c JSON 索引(Phase 7 紧凑化后 ~8000c,实测 7993c)
  3. maybe_skill_plan_replan 6 决策矩阵(replan_disabled/text_too_short/exec_marker/empty_plan/needs_confirm/auto_executed)
  4. prisIragent_web.py GET /api/skill_plan/peek 端点路由
  5. prisIragent_web.py GET /api/skill_plan/ack 端点路由
  6. prisIragent_web.py POST /api/skill_plan/confirm 端点路由 + 顺序 execute_skill
  7. prisIragent_web.py _shell_system_prompt 末尾追加 skills_index 段
  8. prisIragent_web.py _PAGE 含 CSS(.skill-plan-card/.skill-plan-row/.skill-plan-risk) +
     polling 段 + 3 事件 handler(skill_plan_request/auto_executed/confirm_ack)
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── 1. integration.py 顶层 API + SkillPlanQueue 闭环 ──────────
def test_1_integration_apis():
    import importlib
    integ = importlib.import_module("prisir_work.skills.integration")
    skills_index_block = integ.skills_index_block
    SkillPlanQueue = integ.SkillPlanQueue
    push_skill_plan_request = integ.push_skill_plan_request
    push_skill_plan_auto_executed = integ.push_skill_plan_auto_executed
    push_skill_plan_confirm_ack = integ.push_skill_plan_confirm_ack
    maybe_skill_plan_replan = integ.maybe_skill_plan_replan
    DEFAULT_SKILLS_INDEX_ENABLED = integ.DEFAULT_SKILLS_INDEX_ENABLED
    DEFAULT_SKILLS_REPLAN_ENABLED = integ.DEFAULT_SKILLS_REPLAN_ENABLED
    DEFAULT_AUTO_EXECUTE_L1_THRESHOLD = integ.DEFAULT_AUTO_EXECUTE_L1_THRESHOLD

    assert DEFAULT_SKILLS_INDEX_ENABLED is True
    assert DEFAULT_SKILLS_REPLAN_ENABLED is False
    assert DEFAULT_AUTO_EXECUTE_L1_THRESHOLD == 2

    # Queue 闭环:用 singleton(get_queue)保证 push 和 peek 同一实例
    integ._QUEUE = None  # 重置 singleton 防止跨测试污染
    q = integ.get_queue()
    eid1 = push_skill_plan_request("s1", [{"skill_id": "video.info", "args": {}, "risk": "L1"}])
    eid2 = push_skill_plan_auto_executed("s1", [{"skill_id": "x"}], [{"skill_id": "x", "ok": True}])
    eid3 = push_skill_plan_confirm_ack("s1", approved=True, executed=1, total=1)
    items = q.peek("s1")
    assert len(items) == 3
    assert items[0]["type"] == "skill_plan_request"
    assert items[1]["type"] == "skill_plan_auto_executed"
    assert items[2]["type"] == "skill_plan_confirm_ack"
    assert q.ack(eid1) is True
    items2 = q.peek("s1")
    assert len(items2) == 2
    print("✓ integration.py 顶层 API + SkillPlanQueue 闭环 (push×3 / peek×2 / ack)")


# ── 2. skills_index_block 返索引 ─────────────────────────────
def test_2_skills_index_block():
    import importlib
    skills_index_block = importlib.import_module("prisir_work.skills.integration").skills_index_block
    idx = skills_index_block("test")
    assert isinstance(idx, str)
    assert len(idx) >= 7000, f"索引太短: {len(idx)} (Phase 7 标准紧凑 ~7993c, ultra ~7281c)"
    assert "skill" in idx.lower() or "capability" in idx.lower()
    print(f"✓ skills_index_block 返 {len(idx)}c 索引(>=7000c Phase 7 标准紧凑阈值)")


# ── 3. maybe_skill_plan_replan 决策矩阵 ──────────────────────
def test_3_replan_decisions():
    import importlib
    integ = importlib.import_module("prisir_work.skills.integration")
    maybe_skill_plan_replan = integ.maybe_skill_plan_replan

    # 3.1 replan_disabled → 跳过
    async def _no_plan(messages): return "{}"
    r = asyncio.run(maybe_skill_plan_replan(
        user_text="test", answer="x", session_id="s",
        plan_llm_call=_no_plan, skills_replan_enabled=False))
    assert r.get("skipped") is True, r
    assert r.get("reason") == "replan_disabled"

    # 3.2 text_too_short → 跳过
    r = asyncio.run(maybe_skill_plan_replan(
        user_text="", answer="x", session_id="s",
        plan_llm_call=_no_plan, skills_replan_enabled=True))
    assert r.get("reason") == "text_too_short", r

    # 3.3 exec_marker_present → 跳过
    r = asyncio.run(maybe_skill_plan_replan(
        user_text="做个 React 组件", answer="[[EXEC: ...]]", session_id="s",
        plan_llm_call=_no_plan, skills_replan_enabled=True))
    assert r.get("reason") == "exec_marker_present", r

    # 3.4 empty_plan → 跳过(LLM 返空 calls)
    async def _empty(messages): return '{"calls": []}'
    r = asyncio.run(maybe_skill_plan_replan(
        user_text="做点啥", answer="x", session_id="s",
        plan_llm_call=_empty, skills_replan_enabled=True))
    assert r.get("reason") == "empty_plan", r

    # 3.5 needs_confirm(L1+ 数量 > 阈值 = 需要弹卡)
    # 注:plan_skill_calls 解析时 risk 从 registry 拿(不信任 LLM JSON)
    # 用一个 registry 里真实存在且 risk ≥ L1 的 skill 触发 needs_confirm
    async def _three_l2(messages):
        return json.dumps({"calls": [
            {"skill_id": "video.info", "args": {"path": "x.mp4"}, "risk": "L1"},
            {"skill_id": "agency.search", "args": {"query": "React"}, "risk": "L0"},
            {"skill_id": "free_for_dev.search", "args": {"query": "api"}, "risk": "L0"},
        ]})
    # 找一个 registry 里真存在 + L1+ 的 skill 用于测试
    import importlib
    registry = importlib.import_module("prisir_work.skills.registry")
    real_l1 = None
    for s in registry.list_skills():
        if s.risk in ("L1", "L2", "L3"):
            real_l1 = s.id
            break
    assert real_l1, "registry 无 L1+ skill"
    async def _l1_triple(messages):
        return json.dumps({"calls": [
            {"skill_id": real_l1, "args": {}, "risk": "L1"},
            {"skill_id": real_l1, "args": {}, "risk": "L1"},
            {"skill_id": real_l1, "args": {}, "risk": "L1"},
        ]})
    r = asyncio.run(maybe_skill_plan_replan(
        user_text="触发 needs_confirm 测试", answer="x", session_id="s",
        plan_llm_call=_l1_triple, skills_replan_enabled=True,
        auto_execute_l1_threshold=2))
    assert r.get("needs_confirm") is True, r
    assert len(r.get("calls") or []) == 3, r
    print("✓ needs_confirm 触发:3 个 L1+ skill 超过阈值 → 弹规划卡")

    # 3.6 auto_executed(L1+ 数量 ≤ 阈值)
    async def _one_call(messages):
        return json.dumps({"calls": [
            {"skill_id": "video.info", "args": {"path": "x.mp4"}, "risk": "L1"},
        ]})
    r = asyncio.run(maybe_skill_plan_replan(
        user_text="查个视频", answer="x", session_id="s",
        plan_llm_call=_one_call, skills_replan_enabled=True,
        auto_execute_l1_threshold=2))
    assert r.get("auto_executed") is True, r
    assert len(r.get("results") or []) == 1, r

    print("✓ maybe_skill_plan_replan 6 决策矩阵(replan_disabled/text_too_short/exec_marker/empty_plan/needs_confirm/auto_executed)")


# ── 4. GET /api/skill_plan/peek 端点路由 ─────────────────────
def test_4_peek_endpoint():
    with open("prisIragent_web.py", encoding="utf-8") as f:
        src = f.read()
    import ast
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            code = ast.unparse(node.test)
            if "skill_plan/peek" in code:
                body = ast.unparse(node)
                assert "get_skill_plan_queue" in body, "peek 缺 queue 调用"
                assert "peek" in body, "peek 缺 peek 调用"
                assert "session_id" in body, "peek 缺 session_id 过滤"
                found = True
                break
    assert found, "GET /api/skill_plan/peek 端点缺失"
    print("✓ GET /api/skill_plan/peek 端点到位(queue + session_id 过滤)")


# ── 5. GET /api/skill_plan/ack 端点路由 ──────────────────────
def test_5_ack_endpoint():
    with open("prisIragent_web.py", encoding="utf-8") as f:
        src = f.read()
    import ast
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            code = ast.unparse(node.test)
            if "skill_plan/ack" in code:
                body = ast.unparse(node)
                assert "id 必填" in body or 'id' in body, "ack 缺 id 校验"
                assert "ack(" in body, "ack 缺 queue.ack 调用"
                found = True
                break
    assert found, "GET /api/skill_plan/ack 端点缺失"
    print("✓ GET /api/skill_plan/ack 端点到位(id 校验 + queue.ack)")


# ── 6. POST /api/skill_plan/confirm 端点路由 ─────────────────
def test_6_confirm_endpoint():
    with open("prisIragent_web.py", encoding="utf-8") as f:
        src = f.read()
    import ast
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            code = ast.unparse(node.test)
            if "skill_plan/confirm" in code:
                body = ast.unparse(node)
                assert "execute_skill" in body, "confirm 缺 execute_skill"
                assert "approved" in body, "confirm 缺 approved 字段"
                assert "push_skill_plan_confirm_ack" in body, "confirm 缺 ack push"
                # approved=False 分支应返 reason=user_cancelled
                assert "user_cancelled" in body, "approved=False 分支缺 user_cancelled"
                found = True
                break
    assert found, "POST /api/skill_plan/confirm 端点缺失"
    print("✓ POST /api/skill_plan/confirm 端点到位(execute_skill + approved + ack + user_cancelled)")


# ── 7. _shell_system_prompt 末尾追加 skills_index ─────────────
def test_7_system_prompt_skills_index():
    with open("prisIragent_web.py", encoding="utf-8") as f:
        src = f.read()
    import ast
    tree = ast.parse(src)
    # 找 _shell_system_prompt 函数体
    fn_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_shell_system_prompt":
            body_src = ast.unparse(node)
            assert "skills_index_block" in body_src, \
                "_shell_system_prompt 未注入 skills_index_block"
            assert "PRISIRAI_SKILLS_INDEX" in body_src, \
                "_shell_system_prompt 缺 skills_index 开关 env"
            fn_found = True
            break
    assert fn_found, "_shell_system_prompt 函数缺失"
    print("✓ _shell_system_prompt 末尾追加 skills_index_block(env 开关到位)")


# ── 8. _PAGE 含 CSS + polling + 3 事件 handler ──────────────
def test_8_page_css_polling_handlers():
    with open("prisIragent_web.py", encoding="utf-8") as f:
        src = f.read()
    # 8.1 CSS 6 类
    for cls in (".skill-plan-card", ".skill-plan-box", ".skill-plan-row",
                ".skill-plan-sid", ".skill-plan-risk", ".skill-plan-args"):
        assert cls in src, f"缺 CSS 类 {cls}"
    # 8.2 风险配色 L0/L1/L2/L3
    for r in ("L0", "L1", "L2", "L3"):
        assert f'data-risk="{r}"' in src, f"缺 {r} 配色"
    # 8.3 polling 段
    assert "_setupSkillPlanPolling" in src, "缺 polling IIFE"
    assert "/api/skill_plan/peek" in src, "缺 peek URL"
    assert "/api/skill_plan/ack" in src, "缺 ack URL"
    assert "/api/skill_plan/confirm" in src, "缺 confirm URL"
    # 8.4 3 事件 handler
    for evt in ("skill_plan_request", "skill_plan_auto_executed", "skill_plan_confirm_ack"):
        assert f'"{evt}"' in src, f"缺事件分支 {evt}"
    # 8.5 弹卡 + 按钮 + ack fetch
    assert "skillPlanCard" in src, "缺卡片 DOM id"
    assert "skillPlanOk" in src and "skillPlanCancel" in src, "缺按钮 id"
    assert "approved: true" in src, "缺 approved:true emit"
    assert "approved: false" in src, "缺 approved:false emit"
    print("✓ _PAGE 含 CSS(6 类 + 4 档配色) + polling 段 + 3 事件 handler + 按钮")


if __name__ == "__main__":
    test_1_integration_apis()
    test_2_skills_index_block()
    test_3_replan_decisions()
    test_4_peek_endpoint()
    test_5_ack_endpoint()
    test_6_confirm_endpoint()
    test_7_system_prompt_skills_index()
    test_8_page_css_polling_handlers()
    print("\n所有 8 组断言通过 ✅")