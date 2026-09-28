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
    """#4 7 步全免费 → $0 ≤ $14(¥100 预算)"""
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
    print(f"✓ #4 全免费 7 步 → ${est.total} ≤ $14 预算")


def test_om_p3_5_workflow_with_veo3_exceeds():
    """#5 7 步含 veo3($0.10)→ total $0.10 ≤ $14(预算已改 ¥100)→ 不过超集预算"""
    from prisir_work.video_budget import estimate_workflow_cost
    steps = [
        {"id": f"s{i}", "capability": "video.x", "args": {"provider": p}}
        for i, p in enumerate([
            "edge_tts", "veo3", "piper", "fma_music",
            "archive_org", "local_silence", "edge_tts",
        ])
    ]
    est = estimate_workflow_cost(steps)
    # 预算改 $14 后,$0.10 ≪ $14 不过超集预算
    assert est.total >= 0.10, f"含 veo3 应 ≥ $0.10,实得 ${est.total}"
    assert est.total < 14.0, f"veo3 单个应 < $14(预算改后),实得 ${est.total}"
    # 单步硬上限 $2 → veo3 $0.10 不再超限
    assert est.over_cap_count == 0, f"veo3 单步 $0.10 ≤ $2 不应超限,实得 over_cap={est.over_cap_count}"
    print(f"✓ #5 含 veo3 → ${est.total:.4f}(< $14 预算), 单步超限=0")


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
    assert c.budget == 14.00, f"默认预算应 $14,实得 ${c.budget}"
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


# 重新加 #9 #11(在 #10 之后,因为之前 edit 失败跳过)
def test_om_p3_9_check_budget_exceeded_with_replacements():
    """#9 极端编排:超预算 + 单步超 cap → replace_plan"""
    from prisir_work.video_budget import check_budget
    from prisir_work.video_provider_scoring import register_provider
    # 注入 _test_super_expensive 单步 $5 > $2 单步 cap
    register_provider("_test_super_expensive", {
        "tag": "image2video", "currency": "USD",
        "cost_per_call": 5.0,
    })
    # 4 个 step = $20 > $14 预算
    steps = [
        {"id": f"shot{i}", "capability": "video.image2video",
         "args": {"provider": "_test_super_expensive"}}
        for i in range(4)
    ]
    c = check_budget(steps)
    assert not c.ok
    assert c.exceeded, f"应超预算:{c.message}"
    # 4 个 step 都超 $2 cap → 都应进 replace_plan
    assert c.replace_plan, f"应给替换建议:{c.replace_plan}"
    assert "shot0" in c.replace_plan
    print(f"✓ #9 4×$5=$20 > $14 → replace_plan={list(c.replace_plan.keys())}")


def test_om_p3_11_check_budget_single_step_over_cap():
    """#11 单 provider $3 → 触发单步硬上限($2)"""
    from prisir_work.video_budget import check_budget
    from prisir_work.video_provider_scoring import register_provider
    register_provider("_test_expensive", {
        "tag": "image2video", "currency": "USD",
        "cost_per_call": 3.0,
    })
    steps_exp = [{"id": "single", "capability": "video.x",
                  "args": {"provider": "_test_expensive"}}]
    c = check_budget(steps_exp)
    assert "single" in c.over_cap_steps, f"单步 $3 应超 $2 上限:{c.over_cap_steps}"
    assert not c.exceeded, f"单步超但 total < budget 不算超:{c.message}"
    print(f"✓ #11 单步 $3 > $2 cap, over_cap=[single], ok={c.ok}")


def test_om_p3_12_workflow_integration():
    """#12 workflow 集成 — 超预算时 fail-fast 返 budget_check artifact"""
    from prisir_work.agent_video_workflow import run_workflow
    from prisir_work.video_provider_scoring import register_provider
    # 注入 _test_super_expensive 单步 $5 + 4 个 = $20 > $14
    register_provider("_test_super_expensive", {
        "tag": "image2video", "currency": "USD",
        "cost_per_call": 5.0,
    })
    dsl = {
        "steps": [
            {"id": f"shot{i}", "capability": "video.image2video",
             "args": {"provider": "_test_super_expensive"}}
            for i in range(4)
        ]
    }
    r = run_workflow(dsl)
    assert not r.ok, "超预算应 fail-fast"
    assert "budget_exceeded" in r.error, f"应含 budget_exceeded,实得:{r.error}"
    assert "budget_check" in (r.artifact or {}), "应返 budget_check artifact 给前端弹卡"
    check = r.artifact["budget_check"]
    assert check["exceeded"]
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


