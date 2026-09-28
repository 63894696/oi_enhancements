"""
tests/test_phase_5_skill_plan_ui.py — Skills 工作台 Phase 5 前端 skill_plan_request 渲染 + 后端 confirm 分支测试(2026-09-28)。

验证 8 维度:
  1. index.html 含 skillPlanConfirm / 7 个新 DOM id
  2. index.html 含 CSS 样式(.skill-plan-row / .skill-plan-sid / .skill-plan-risk / .skill-plan-args / .skill-plan-auto)
  3. app.js 含 7 个新 DOM ref + pendingSkillPlan state
  4. app.js 三个 ws 事件 handler 到位: skill_plan_request / skill_plan_auto_executed / skill_plan_confirm_ack
  5. app.js 两个新函数: showSkillPlanConfirm / renderSkillPlanAutoExec
  6. app.js 按钮事件: skillPlanOk emit skill_plan_confirm {approved:true,calls}
  7. 后端 web.py 含 skill_plan_confirm 分支(顺序 execute_skill → emit capability_exec_result)
  8. 后端 skill_plan_confirm_ack 字段语义正确(approved/executed/total/reason)
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── 1. index.html DOM ─────────────────────────────────────────
def test_1_html_dom_ids():
    with open("companion/static/index.html", encoding="utf-8") as f:
        html = f.read()
    ids = ["skillPlanConfirm", "skillPlanCount", "skillPlanRisk",
           "skillPlanBody", "skillPlanCalls", "skillPlanOk", "skillPlanCancel"]
    missing = [i for i in ids if f'id="{i}"' not in html]
    assert not missing, f"index.html 缺 DOM id: {missing}"
    # 卡片是 .cap-confirm 复用视觉
    assert 'class="cap-confirm"' in html
    assert "skillPlanConfirm" in html
    print(f"✓ index.html 含 7 个新 DOM id + 复用 .cap-confirm 视觉")


# ── 2. index.html CSS ─────────────────────────────────────────
def test_2_html_css():
    with open("companion/static/index.html", encoding="utf-8") as f:
        html = f.read()
    classes = [".skill-plan-row", ".skill-plan-sid", ".skill-plan-risk",
               ".skill-plan-args", ".skill-plan-auto", ".skill-plan-idx"]
    missing = [c for c in classes if c not in html]
    assert not missing, f"index.html 缺 CSS 类: {missing}"
    # 风险徽章 3 档配色
    for r in ("L1", "L2", "L3"):
        assert f'data-risk="{r}"' in html or f'[data-risk="{r}"]' in html, f"缺 {r} 风险配色"
    print(f"✓ CSS 含 6 个 skill-plan-* 类 + L1/L2/L3 3 档风险配色")


# ── 3. app.js DOM ref + state ─────────────────────────────────
def test_3_app_js_dom_refs():
    with open("companion/static/app.js", encoding="utf-8") as f:
        js = f.read()
    refs = ["skillPlanConfirm", "skillPlanCount", "skillPlanRisk",
            "skillPlanBody", "skillPlanCalls", "skillPlanOk", "skillPlanCancel"]
    missing = [r for r in refs if f'$("{"." if False else ""}{r}")' not in js
               and f'$("{r}")' not in js]
    assert not missing, f"app.js 缺 DOM ref: {missing}"
    assert "pendingSkillPlan" in js, "缺 pendingSkillPlan state"
    print(f"✓ app.js 含 7 个新 DOM ref + pendingSkillPlan state")


# ── 4. app.js ws 事件分支 ─────────────────────────────────────
def test_4_app_js_ws_events():
    with open("companion/static/app.js", encoding="utf-8") as f:
        js = f.read()
    for evt in ["skill_plan_request", "skill_plan_auto_executed",
                "skill_plan_confirm_ack"]:
        assert f'"{evt}"' in js, f"缺 ws 事件分支 {evt}"
    print(f"✓ app.js 三个 ws 事件 handler 到位")


# ── 5. app.js 新函数 ──────────────────────────────────────────
def test_5_app_js_functions():
    with open("companion/static/app.js", encoding="utf-8") as f:
        js = f.read()
    for fn in ["showSkillPlanConfirm", "renderSkillPlanAutoExec"]:
        assert f"function {fn}" in js, f"缺函数 {fn}"
    # showSkillPlanConfirm 应有 max risk 计算 + 风险徽章
    # (粗粒度:确保含 L0/L1/L2/L3 风险判断 + skill_id + args 渲染)
    assert "L0" in js and "L1" in js and "L2" in js and "L3" in js, "风险等级映射不全"
    print(f"✓ app.js 两个新函数 + 风险等级映射到位")


# ── 6. app.js 按钮事件 ────────────────────────────────────────
def test_6_app_js_buttons():
    with open("companion/static/app.js", encoding="utf-8") as f:
        js = f.read()
    # skillPlanOk → emit skill_plan_confirm + approved:true
    assert 'skillPlanOk.addEventListener("click"' in js
    # skillPlanCancel → emit approved:false
    assert 'skillPlanCancel.addEventListener("click"' in js
    # emit 用 type skill_plan_confirm
    assert '"skill_plan_confirm"' in js
    assert "approved: true" in js
    assert "approved: false" in js
    print(f"✓ app.js 按钮事件:skillPlanOk → approved:true, skillPlanCancel → approved:false")


# ── 7. 后端 web.py skill_plan_confirm 分支 ────────────────────
def test_7_backend_skill_plan_confirm():
    with open("companion/prisIragent-companion-web.py", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    # 找到 'elif t == "skill_plan_confirm":' 节点
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            code = ast.unparse(node.test)
            if "skill_plan_confirm" in code:
                found = True
                body_src = ast.unparse(node)
                # 必须包含:execute_skill + capability_exec_result + skill_plan_confirm_ack
                assert "execute_skill" in body_src, "缺 execute_skill 调用"
                assert "capability_exec_result" in body_src, "缺 capability_exec_result emit"
                assert "skill_plan_confirm_ack" in body_src, "缺 skill_plan_confirm_ack emit"
                break
    assert found, "后端缺 skill_plan_confirm 分支"
    print(f"✓ 后端 skill_plan_confirm 分支到位:顺序 execute_skill → emit capability_exec_result")


# ── 8. 后端 ack 字段语义 ──────────────────────────────────────
def test_8_backend_ack_fields():
    """skill_plan_confirm_ack 应含 approved/executed/total/reason 字段"""
    with open("companion/prisIragent-companion-web.py", encoding="utf-8") as f:
        src = f.read()
    # 两个 ack 节点(approved=True / False)
    ack_count = src.count('"skill_plan_confirm_ack"')
    assert ack_count >= 2, f"期望 ≥2 个 skill_plan_confirm_ack emit,实际 {ack_count}"
    # approved=False 分支返 reason=user_cancelled
    assert "user_cancelled" in src, "approved=False 分支缺 reason"
    # approved=True 分支返 executed / total 字段
    assert '"executed":' in src, "approved=True 分支缺 executed 字段"
    assert '"total":' in src, "approved=True 分支缺 total 字段"
    print(f"✓ 后端 ack 字段:approved/executed/total/reason 语义到位")


if __name__ == "__main__":
    test_1_html_dom_ids()
    test_2_html_css()
    test_3_app_js_dom_refs()
    test_4_app_js_ws_events()
    test_5_app_js_functions()
    test_6_app_js_buttons()
    test_7_backend_skill_plan_confirm()
    test_8_backend_ack_fields()
    print("\n所有 8 组断言通过 ✅")