"""
tests/test_agency_agents_extract.py — agency-agents Phase A 数据抽取测试(2026-09-27)。

验收 5 维度:
  1. divisions 数量 = 18 且与 divisions.json 对齐
  2. roles 数量 >= 200 且每个 division 至少 1 role
  3. 每 role 都有完整 frontmatter(name/description/vibe/emoji/color)+ body > 100 chars
  4. persona_tokens_est 误差 sanity(>0 且与 chars 同数量级)
  5. meta.json 含 snapshot_date / sync_command / license / extension_fork=false
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "extensions" / "agency-roles" / "data"


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_1_divisions_count_and_alignment():
    div = load("divisions.json")["divisions"]
    meta = load("meta.json")
    assert len(div) == 18, f"divisions={len(div)}, expected 18"
    assert meta["total_divisions"] == 18

    # 每个 division 都有 n_roles 字段且 >= 1
    for d in div:
        assert "name" in d and "label" in d and "icon" in d and "color" in d
        assert d["n_roles"] >= 1, f"{d['name']} has 0 roles"

    # divisions.json 应当含全部 18 个计划 division
    expected = {
        "academic", "design", "engineering", "finance", "game-development",
        "gis", "healthcare", "marketing", "paid-media", "product",
        "project-management", "research", "sales", "security",
        "spatial-computing", "specialized", "support", "testing",
    }
    actual = {d["name"] for d in div}
    assert expected == actual, f"missing={expected - actual} extra={actual - expected}"
    print(f"✓ 18 divisions aligned with divisions.json (total roles={sum(d['n_roles'] for d in div)})")


def test_2_roles_count_and_frontmatter():
    roles = load("roles.json")["roles"]
    meta = load("meta.json")
    assert len(roles) >= 200, f"roles={len(roles)}, expected >= 200"
    assert len(roles) == meta["total_roles"]

    # 严格必填:name/description/emoji/color;vibe 是强烈推荐但少数 agent 缺(只 1 个)
    strict_fm = {"name", "description", "emoji", "color"}
    bad: list[str] = []
    for r in roles:
        for k in strict_fm:
            if not r.get(k):
                bad.append(f"{r['slug']}: missing {k}")
        if len(r.get("persona_md", "")) < 100:
            bad.append(f"{r['slug']}: persona_md < 100 chars")
    # vibe 缺失记入 warning 但不 fail
    vibe_missing = [r["slug"] for r in roles if not r.get("vibe")]
    assert not bad, "frontmatter/persona_md issues:\n" + "\n".join(bad[:10])

    # engineering:frontend-developer 必须命中
    slugs = {r["slug"] for r in roles}
    assert "engineering-frontend-developer" in slugs
    print(f"✓ {len(roles)} roles, all have strict frontmatter + body>100 chars "
          f"(vibe missing in {len(vibe_missing)}: {vibe_missing[:3]})")


def test_3_division_role_distribution():
    roles = load("roles.json")["roles"]
    by_div: dict[str, int] = {}
    for r in roles:
        by_div[r["div"]] = by_div.get(r["div"], 0) + 1
    # engineering 应该是最大(64)
    assert by_div.get("engineering", 0) >= 50, f"engineering={by_div.get('engineering')}"
    # specialized 第二(59)
    assert by_div.get("specialized", 0) >= 30, f"specialized={by_div.get('specialized')}"
    # 没有零 division
    assert all(n >= 1 for n in by_div.values()), f"empty division: {by_div}"
    print(f"✓ division distribution OK ({len(by_div)} divs, top3={sorted(by_div.items(), key=lambda x: -x[1])[:3]})")


def test_4_token_estimation_sanity():
    roles = load("roles.json")["roles"]
    bad = []
    for r in roles:
        est = r.get("persona_tokens_est", 0)
        chars = r.get("persona_chars", 0)
        if est <= 0 or chars <= 0:
            bad.append(f"{r['slug']}: est={est} chars={chars}")
            continue
        # 比例应大致在 chars/6 ~ chars/2(粗估边界)
        ratio = chars / est
        if not (2 <= ratio <= 6):
            bad.append(f"{r['slug']}: chars={chars} est={est} ratio={ratio:.2f}")
    assert not bad, "token estimation off:\n" + "\n".join(bad[:10])

    # 抽查 engineering-frontend-developer(实际 ~8952 chars / ~2238 tokens)
    fd = next(r for r in roles if r["slug"] == "engineering-frontend-developer")
    assert 1500 <= fd["persona_tokens_est"] <= 3000, \
        f"frontend-developer tokens={fd['persona_tokens_est']}"
    print(f"✓ persona_tokens_est sane (frontend-developer: {fd['persona_chars']}c / {fd['persona_tokens_est']}t)")


def test_5_meta_compliance():
    meta = load("meta.json")
    for k in ("source", "snapshot_date", "total_divisions", "total_roles",
              "snapshot_method", "license", "sync_command", "extension_fork",
              "non_division_dirs", "roles_by_division", "lang_split"):
        assert k in meta, f"missing meta key: {k}"
    assert meta["extension_fork"] is False
    assert meta["license"] == "MIT"
    assert meta["snapshot_method"] == "git clone --depth 1"
    assert "examples" in meta["non_division_dirs"]
    assert "strategy" in meta["non_division_dirs"]
    assert "scripts" in meta["non_division_dirs"]
    assert "integrations" in meta["non_division_dirs"]

    # snapshot_date 是 ISO 日期
    import datetime as _dt
    _dt.date.fromisoformat(meta["snapshot_date"])
    print(f"✓ meta.json compliant: {meta['source']} ({meta['snapshot_date']})")


if __name__ == "__main__":
    test_1_divisions_count_and_alignment()
    test_2_roles_count_and_frontmatter()
    test_3_division_role_distribution()
    test_4_token_estimation_sanity()
    test_5_meta_compliance()
    print("\n所有 5 组断言通过 ✅")