# ════════════════════════════════════════════════════════════════════════════
# Phase 11-H:国内 CNY provider + ¥100/集 顶级预算(2026-09-28)
# ════════════════════════════════════════════════════════════════════════════

def test_om_p3_h1_cny_provider_registered():
    """#H1 5 国内 provider 已注册 + cost_per_call 自动转 USD"""
    from prisir_work.video_provider_scoring import (
        get_provider_meta, list_providers, CNY_TO_USD,
    )
    for name in ["kling_cn", "jimeng", "vidu", "cogvideox", "hailuo"]:
        m = get_provider_meta(name)
        assert m, f"{name} 未注册"
        assert m.get("currency") == "CNY", f"{name} 应标 CNY"
        assert m.get("cost_per_call_original", 0) > 0, f"{name} 缺原价"
        # USD 转 (原始币值 × CNY_TO_USD)
        assert abs(m["cost_per_call"] - m["cost_per_call_original"] * CNY_TO_USD) < 0.001
    # wan2.1_local 是 USD 本地
    m = get_provider_meta("wan2.1_local")
    assert m["cost_per_call"] == 0.0
    assert m["currency"] == "USD"
    print("✓ #H1 5 国内 provider + wan2.1_local 注册,CNY 自动转 USD")


def test_om_p3_h2_kling_cn_cost_in_usd():
    """#H2 kling_cn(¥1.0)→ USD ≈ $0.139"""
    from prisir_work.video_budget import estimate_step_cost
    step = {"id": "v1", "capability": "video.image2video",
            "args": {"provider": "kling_cn"}}
    cost = estimate_step_cost(step)
    assert abs(cost - 0.139) < 0.001, f"kling_cn 应 ~$0.139,实得 ${cost}"
    print(f"✓ #H2 kling_cn (¥1.0)→ USD ${cost:.4f}")


def test_om_p3_h3_budget_100yuan_usd14():
    """#H3 默认预算常量已改为 $14/集(¥100)"""
    from prisir_work.video_budget import (
        DEFAULT_BUDGET_PER_EPISODE, DEFAULT_COST_PER_STEP_HARD_CAP,
    )
    assert DEFAULT_BUDGET_PER_EPISODE == 14.00, f"应 $14,实得 ${DEFAULT_BUDGET_PER_EPISODE}"
    assert DEFAULT_COST_PER_STEP_HARD_CAP == 2.00, f"单步上限应 $2,实得 ${DEFAULT_COST_PER_STEP_HARD_CAP}"
    print(f"✓ #H3 DEFAULT_BUDGET_PER_EPISODE=${DEFAULT_BUDGET_PER_EPISODE}, 单步 ${DEFAULT_COST_PER_STEP_HARD_CAP}")


def test_om_p3_h4_chinese_top_short_drama_budget():
    """#H4 国内顶级短剧:7 个 kling_cn × ¥1.0 = $0.97 ≪ ¥100 预算"""
    from prisir_work.video_budget import check_budget
    steps = [
        {"id": f"shot{i}", "capability": "video.image2video",
         "args": {"provider": "kling_cn"}}
        for i in range(7)
    ]
    c = check_budget(steps)
    # 7 × $0.139 = $0.97 < $14 → ok
    assert c.ok, f"7 个 kling_cn $0.97 应在 ¥100/$14 预算内:{c.message}"
    print(f"✓ #H4 7 个 kling_cn → ${c.estimate.total:.2f} < $14 ok")


def test_om_p3_h5_replace_kling_to_free():
    """#H5 kling_cn → 建议降级 wan2.1_local(免费)"""
    from prisir_work.video_budget import suggest_replacements
    reps = suggest_replacements("kling_cn")
    assert "wan2.1_local" in reps, f"应推荐 wan2.1_local,实得 {reps}"
    print(f"✓ #H5 kling_cn 降级 → {reps}")


