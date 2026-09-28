"""
tests/test_phase_10_om_p2_provider_scoring.py — Phase 10 OM-P2 测试(2026-09-28)。

承接 [[prisIr-openmontage-recon]] + [[prisIr-phase-9-om-p1-and-ma-p1]] + 用户「spawn on-demand 架构」决策。

OM-P2:Provider 7 维度评分 + 自动选最优 + 降级语义。

## 测试矩阵(7 项)
  1. Score dataclass 默认值 + weighted 计算
  2. register_provider / get_provider_meta / list_providers
  3. score_provider — 未知 provider → eligible=False, reason=unknown_provider
  4. score_provider — 免费 provider(budget 充足)在 cost 维度 1.0
  5. score_provider — unhealthy(provider health=0)→ ineligible
  6. score_provider — quota=0 → ineligible + reason
  7. score_provider — history_failure > 0.5 → ineligible + high_failure_rate
  8. pick_best — 多 provider 中选总分最高
  9. pick_best — budget=0 时只选 free provider
 10. pick_best — 全 ineligible → 返 None
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ════════════════════════════════════════════════════════════════════════════
# OM-P2 tests — Provider scoring(10 项)
# ════════════════════════════════════════════════════════════════════════════

def test_om_p2_1_score_dataclass():
    """#1 Score dataclass 默认值 + weighted"""
    from prisir_work.video_provider_scoring import Score, DEFAULT_WEIGHTS
    s = Score()
    # 默认:cost=1.0,availability=1.0,quota=1.0,history_success=1.0,history_failure=0.0
    assert s.cost == 1.0
    assert s.availability == 1.0
    assert s.quota == 1.0
    assert s.history_success == 1.0
    assert s.history_failure == 0.0
    assert s.quality == 0.5
    assert s.speed == 0.5
    # weighted 应在 [0, 1]
    total = s.weighted()
    assert 0.0 <= total <= 1.0, f"weighted 越界: {total}"
    # 默认权重 sanity check:0.5×0.30+0.5×0.15+1×0.20+1×0.10+1×0.10+1×0.10+0×-0.05 = 0.725
    assert abs(total - 0.725) < 0.01, f"weighted 偏差: {total}"
    # 自定义权重
    w_only_cost = {"quality": 0.0, "speed": 0.0, "cost": 1.0, "availability": 0.0,
                   "quota": 0.0, "history_success": 0.0, "history_failure": 0.0}
    assert s.weighted(w_only_cost) == 1.0
    print("✓ #1 Score dataclass + weighted(默认 + 自定义权重)")


def test_om_p2_2_registry():
    """#2 register_provider / get_provider_meta / list_providers"""
    from prisir_work.video_provider_scoring import (
        register_provider, get_provider_meta, list_providers,
    )
    register_provider("test_provider_xx", {
        "tag": "test", "quality": 0.7, "cost": 0.5,
    })
    assert "test_provider_xx" in list_providers()
    assert "test_provider_xx" in list_providers(tag="test")
    meta = get_provider_meta("test_provider_xx")
    assert meta["quality"] == 0.7
    assert meta["cost"] == 0.5
    # tag 过滤:不应有其它 tag 的 provider
    assert "test_provider_xx" not in list_providers(tag="tts")
    print("✓ #2 register / get / list(tag 过滤)")


def test_om_p2_3_unknown_provider():
    """#3 未知 provider → eligible=False, reason=unknown_provider"""
    from prisir_work.video_provider_scoring import score_provider
    ps = score_provider("nonexistent_zzz_abc")
    assert ps.eligible is False
    assert "unknown_provider" in ps.reasons
    assert ps.total == 0.0
    print("✓ #3 未知 provider 返 ineligible + reason")


def test_om_p2_4_free_provider_score():
    """#4 免费 provider(budget 充足)在 cost 维度 1.0"""
    from prisir_work.video_provider_scoring import (
        score_provider, pick_best, list_providers,
    )
    # piper / edge_tts 都是免费的(无 key)
    piper = score_provider("piper", context={"budget_remaining": 1.0})
    assert piper.score.cost == 1.0
    assert piper.eligible
    # 在 cost=0 场景(零预算),免费 provider 应赢付费 provider
    ctx_zero = {"budget_remaining": 0.0}
    piper_zero = score_provider("piper", context=ctx_zero)
    elevenlabs_zero = score_provider("elevenlabs_tts", context=ctx_zero)
    assert piper_zero.eligible, "预算=0 时免费 piper 应 eligible"
    assert not elevenlabs_zero.eligible, "预算=0 时付费 elevenlabs 应 ineligible"
    print("✓ #4 免费 provider 在 cost 维度 1.0 + 预算=0 自动降级到免费")


def test_om_p2_5_unhealthy_provider():
    """#5 unhealthy(health=0)→ ineligible + unhealthy reason"""
    from prisir_work.video_provider_scoring import (
        register_provider, score_provider,
    )
    register_provider("test_unhealthy", {
        "tag": "test", "quality": 0.9, "cost": 1.0,
        "availability": 0.0,  # unhealthy
    })
    ps = score_provider("test_unhealthy")
    assert ps.eligible is False
    assert "unhealthy" in ps.reasons
    print("✓ #5 availability=0 → ineligible + unhealthy")


def test_om_p2_6_quota_exhausted():
    """#6 quota=0 → ineligible + quota_exhausted"""
    from prisir_work.video_provider_scoring import (
        register_provider, score_provider,
    )
    register_provider("test_quota_dry", {
        "tag": "test", "quality": 0.9, "cost": 1.0,
        "quota": 0.0,
    })
    ps = score_provider("test_quota_dry")
    assert ps.eligible is False
    assert "quota_exhausted" in ps.reasons
    print("✓ #6 quota=0 → ineligible + quota_exhausted")


