"""
tests/test_agency_capabilities.py — agency_capabilities 单元测试(Phase 1.6,2026-09-28)。

验证 6 维度:
  1. register_capability 自动注入 3 个 capability(agency.list_divisions / search / detail)
  2. skills_index 总数 ≥ 80(含 3 个 agency)
  3. list_divisions 返回 18 个 division + 264 总数
  4. search 关键词命中(name/div/tag 评分)
  5. detail 精确 slug 查回 persona_md
  6. fail-soft(不存在 slug → ok=False + 友好 hint)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须先 import 让 module-level register_all 跑
from prisir_work import poster_capabilities  # noqa: E402,F401
from prisir_work import poster_to_image_capability  # noqa: E402,F401
from prisir_work import free_for_dev_capabilities  # noqa: E402,F401
from prisir_work import agency_capabilities  # noqa: E402,F401
from prisir_work import capability as c  # noqa: E402
from prisir_work import skills  # noqa: E402
from prisir_work import endpoints as _ep  # noqa: E402


# ── 1. 3 个 capability 已注册 ──────────────────────────────
def test_1_capability_registered():
    ids = {x["id"] for x in c.list_capabilities()}
    for need in ("agency.list_divisions", "agency.search", "agency.detail"):
        assert need in ids, f"{need} not in capability._REGISTRY"
    print(f"✓ 3 agency capability registered (total {len(ids)})")


# ── 2. skills_index 包含 agency.* ─────────────────────────
def test_2_skills_index_includes_agency():
    n = skills.count_skills()
    assert n >= 80, f"expected ≥80 skills, got {n}"
    idx = skills.describe_registry()
    agency_skills = [s for s in idx["skills"] if s["id"].startswith("agency.")]
    assert len(agency_skills) >= 3, f"agency skills count = {len(agency_skills)}"
    print(f"✓ skills_index covers {n} skills, agency.* = {len(agency_skills)}")


# ── 3. list_divisions 端点返回 18 division ────────────────
def test_3_list_divisions_returns_18():
    entry = _ep._REGISTRY["/agency/list_divisions"]
    payload, status = entry["handler"]({})
    assert status == 200
    assert payload["ok"] is True
    assert payload["total_divisions"] == 18
    assert payload["total_roles"] >= 200
    assert payload["license"] == "MIT"
    # 每个 division 含 name/label/n_roles
    div0 = payload["divisions"][0]
    assert {"name", "label", "n_roles"}.issubset(div0.keys())
    print(f"✓ list_divisions: {payload['total_divisions']} divs / "
          f"{payload['total_roles']} roles (MIT)")


# ── 4. search 关键词命中 + 评分 ───────────────────────────
def test_4_search_keyword_hit():
    entry = _ep._REGISTRY["/agency/search"]
    payload, status = entry["handler"]({"query": "frontend"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["total"] >= 1
    # 第一条应是 frontend-developer 或类似 div=engineering
    top = payload["results"][0]
    assert top["div"] in ("engineering", "design", "specialized"), \
        f"unexpected top div={top['div']}"
    assert top["score"] > 0
    # 限定 division 时精确过滤
    payload2, _ = entry["handler"]({"query": "developer", "division": "engineering"})
    for r in payload2["results"]:
        assert r["div"] == "engineering"
    print(f"✓ search 'frontend' → {payload['total']} hits, top={top['slug']}")
    print(f"  division filter → {payload2['total']} in engineering")


# ── 5. detail 查 persona ──────────────────────────────────
def test_5_detail_returns_persona():
    entry = _ep._REGISTRY["/agency/detail"]
    payload, status = entry["handler"]({"slug": "engineering-frontend-developer"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["slug"] == "engineering-frontend-developer"
    assert payload["div"] == "engineering"
    assert "# Anthropologist" in payload["persona_md"] or "Frontend" in payload["persona_md"] \
        or "Developer" in payload["persona_md"]
    assert payload["persona_tokens_est"] > 500
    print(f"✓ detail: {payload['slug']} persona = {payload['persona_chars']}c / "
          f"{payload['persona_tokens_est']}t")


# ── 6. fail-soft: 不存在 slug → ok=False ──────────────────
def test_6_failsoft_not_found():
    entry = _ep._REGISTRY["/agency/detail"]
    payload, status = entry["handler"]({"slug": "nonexistent-role-xyz"})
    assert status == 200
    assert payload["ok"] is False
    assert "not_found" in payload["error"] or payload["error"] == "role_not_found"

    # search 缺 query+division → 友好 hint
    payload2, _ = _ep._REGISTRY["/agency/search"]["handler"]({})
    assert payload2["ok"] is False
    assert payload2["error"] == "missing_query_or_division"
    print("✓ fail-soft: not_found + missing_args 都有友好降级")


if __name__ == "__main__":
    test_1_capability_registered()
    test_2_skills_index_includes_agency()
    test_3_list_divisions_returns_18()
    test_4_search_keyword_hit()
    test_5_detail_returns_persona()
    test_6_failsoft_not_found()
    print("\n所有 6 组断言通过 ✅")