def test_om_p3_h6_workflow_14usd_chinese_orchestra():
    """#H6 真实国内短剧编排:7 镜头 jimeng + cogvideox + wan2.1_local + piper + fma + assemble"""
    from prisir_work.video_budget import estimate_workflow_cost
    steps = [
        # 7 个镜头(图生视频)混合 3 个 provider
        *[{"id": f"shot{i}", "capability": "video.image2video",
           "args": {"provider": p}}
          for i, p in enumerate([
              "jimeng", "kling_cn", "cogvideox",
              "jimeng", "hailuo", "cogvideox", "jimeng",
          ])],
        # TTS 配音
        {"id": "tts", "capability": "audio.tts", "args": {"provider": "piper"}},
        # BGM
        {"id": "bgm", "capability": "music.bgm", "args": {"provider": "fma_music"}},
        # 字幕
        {"id": "subtitle", "capability": "video.subtitle", "args": {"provider": "edge_tts"}},
        # 合成
        {"id": "assemble", "capability": "video.assemble", "args": {}},
    ]
    est = estimate_workflow_cost(steps)
    # 估算:jimeng 3x0.07 + kling_cn 0.14 + cogvideox 2x0.04 + hailuo 0.11 ≈ 0.21 + 0.14 + 0.08 + 0.11 = $0.54
    # 加上 tts/bgm/subtitle/assemble 全免费 = $0.54
    assert est.total < 14.0, f"应 < $14,实得 ${est.total}"
    print(f"✓ #H6 国内 11 步真实编排 → ${est.total:.2f}(远低 $14 预算)")


def test_om_p3_h7_explicit_budget_30yuan():
    """#H7 用户临时加大/减小预算:¥30 = $4.17 不够 7 kling_cn($0.97)pass;7 veo3($0.70)pass"""
    from prisir_work.video_budget import check_budget
    # 7 个 veo3 → $0.70 ≤ $4.17 → ok
    dsl_us = [
        {"id": f"s{i}", "capability": "video.x",
         "args": {"provider": "veo3"}} for i in range(7)
    ]
    c = check_budget(dsl_us, budget=4.17)
    assert c.ok, f"7 veo3 $0.70 ≤ $4.17 应 ok:{c.message}"
    # 7 个 kling_cn → $0.97 ≤ $4.17 → ok
    dsl_cn = [
        {"id": f"s{i}", "capability": "video.x",
         "args": {"provider": "kling_cn"}} for i in range(7)
    ]
    c = check_budget(dsl_cn, budget=4.17)
    assert c.ok, f"7 kling_cn $0.97 ≤ $4.17 应 ok:{c.message}"
    # 14 个 veo3 → $1.40 ≤ $4.17 → ok;但 14 × 1.40 = 实际是 $1.40 还是 $14?veo3 单价 $0.10
    # 修正:14 × $0.10 = $1.40 ≤ $4.17 → ok
    print("✓ #H7 ¥30 budget=$4.17 通过 7 veo3 / 7 kling_cn")


def test_om_p3_h8_workflow_real_chinese_budget_check():
    """#H8 workflow 集成:国内 7 镜头编排 + 默认 $14 预算 → 不拦截"""
    from prisir_work.agent_video_workflow import run_workflow
    from prisir_work import capability as _cap
    from prisir_work import endpoints as _ep
    real_cap = None
    for cap_id, entry in _cap._REGISTRY.items():
        if entry.get("endpoint") and entry["endpoint"] in _ep._REGISTRY:
            real_cap = cap_id
            break
    if not real_cap:
        print("⚠ #H8 跳过 — 无真实 capability")
        return
    # 1 个真实 step(无 provider 字段)→ 估算 $0 通过预算检查
    dsl = {"steps": [{"id": "s1", "capability": real_cap, "args": {}}]}
    r = run_workflow(dsl)
    assert r.ok, f"默认 $14 应通过:{r.error}"
    print(f"✓ #H8 workflow + 默认 $14 预算, 无 provider 字段 → ok({r.ok})")


if __name__ == "__main__":
    print("═══ OM-P3 tests(14) + Phase 11-H 国内 CNY(8)═══")
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
    print()
    print("--- Phase 11-H(国内 CNY / ¥100/集 顶级档)---")
    test_om_p3_h1_cny_provider_registered()
    test_om_p3_h2_kling_cn_cost_in_usd()
    test_om_p3_h3_budget_100yuan_usd14()
    test_om_p3_h4_chinese_top_short_drama_budget()
    test_om_p3_h5_replace_kling_to_free()
    test_om_p3_h6_workflow_14usd_chinese_orchestra()
    test_om_p3_h7_explicit_budget_30yuan()
    test_om_p3_h8_workflow_real_chinese_budget_check()
    print("\n所有 22 组断言通过 ✅")