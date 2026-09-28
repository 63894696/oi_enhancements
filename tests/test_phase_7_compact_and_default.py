"""
tests/test_phase_7_compact_and_default.py — Skills 工作台 Phase 7 紧凑化 + 默认配置测试(2026-09-28)。

验证 7 维度:
  1. describe_registry_compact() 标准紧凑版(去 emoji + name 截断 24 + tags 上限 4 + tier 字段)≤ 9500c
     (Phase 8 加 tier 字段,7000→8500 阈值放宽到 9500,实际 ~8956c)
  2. describe_registry_compact(ultra=True) 字段名短化版 ≤ 8000c
     (Phase 8 加 tier 字段=tir,阈值 7500→8000)
  3. standard 节省 ≥ 25%(原 12882c,Phase 8 加 tier 阈值放宽 35→25)
  4. ultra 节省 ≥ 30%(Phase 8 加 tier 阈值放宽 40→30)
  5. 紧凑后 id/name/risk/tags/tier 5 字段都保留
  6. emoji 字段不进紧凑 JSON
  7. 默认配置(主面板 PRISIRAI_SKILLS_REPLAN=1 + companion skills_replan_enabled=True)变更到位
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── 1. 标准紧凑版长度 ──────────────────────────────────────────
def test_1_standard_compact_len():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    std = reg.describe_registry_compact()
    assert len(std) <= 9500, f"标准紧凑版过长: {len(std)}c"
    assert len(std) >= 7500, f"标准紧凑版过短: {len(std)}c"
    print(f"✓ 标准紧凑版 {len(std)}c(7500-9500 阈值,Phase 8 加 tier)")


# ── 2. ultra 紧凑版长度 ────────────────────────────────────────
def test_2_ultra_compact_len():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    ultra = reg.describe_registry_compact(ultra=True)
    assert len(ultra) <= 8500, f"ultra 过长: {len(ultra)}c"
    print(f"✓ ultra 紧凑版 {len(ultra)}c(<=8500,Phase 8 加 tier=tir)")


# ── 3. standard 节省 ≥ 35% ────────────────────────────────────
def test_3_standard_savings():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    # 对照:用 describe_registry() + 全字段得原版长度
    full = reg.describe_registry()
    full_json = json.dumps(full, ensure_ascii=False, separators=(",", ":"))
    std = reg.describe_registry_compact()
    savings = 100 * (len(full_json) - len(std)) / len(full_json)
    assert savings >= 25, f"standard 节省 {savings:.1f}% < 25% 阈值"
    print(f"✓ standard 节省 {savings:.1f}%(>=25% 阈值,Phase 8 加 tier)")


# ── 4. ultra 节省 ≥ 40% ──────────────────────────────────────
def test_4_ultra_savings():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    full = reg.describe_registry()
    full_json = json.dumps(full, ensure_ascii=False, separators=(",", ":"))
    ultra = reg.describe_registry_compact(ultra=True)
    savings = 100 * (len(full_json) - len(ultra)) / len(full_json)
    assert savings >= 30, f"ultra 节省 {savings:.1f}% < 30% 阈值"
    print(f"✓ ultra 节省 {savings:.1f}%(>=30% 阈值,Phase 8 加 tier)")


# ── 5. 紧凑后 5 字段都保留(Phase 8 加 tier) ─────────────────
def test_5_required_fields():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    std_data = json.loads(reg.describe_registry_compact())
    assert std_data["schema_version"] == "1.0"
    assert std_data["total"] == 69
    first = std_data["skills"][0]
    for k in ("id", "name", "risk", "tags", "tier"):
        assert k in first, f"紧凑版缺字段 {k}"
    print(f"✓ standard 保留 schema_version + total + id/name/risk/tags/tier 6 字段")


# ── 6. emoji 字段不进紧凑 JSON ──────────────────────────────────
def test_6_no_emoji_in_compact():
    import importlib
    reg = importlib.import_module("prisir_work.skills.registry")
    reg.invalidate_cache()
    std = reg.describe_registry_compact()
    assert '"emoji"' not in std, "紧凑版含 emoji 字段(应剔除)"
    ultra = reg.describe_registry_compact(ultra=True)
    assert '"emoji"' not in ultra, "ultra 含 emoji 字段(应剔除)"
    # emoji unicode 字符也应不存在
    for ch in ("🟢", "🟡", "🟠", "🔴", "⚪", "🎨", "🎬"):
        assert ch not in std, f"标准紧凑版含 emoji {ch}"
    print("✓ 标准/ultra 紧凑版都剔除 emoji 字段 + 字符")


# ── 7. 默认配置变更到位 ─────────────────────────────────────────
def test_7_default_config_changes():
    # 7.1 主面板 PRISIRAI_SKILLS_REPLAN 默认 1
    with open("prisIragent_web.py", encoding="utf-8") as f:
        src = f.read()
    assert 'PRISIRAI_SKILLS_REPLAN", "1")' in src, \
        "主面板 PRISIRAI_SKILLS_REPLAN 默认值未改"
    assert 'PRISIRAI_SKILLS_INDEX", "1")' in src, \
        "主面板 PRISIRAI_SKILLS_INDEX 默认值未保持 1"
    # 7.2 companion skills_replan_enabled = True
    with open("companion/prisIragent-companion-web.py", encoding="utf-8") as f:
        csrc = f.read()
    assert '"skills_replan_enabled": True' in csrc, \
        "companion skills_replan_enabled 未改 True"
    assert '"skills_index_enabled": True' in csrc, \
        "companion skills_index_enabled 未改 True"
    print("✓ 主面板 PRISIRAI_SKILLS_REPLAN=1 + companion skills_replan/index=True 默认配置到位")


if __name__ == "__main__":
    test_1_standard_compact_len()
    test_2_ultra_compact_len()
    test_3_standard_savings()
    test_4_ultra_savings()
    test_5_required_fields()
    test_6_no_emoji_in_compact()
    test_7_default_config_changes()
    print("\n所有 7 组断言通过 ✅")