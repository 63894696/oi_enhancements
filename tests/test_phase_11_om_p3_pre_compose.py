"""
tests/test_phase_11_om_p3_pre_compose.py — Phase 11 OM-P3 测试(2026-09-28)。

承接 [[prisIr-phase-10-om-p2-scoring]] + 用户「免费优先,单集 ≤ $0.10」拍板。

OM-P3:Pre-compose 校验 — 预算估算 + 超限 fail-fast + 替换建议。

## 测试矩阵(10 项)
  1. estimate_step_cost — 免费 provider(cost=0)返 $0
  2. estimate_step_cost — 付费 provider(kling $0.05)返 $0.05
  3. estimate_step_cost — 未知 provider → $0(保守)
  4. estimate_workflow_cost — 7 步全免费 → $0 ≤ $0.10
  5. estimate_workflow_cost — 7 步含 veo3 → 超 $0.10
  6. suggest_replacements — kling → [local_wan](quality 降序)
  7. suggest_replacements — piper(免费)→ []
  8. check_budget — 全免费 → ok=True
  9. check_budget — 含 veo3 → ok=False, exceeded=True, replace_plan 含 local_wan
 10. check_budget — 空 steps → ok=False, exceeded=False
 11. check_budget — 单步超硬上限(单 provider ≥ $0.015)→ over_cap_steps 非空
 12. workflow 集成 — run_workflow 检测超预算 → fail-fast 返 need_confirm
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ════════════════════════════════════════════════════════════════════════════
# OM-P3 tests — Pre-compose 预算校验
# ════════════════════════════════════════════════════════════════════════════

def test_om_p3_1_estimate_free_provider():
    """#1 免费 provider(piper)→ $0"""
    from prisir_work.video_budget import estimate_step_cost
    step = {"id": "tts1", "capability": "audio.tts", "args": {"provider": "piper"}}
    assert estimate_step_cost(step) == 0.0
    print("✓ #1 estimate_step_cost 免费 provider → $0")


def test_om_p3_2_estimate_paid_provider():
    """#2 付费 provider(kling $0.05)→ $0.05"""
    from prisir_work.video_budget import estimate_step_cost
    step = {"id": "v1", "capability": "video.image2video", "args": {"provider": "kling"}}
    cost = estimate_step_cost(step)
    assert abs(cost - 0.05) < 0.001, f"kling 应返 $0.05,实得 {cost}"
    print(f"✓ #2 estimate_step_cost kling → ${cost}")


def test_om_p3_3_estimate_unknown_provider():
    """#3 未知 provider → $0(保守,不算错)"""
    from prisir_work.video_budget import estimate_step_cost
    step = {"id": "x", "capability": "video.x", "args": {"provider": "nonexistent_xyz"}}
    assert estimate_step_cost(step) == 0.0
    print("✓ #3 estimate_step_cost 未知 provider → $0(保守)")


def test_om_p3_4_workflow_all_free():
    """#4 7 步全免费 → $0 ≤ $0.10"""
    from prisir_work.video_budget import estimate_workflow_cost
    steps = [
        {"id": f"s{i}", "capability": "video.x",
         "args": {"provider": p}}
        for i, p in enumerate([
            "edge_tts",   # tts $0
            "local_wan",  # i2v $0
            "piper",      # tts $0
            "fma_music",  # bgm $0
            "archive_org",  # stock $0
            "local_silence",  # bgm fallback $0
            "edge_tts",   # tts $0
        ])
    ]
    est = estimate_workflow_cost(steps)
    assert est.total == 0.0
    assert est.steps_count == 7
    assert est.over_cap_count == 0
    print(f"✓ #4 全免费 7 步 → ${est.total} ≤ $0.10")


def test_om_p3_5_workflow_with_veo3_exceeds():
    """#5 7 步含 veo3($0.10)→ 超 $0.10"""
    from prisir_work.video_budget import estimate_workflow_cost
    steps = [
        {"id": f"s{i}", "capability": "video.x", "args": {"provider": p}}
        for i, p in enumerate([
            "edge_tts", "veo3", "piper", "fma_music",
            "archive_org", "local_silence", "edge_tts",
        ])
    ]
    est = estimate_workflow_cost(steps)
    assert est.total >= 0.10, f"含 veo3 应 ≥ $0.10,实得 ${est.total}"
    assert est.over_cap_count >= 1, "veo3 单步超硬上限"
    print(f"✓ #5 含 veo3 → ${est.total:.4f}, over_cap={est.over_cap_count}")