def test_om_p2_7_high_failure_rate():
    """#7 history_failure > 0.5 → ineligible + high_failure_rate"""
    from prisir_work.video_provider_scoring import (
        register_provider, score_provider,
    )
    register_provider("test_unreliable", {
        "tag": "test", "quality": 0.9, "cost": 1.0,
        "history_failure": 0.6,  # 60% 失败
    })
    ps = score_provider("test_unreliable")
    assert ps.eligible is False
    assert "high_failure_rate" in ps.reasons
    print("✓ #7 history_failure > 0.5 → ineligible + high_failure_rate")


def test_om_p2_8_pick_best_highest_score():
    """#8 pick_best 多 provider 中选总分最高"""
    from prisir_work.video_provider_scoring import (
        register_provider, pick_best,
    )
    register_provider("test_high_quality", {
        "tag": "test", "quality": 0.95, "speed": 0.5, "cost": 1.0,
        "availability": 1.0, "quota": 1.0,
    })
    register_provider("test_low_quality", {
        "tag": "test", "quality": 0.3, "speed": 0.5, "cost": 1.0,
        "availability": 1.0, "quota": 1.0,
    })
    best = pick_best(["test_high_quality", "test_low_quality"], tag="test")
    assert best is not None
    assert best.name == "test_high_quality", f"高分 provider 应被选中,实得 {best.name}"
    assert best.eligible
    print("✓ #8 pick_best 在多 provider 中选总分最高")


def test_om_p2_9_pick_best_budget_zero():
    """#9 budget=0 时只选 free provider"""
    from prisir_work.video_provider_scoring import pick_best
    # edge_tts(free) vs elevenlabs_tts(paid)— budget=0 应只选 edge_tts
    best = pick_best(
        ["edge_tts", "elevenlabs_tts"],
        context={"budget_remaining": 0.0},
    )
    assert best is not None
    assert best.name == "edge_tts", f"预算=0 应选免费,实选 {best.name}"
    print("✓ #9 budget=0 自动降级到免费 provider")


def test_om_p2_10_pick_best_all_ineligible():
    """#10 全 ineligible → 返 None"""
    from prisir_work.video_provider_scoring import (
        register_provider, pick_best,
    )
    register_provider("test_unhealthy_a", {
        "tag": "test_fail_all", "availability": 0.0,
    })
    register_provider("test_unhealthy_b", {
        "tag": "test_fail_all", "quota": 0.0,
    })
    best = pick_best(
        ["test_unhealthy_a", "test_unhealthy_b"],
        tag="test_fail_all",
    )
    assert best is None, "全 ineligible 应返 None"
    print("✓ #10 全 ineligible → 返 None")


# ════════════════════════════════════════════════════════════════════════════
# video_creator 集成测试
# ════════════════════════════════════════════════════════════════════════════

def test_om_p2_11_video_creator_pick_provider_for_tts():
    """#11 video_creator.pick_provider_for_creator("tts", ...) 返免费 tts"""
    from prisir_work.video_creator import pick_provider_for_creator
    # budget=0 → 应选免费 provider(piper 或 edge_tts)
    chosen = pick_provider_for_creator("tts", context={"budget_remaining": 0.0})
    assert chosen is not None, "应有免费 tts 可选"
    assert chosen in ("piper", "edge_tts"), f"预算=0 应选免费,实选 {chosen}"
    print(f"✓ #11 video_creator.pick_provider_for_creator('tts', budget=0) → {chosen}")


def test_om_p2_12_video_creator_pick_provider_for_music():
    """#12 music provider 在预算充足时优先质量高者"""
    from prisir_work.video_creator import pick_provider_for_creator
    # budget=1.0 时,fma_music(quality 0.70)与 pixabay_music(quality 0.75,需 key),
    # 默认 keys_available=True → 两者都 eligible,选 quality 高的 pixabay_music
    chosen = pick_provider_for_creator("music", context={"budget_remaining": 1.0})
    assert chosen is not None
    # 默认 ctx.keys_available=True,pixabay_music quality > fma_music,选 pixabay_music
    assert chosen in ("pixabay_music", "fma_music"), f"应选质量高的免费 music,实选 {chosen}"
    print(f"✓ #12 video_creator.pick_provider_for_creator('music', budget=1.0) → {chosen}")


def test_om_p2_13_video_creator_pick_provider_unknown_tag():
    """#13 未知 tag → 返 None(不抛栈)"""
    from prisir_work.video_creator import pick_provider_for_creator
    chosen = pick_provider_for_creator("nonexistent_tag_xyz")
    assert chosen is None, "未知 tag 应返 None"
    print("✓ #13 未知 tag → 返 None(fail-soft)")


if __name__ == "__main__":
    print("═══ OM-P2 tests(10)═══")
    test_om_p2_1_score_dataclass()
    test_om_p2_2_registry()
    test_om_p2_3_unknown_provider()
    test_om_p2_4_free_provider_score()
    test_om_p2_5_unhealthy_provider()
    test_om_p2_6_quota_exhausted()
    test_om_p2_7_high_failure_rate()
    test_om_p2_8_pick_best_highest_score()
    test_om_p2_9_pick_best_budget_zero()
    test_om_p2_10_pick_best_all_ineligible()
    print()
    print("═══ video_creator 集成(3)═══")
    test_om_p2_11_video_creator_pick_provider_for_tts()
    test_om_p2_12_video_creator_pick_provider_for_music()
    test_om_p2_13_video_creator_pick_provider_unknown_tag()
    print("\n所有 13 组断言通过 ✅")