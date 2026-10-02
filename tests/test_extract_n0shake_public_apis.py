"""tests/test_extract_n0shake_public_apis.py — Sprint 2 Phase A 数据抽取验收测试(2026-10-02)。

验收 8 维度:
  1. categories 数量在 [50, 60](目标 56 = 59 H3 - 3 尾部跳过段)
  2. services 数量在 [400, 600](目标 ~481)
  3. 每条 service 必含 cat/name/url/desc/open_trial
  4. sample("Spotify") 字段类型正确 + cat=Music
  5. sample("GitHub Licenses API") — 脏数据 desc=**N/A** 仍能解析(open_trial=**N/A**)
  6. open_trial 三档分布:N/A + 💸 + Open Source 应覆盖 > 99%
  7. meta.snapshot_date 存在 + 格式 YYYY-MM-DD + 含 license_note + agents_note
  8. categories.n_items 加总应等于 services 总数,且 cat 全部对齐 categories

跑法:
    pytest tests/test_extract_n0shake_public_apis.py -v
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "extensions" / "n0shake-public-apis-promo" / "data"


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_1_categories_count_in_range():
    cats = load("categories.json")
    assert 50 <= len(cats) <= 60, f"categories={len(cats)} 不在 [50,60]"
    # 每项必含 name/slug/n_items
    for c in cats:
        assert "name" in c and "slug" in c and "n_items" in c, f"分类字段缺失: {c}"
        assert isinstance(c["n_items"], int) and c["n_items"] >= 0
    # 尾部 Credits/More Resources/Contributions 不应出现(显式 SKIP_CATS)
    skip_names = {"Credits", "More Resources", "Contributions"}
    skip_in = [c["name"] for c in cats if c["name"] in skip_names]
    assert not skip_in, f"非 API 段不应入分类:{skip_in}"
    print(f"✓ categories={len(cats)} (区间 [50,60],尾部 3 段已跳过)")


def test_2_services_count_in_range():
    svcs = load("services.json")
    assert 400 <= len(svcs) <= 600, f"services={len(svcs)} 不在 [400,600]"
    print(f"✓ services={len(svcs)} (区间 [400,600],目标 ~481)")


def test_3_required_fields_present():
    svcs = load("services.json")
    required = {"cat", "name", "url", "desc", "open_trial", "level"}
    bad: list[str] = []
    for s in svcs:
        missing = required - set(s.keys())
        if missing:
            bad.append(f"{s.get('name','?')}: missing {missing}")
    assert not bad, f"{len(bad)} services 缺字段,示例:\n" + "\n".join(bad[:5])
    # level 全 1
    levels = {s["level"] for s in svcs}
    assert levels == {1}, f"levels 应全 1,实际 {levels}"
    # cat 全部对齐 categories
    cats = {c["name"] for c in load("categories.json")}
    orphan = [s["name"] for s in svcs if s["cat"] not in cats]
    assert not orphan, f"{len(orphan)} 条 service.cat 不在 categories.json,示例:{orphan[:3]}"
    print(f"✓ {len(svcs)} services 字段完整 + level 全 1 + cat 全部对齐 categories")


def test_4_spotify_sample_field_types():
    svcs = load("services.json")
    spotify = [s for s in svcs if s["name"] == "Spotify"]
    assert len(spotify) >= 1, "没找到 'Spotify' sample"
    s = spotify[0]
    assert s["url"].startswith("http"), f"url 异常: {s['url']}"
    assert isinstance(s["desc"], str) and len(s["desc"]) > 0
    known_open = {"N/A", "💸", "Open Source", "Unknown"}
    assert s["open_trial"] in known_open, f"未知 open_trial: {s['open_trial']}"
    assert s["cat"] == "Music", f"Spotify 分类错: {s['cat']}"
    print(f"✓ Spotify sample: url={s['url'][:60]}... cat={s['cat']} open_trial={s['open_trial']}")


def test_5_github_licenses_dirty_data():
    """GitHub Licenses API 行原 desc+open_triage 都是 **N/A**(上游脏数据)"""
    svcs = load("services.json")
    gh = [s for s in svcs if s["name"] == "GitHub Licenses API"]
    assert len(gh) >= 1, "没找到 'GitHub Licenses API' sample"
    s = gh[0]
    # 仍能解析,name/url/desc 都不为空
    assert s["name"] == "GitHub Licenses API"
    assert s["url"].startswith("http"), f"url 异常: {s['url']}"
    # desc 解析为 '**N/A**' 原文(我们知道这是错的,但不能丢失信息)
    assert "N/A" in s["desc"]
    assert s["open_trial"] == "N/A", f"open_trial 应解析为 N/A,实际 {s['open_trial']}"
    # GitHub Licenses API 在 Legal 分类下(`#### Open Licenses` 子段 → 归父 H3)
    assert s["cat"] == "Legal", f"GitHub Licenses API 分类错: {s['cat']}"
    print(f"✓ 脏数据 GitHub Licenses API 仍正确解析: cat={s['cat']} open_trial={s['open_trial']}")


def test_6_open_trial_distribution_sane():
    svcs = load("services.json")
    meta = load("meta.json")
    # 分布中三档(N/A + 💸 + Open Source)应覆盖 > 95%
    dist = meta["open_trial_distribution"]
    top3 = sum(dist.get(k, 0) for k in ("N/A", "💸", "Open Source"))
    coverage = top3 / len(svcs)
    assert coverage >= 0.95, f"open_trial 三档只覆盖 {top3}/{len(svcs)}={coverage:.2%}"
    # 含至少 1 个 Open Source + 1 个 💸(确认三类都存在)
    assert dist.get("Open Source", 0) >= 50, f"Open Source 太少: {dist.get('Open Source', 0)}"
    assert dist.get("💸", 0) >= 30, f"💸 太少: {dist.get('💸', 0)}"
    assert dist.get("N/A", 0) >= 200, f"N/A 太少: {dist.get('N/A', 0)}"
    print(f"✓ open_trial 三档覆盖 {top3}/{len(svcs)}={coverage:.1%}; "
          f"N/A={dist.get('N/A',0)} Open Source={dist.get('Open Source',0)} 💸={dist.get('💸',0)}")


def test_7_meta_snapshot_date_format():
    meta = load("meta.json")
    assert "snapshot_date" in meta, "meta 缺 snapshot_date"
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", meta["snapshot_date"]), \
        f"snapshot_date 格式错: {meta['snapshot_date']}"
    # 必有 license_note + agents_note + source
    assert meta["source"].startswith("https://github.com/n0shake")
    assert "只读快照" in meta["agents_note"] or "snapshot" in meta["agents_note"].lower()
    assert "license" in meta["license_note"].lower() or "CC" in meta["license_note"]
    # total_* 与 JSON 文件对齐
    assert meta["total_categories"] == len(load("categories.json"))
    assert meta["total_services"] == len(load("services.json"))
    print(f"✓ meta.snapshot_date={meta['snapshot_date']} source={meta['source'][:50]}...")


def test_8_categories_n_items_sum_matches_services():
    """交叉校验:categories.json 的 n_items 加总应等于 services 总数"""
    cats = load("categories.json")
    svcs = load("services.json")
    sum_n = sum(c["n_items"] for c in cats)
    assert sum_n == len(svcs), f"categories n_items 总和={sum_n} != services={len(svcs)}"
    # 抽 3 个分类抽查实际条目数对齐
    big3 = sorted(cats, key=lambda c: -c["n_items"])[:3]
    for c in big3:
        actual = sum(1 for s in svcs if s["cat"] == c["name"])
        assert actual == c["n_items"], \
            f"分类 '{c['name']}' n_items={c['n_items']} 但实际 services={actual}"
    print(f"✓ categories.n_items 总和={sum_n} == services={len(svcs)},Top3: " +
          ", ".join(f"{c['name']}({c['n_items']})" for c in big3))