def test_om_p3_6_suggest_replacements_kling():
    """#6 kling → [local_wan](quality 降序,免费替代)"""
    from prisir_work.video_budget import suggest_replacements
    reps = suggest_replacements("kling")
    # 应有 local_wan(免费)
    assert "local_wan" in reps, f"应有 local_wan,实得 {reps}"
    # 验证全部 cost>=1.0(免费)
    from prisir_work.video_provider_scoring import get_provider_meta
    for r in reps:
        m = get_provider_meta(r)
        assert m.get("cost", 0) >= 1.0, f"{r} 不是免费 provider"
    print(f"✓ #6 kling 替代 → {reps}(全免费)")


def test_om_p3_7_suggest_replacements_already_free():
    """#7 piper(免费)→ [] 无替代建议"""
    from prisir_work.video_budget import suggest_replacements
    reps = suggest_replacements("piper")
    assert reps == [], f"免费 provider 不应有替代,实得 {reps}"
    print("✓ #7 免费 provider → [] 无替代")


def test_om_p3_8_check_budget_all_free_ok():
    """#8 全免费 → ok=True"""
    from prisir_work.video_budget import check_budget
    steps = [
        {"id": f"s{i}", "capability": "video.x",
         "args": {"provider": p}}
        for i, p in enumerate([
            "edge_tts", "local_wan", "piper", "fma_music",
            "archive_org", "local_silence", "edge_tts",
        ])
    ]
    c = check_budget(steps)
    assert c.ok, f"全免费应在预算内:{c.message}"
    assert not c.exceeded
    assert c.budget == 0.10
    print(f"✓ #8 全免费 → ok=True ({c.message})")


def test_om_p3_9_check_budget_exceeded_with_replacements():
    """#9 含 veo3 → ok=False, exceeded=True, replace_plan 给出 local_wan"""
    from prisir_work.video_budget import check_budget
    steps = [
        {"id": "shot1", "capability": "video.image2video",
         "args": {"provider": "veo3"}},
        {"id": "shot2", "capability": "video.image2video",
         "args": {"provider": "veo3"}},
        {"id": "shot3", "capability": "video.image2video",
         "args": {"provider": "kling"}},
        {"id": "shot4", "capability": "video.image2video",
         "args": {"provider": "seedance"}},
        {"id": "shot5", "capability": "audio.tts",
         "args": {"provider": "edge_tts"}},
        {"id": "shot6", "capability": "music.bgm",
         "args": {"provider": "fma_music"}},
        {"id": "shot7", "capability": "video.assemble", "args": {}},
    ]
    c = check_budget(steps)
    assert not c.ok
    assert c.exceeded, f"应超预算:{c.message}"
    # replace_plan 应给 veo3/kling/seedance 替代为 local_wan
    assert "shot1" in c.replace_plan, f"shot1(veo3)应有替代:{c.replace_plan}"
    assert "local_wan" in c.replace_plan["shot1"]
    # shot5(tts免费)和 shot7(assemble)不应有 replace_plan
    assert "shot5" not in c.replace_plan
    print(f"✓ #9 超预算 → ok=False, replace_plan={c.replace_plan}")


def test_om_p3_10_check_budget_empty():
    """#10 空 steps → ok=False(校验失败)"""
    from prisir_work.video_budget import check_budget
    c = check_budget([])
    assert not c.ok
    assert not c.exceeded, "空 steps 不算超预算"
    assert "empty" in c.message
    print("✓ #10 空 steps → ok=False, message='empty workflow steps'")


def test_om_p3_11_check_budget_single_step_over_cap():
    """#11 单 provider $0.10 → 触发单步硬上限($0.014)"""
    from prisir_work.video_budget import check_budget
    # 1 个 veo3 step 单步就 $0.10 > $0.014(单步硬上限)
    steps = [
        {"id": "single", "capability": "video.image2video",
         "args": {"provider": "veo3"}},
    ]
    c = check_budget(steps)
    assert "single" in c.over_cap_steps, f"单 veo3 应超单步上限:{c.over_cap_steps}"
    # 单步超 $0.014 但 total $0.10 = budget,exceeded 看总成本
    assert not c.exceeded, f"单步超但 total = budget 不算超:{c.message}"
    print(f"✓ #11 单 veo3 over_cap 但 total = budget, ok={c.ok}")


