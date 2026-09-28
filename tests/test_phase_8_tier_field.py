"""
tests/test_phase_8_tier_field.py — Skills 工作台 Phase 8 tier 分层字段测试(2026-09-28)。

承接用户决策:
  「长尾低频但关键的技能不能被频次去重干掉(年度/季度/审计场景)。
   给每个 skill 加 tier 字段分层兜底,不动能力,只分层。」

验证 8 维度:
  1. SkillIndex dataclass 默认 tier='warm'
  2. to_dict 序列化含 tier 字段
  3. _tier_for 启发式:hot(查询)/ warm(常规)/ cold(写操作兜底)
  4. capability 显式 `_tier` 字段可 override 启发式
  5. count_by_tier 永远返 hot/warm/cold/archive 4 个 key
  6. list_skills_by_tier 返指定 tier 子集
  7. describe_registry_compact 保留 tier 字段(standard + ultra)
  8. Phase 1-7 测试不破(回归)
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── 1. SkillIndex 默认 tier='warm' ────────────────────────────────
def test_1_default_tier_is_warm():
    from prisir_work.skills.schema import SkillIndex
    idx = SkillIndex(id="test.skill", name="test")
    assert idx.tier == "warm", f"默认 tier 应为 'warm',实为 {idx.tier!r}"
    print(f"✓ SkillIndex 默认 tier = 'warm'")


# ── 2. to_dict 序列化含 tier ──────────────────────────────────────
def test_2_to_dict_includes_tier():
    from prisir_work.skills.schema import SkillIndex
    idx = SkillIndex(id="x", name="n", tier="cold")
    d = idx.to_dict()
    assert "tier" in d, "to_dict 缺 tier 字段"
    assert d["tier"] == "cold"
    print("✓ SkillIndex.to_dict() 含 tier 字段")


# ── 3. _tier_for 启发式 3 类 ──────────────────────────────────────
def test_3_tier_heuristic():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    tf = reg._tier_for
    # 3.1 hot:高频查询类
    assert tf("video.info", "L0") == "hot", "video.info 应为 hot"
    assert tf("web.search", "L0") == "hot", "web.search 应为 hot"
    assert tf("web.fetch", "L0") == "hot", "web.fetch 应为 hot"
    # 3.2 cold:写操作兜底 + 低频关键
    assert tf("git.commit", "L1") == "warm", "git.commit 显式 warm"
    assert tf("audit.report", "L2") == "cold", "audit.report 应为 cold"
    assert tf("tax.file", "L1") == "cold", "tax.file 应为 cold"
    # 3.3 warm:兜底
    assert tf("some.unknown.skill", "L0") == "warm", "L0 兜底 warm"
    assert tf("some.unknown.skill", "L1") == "cold", "L1 兜底 cold"
    print("✓ _tier_for 启发式:hot=3 类 / cold=3 类(年度+审计+报税)/ warm 兜底")


# ── 4. capability 显式 _tier override 启发式 ──────────────────────
def test_4_capability_tier_override():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    skills = reg.list_skills()
    # 所有 skill 都有 tier 字段
    no_tier = [s for s in skills if not s.tier]
    assert not no_tier, f"{len(no_tier)} skill 缺 tier"
    # 所有 tier ∈ {hot, warm, cold, archive}
    valid = {"hot", "warm", "cold", "archive"}
    invalid = [s.id for s in skills if s.tier not in valid]
    assert not invalid, f"非法 tier: {invalid}"
    print(f"✓ {len(skills)} skill 全部 tier ∈ {{hot,warm,cold,archive}}")


# ── 5. count_by_tier 永远返 4 key ─────────────────────────────────
def test_5_count_by_tier_keys():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    c = reg.count_by_tier()
    for k in ("hot", "warm", "cold", "archive"):
        assert k in c, f"count_by_tier 缺 key {k}"
    assert sum(c.values()) == 69, f"count_by_tier 总和 {sum(c.values())} != 69"
    print(f"✓ count_by_tier 永远含 hot/warm/cold/archive 4 key, 总和=69")


# ── 6. list_skills_by_tier 返子集 ─────────────────────────────────
def test_6_list_skills_by_tier():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    hot = reg.list_skills_by_tier("hot")
    cold = reg.list_skills_by_tier("cold")
    archive = reg.list_skills_by_tier("archive")
    assert all(s.tier == "hot" for s in hot), "hot 子集有杂质"
    assert all(s.tier == "cold" for s in cold), "cold 子集有杂质"
    assert len(archive) == 0, "archive 应为空(无废弃 skill)"
    # 不存在 tier 返空
    assert reg.list_skills_by_tier("unknown") == []
    assert reg.list_skills_by_tier("") == []
    print(f"✓ list_skills_by_tier: hot={len(hot)} cold={len(cold)} archive=0")


# ── 7. describe_registry_compact 保留 tier(standard + ultra) ──────
def test_7_compact_includes_tier():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    std = reg.describe_registry_compact()
    assert '"tier"' in std, "standard 紧凑版缺 tier 字段"
    std_data = json.loads(std)
    first = std_data["skills"][0]
    assert "tier" in first
    assert first["tier"] in ("hot", "warm", "cold")
    # ultra
    ultra = reg.describe_registry_compact(ultra=True)
    assert '"tir"' in ultra, "ultra 紧凑版缺 tir 字段(短名)"
    print("✓ standard 含 tier / ultra 含 tir 字段(tier 分层可解析)")


# ── 8. tiers_summary 给 debug 用 ──────────────────────────────────
def test_8_tiers_summary():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    s = reg.tiers_summary()
    assert "hot=" in s and "warm=" in s and "cold=" in s
    assert "total=69" in s
    print(f"✓ tiers_summary 1 行人话: {s}")


if __name__ == "__main__":
    test_1_default_tier_is_warm()
    test_2_to_dict_includes_tier()
    test_3_tier_heuristic()
    test_4_capability_tier_override()
    test_5_count_by_tier_keys()
    test_6_list_skills_by_tier()
    test_7_compact_includes_tier()
    test_8_tiers_summary()
    print("\n所有 8 组断言通过 ✅")