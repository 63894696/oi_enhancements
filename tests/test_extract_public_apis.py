"""tests/test_extract_public_apis.py — Phase A 数据抽取验收测试(2026-10-02)。

验收 6 维度:
  1. categories 数量在 [40, 60](目标 51)
  2. services 数量在 [1500, 2100](目标 ~1953)
  3. 每条 service 必含 cat/name/url/desc/auth/https/cors/level
  4. sample("Cat Facts") 含 url + 字段类型正确
  5. 各字段分布 sanity:auth 'No'/'apiKey'/'OAuth' 应在合理范围
  6. meta.snapshot_date 存在且格式 YYYY-MM-DD

跑法:
    pytest tests/test_extract_public_apis.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "extensions" / "public-apis-promo" / "data"


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_1_categories_count_in_range():
    cats = load("categories.json")
    assert 40 <= len(cats) <= 60, f"categories={len(cats)} 不在 [40,60]"
    assert len(cats) == 51, f"实际 categories={len(cats)},期望 51"
    # 每项必含 name/slug/n_items
    for c in cats:
        assert "name" in c and "slug" in c and "n_items" in c, f"分类字段缺失: {c}"
        assert isinstance(c["n_items"], int) and c["n_items"] >= 0
    print(f"✓ categories={len(cats)} (区间 [40,60],目标 51)")


def test_2_services_count_in_range():
    svcs = load("services.json")
    assert 1500 <= len(svcs) <= 2100, f"services={len(svcs)} 不在 [1500,2100]"
    assert len(svcs) == 1953, f"实际 services={len(svcs)},期望 1953"
    print(f"✓ services={len(svcs)} (区间 [1500,2100],目标 1953)")


def test_3_required_fields_present():
    svcs = load("services.json")
    required = {"cat", "name", "url", "desc", "auth", "https", "cors", "level"}
    bad: list[str] = []
    for s in svcs:
        missing = required - set(s.keys())
        if missing:
            bad.append(f"{s.get('name','?')}: missing {missing}")
    assert not bad, f"{len(bad)} services 缺字段,示例:\n" + "\n".join(bad[:5])
    # 所有 level 都应是 1(public-apis 全 L1,无嵌套)
    levels = {s["level"] for s in svcs}
    assert levels == {1}, f"levels 应全 1,实际 {levels}"
    # cat 必须落在 categories.json 里
    cats = {c["name"] for c in load("categories.json")}
    orphan = [s["name"] for s in svcs if s["cat"] not in cats]
    assert not orphan, f"{len(orphan)} 条 service.cat 不在 categories.json,示例:{orphan[:3]}"
    print(f"✓ {len(svcs)} services 字段完整 + level 全 1 + cat 全部对齐 categories")


def test_4_cat_facts_sample_field_types():
    svcs = load("services.json")
    cat_facts = [s for s in svcs if s["name"] == "Cat Facts"]
    assert len(cat_facts) >= 1, "没找到 'Cat Facts' sample"
    s = cat_facts[0]
    # URL 应是 https:// 开头
    assert s["url"].startswith("http"), f"url 异常: {s['url']}"
    # desc 非空
    assert isinstance(s["desc"], str) and len(s["desc"]) > 0
    # auth/https/cors 应在 known set
    known_auth = {"No", "apiKey", "OAuth", "X-Mashape-Key", "User-Agent"}
    known_yesno = {"Yes", "No", "Unknown"}
    assert s["auth"] in known_auth, f"未知 auth: {s['auth']}"
    assert s["https"] in known_yesno, f"未知 https: {s['https']}"
    assert s["cors"] in known_yesno, f"未知 cors: {s['cors']}"
    # Cat Facts 都在 Animals 类下
    assert s["cat"] == "Animals", f"Cat Facts 分类错: {s['cat']}"
    print(f"✓ Cat Facts sample: url={s['url']} cat={s['cat']} auth={s['auth']} https={s['https']} cors={s['cors']}")


def test_5_field_distributions_sane():
    svcs = load("services.json")
    meta = load("meta.json")
    # auth 分布:No + apiKey + OAuth 应覆盖 > 95%
    auth_dist = meta["auth_distribution"]
    top3 = sum(auth_dist.get(k, 0) for k in ("No", "apiKey", "OAuth"))
    assert top3 / len(svcs) >= 0.95, f"auth top3 只覆盖 {top3}/{len(svcs)}={top3/len(svcs):.2%}"
    # https 应只有 Yes/No
    assert set(meta["https_distribution"].keys()) <= {"Yes", "No"}, \
        f"https 分布出现异常 key: {set(meta['https_distribution'].keys())}"
    # cors 应只有 Yes/No/Unknown
    assert set(meta["cors_distribution"].keys()) <= {"Yes", "No", "Unknown"}, \
        f"cors 分布出现异常 key: {set(meta['cors_distribution'].keys())}"
    print(f"✓ auth_top3={top3}/{len(svcs)}={top3/len(svcs):.1%}; "
          f"https={meta['https_distribution']}; "
          f"cors_keys={list(meta['cors_distribution'].keys())}")


def test_6_meta_snapshot_date_format():
    meta = load("meta.json")
    assert "snapshot_date" in meta, "meta 缺 snapshot_date"
    # 格式 YYYY-MM-DD
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", meta["snapshot_date"]), \
        f"snapshot_date 格式错: {meta['snapshot_date']}"
    # 必有 license_note + agents_note + source
    assert meta["source"].startswith("https://github.com/public-apis")
    assert "MIT" in meta["license_note"]
    assert "只读快照" in meta["agents_note"] or "snapshot" in meta["agents_note"].lower()
    # total_categories/total_services 与 JSON 文件对齐
    assert meta["total_categories"] == len(load("categories.json"))
    assert meta["total_services"] == len(load("services.json"))
    print(f"✓ meta.snapshot_date={meta['snapshot_date']} source={meta['source']}")


def test_7_categories_n_items_sum_matches_services():
    """交叉校验:categories.json 的 n_items 加总应等于 services 总数"""
    cats = load("categories.json")
    svcs = load("services.json")
    sum_n = sum(c["n_items"] for c in cats)
    assert sum_n == len(svcs), f"categories n_items 总和={sum_n} != services={len(svcs)}"
    # 抽 3 个分类抽查
    big3 = sorted(cats, key=lambda c: -c["n_items"])[:3]
    for c in big3:
        actual = sum(1 for s in svcs if s["cat"] == c["name"])
        assert actual == c["n_items"], \
            f"分类 '{c['name']}' n_items={c['n_items']} 但实际 services={actual}"
    print(f"✓ categories.n_items 总和={sum_n} == services={len(svcs)},Top3: " +
          ", ".join(f"{c['name']}({c['n_items']})" for c in big3))