def test_om_p3_12_workflow_integration():
    """#12 workflow 集成 — 超预算时 fail-fast 返 budget_check artifact"""
    from prisir_work.agent_video_workflow import run_workflow
    # 7 个 veo3 step → 单集 $0.70 超 $0.10 默认预算
    dsl = {
        "steps": [
            {"id": f"shot{i}", "capability": "video.image2video",
             "args": {"provider": "veo3"}}
            for i in range(7)
        ]
    }
    r = run_workflow(dsl)
    assert not r.ok, "超预算应 fail-fast"
    assert "budget_exceeded" in r.error, f"应含 budget_exceeded,实得:{r.error}"
    assert "budget_check" in (r.artifact or {}), "应返 budget_check artifact 给前端弹卡"
    check = r.artifact["budget_check"]
    assert check["exceeded"]
    # 7 个 veo3 都应可降级到 local_wan
    assert "shot0" in check["replace_plan"]
    print(f"✓ #12 workflow fail-fast 返 budget_exceeded + replace_plan ({len(check['replace_plan'])} step 可降级)")


def test_om_p3_13_workflow_integration_within_budget():
    """#13 全免费 → 不被预算拦截,正常跑"""
    from prisir_work.agent_video_workflow import run_workflow
    # 找真实 capability
    from prisir_work import capability as _cap
    from prisir_work import endpoints as _ep
    real_cap = None
    for cap_id, entry in _cap._REGISTRY.items():
        if entry.get("endpoint") and entry["endpoint"] in _ep._REGISTRY:
            real_cap = cap_id
            break
    if not real_cap:
        print("⚠ #13 跳过 — 无真实 capability")
        return
    # 不传 provider(默认走 capability handler 内部),全免费 budget 默认
    dsl = {"steps": [{"id": "s1", "capability": real_cap, "args": {}}]}
    r = run_workflow(dsl)
    assert r.ok, f"全免费不应被拦截:{r.error}"
    print("✓ #13 全免费 workflow 不被预算拦截,正常跑通")


def test_om_p3_14_workflow_integration_pre_compose_disabled():
    """#14 pre_compose_check=False → 跳过预算拦截,旧行为不变"""
    from prisir_work.agent_video_workflow import run_workflow
    # 7 个 veo3 + 关闭预算拦截
    dsl = {
        "steps": [
            {"id": f"shot{i}", "capability": "video.x",
             "args": {"provider": "veo3"}}
            for i in range(7)
        ],
        "pre_compose_check": False,
    }
    # 不会真跑(因为 video.x capability 不存在),但应该跳过预算拦截
    # 跑到 parse_dependencies 或 capability 找不到时报错,但不报 budget_exceeded
    r = run_workflow(dsl)
    if r.ok:
        print("✓ #14 pre_compose_check=False 跳过预算拦截")
        return
    assert "budget_exceeded" not in r.error, f"应跳过预算,实得:{r.error}"
    print(f"✓ #14 pre_compose_check=False 跳过预算拦截(error='{r.error[:50]}...')")


if __name__ == "__main__":
    print("═══ OM-P3 tests(14)═══")
    test_om_p3_1_estimate_free_provider()
    test_om_p3_2_estimate_paid_provider()
    test_om_p3_3_estimate_unknown_provider()
    test_om_p3_4_workflow_all_free()
    test_om_p3_5_workflow_with_veo3_exceeds()
    test_om_p3_6_suggest_replacements_kling()
    test_om_p3_7_suggest_replacements_already_free()
    test_om_p3_8_check_budget_all_free_ok()
    test_om_p3_9_check_budget_exceeded_with_replacements()
    test_om_p3_10_check_budget_empty()
    test_om_p3_11_check_budget_single_step_over_cap()
    test_om_p3_12_workflow_integration()
    test_om_p3_13_workflow_integration_within_budget()
    test_om_p3_14_workflow_integration_pre_compose_disabled()
    print("\n所有 14 组断言通过 ✅")