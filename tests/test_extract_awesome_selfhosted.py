"""tests/test_extract_awesome_selfhosted.py — Phase A 数据抽取验收测试(2026-10-02)。

验收 9 维度:
  1. categories 数量在 [80, 110](目标 95)
  2. services 数量在 [1200, 1350](目标 ~1260)
  3. 每条 service 必含 cat/name/url/licenses/languages/source_code_url/has_warning
  4. 抽样验证:Nextcloud 应在 File Transfer & Synchronization 类下,lic 含 AGPL-3.0
  5. 抽样验证:Matomo 应有 source_code_url 字段(从 ([Source Code](url)) 提取)
  6. 抽样验证:有 warning 的条目(如 Postiz)has_warning=true
  7. licenses 字段应不含 / (例如 Apache-2.0/MIT 已被拆分)
  8. meta 含 license_distribution + language_distribution
  9. categories.n_items 总和应等于 services 总数

跑法:
    pytest tests/test_extract_awesome_selfhosted.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "extensions" / "awesome-selfhosted-promo" / "data"


def load(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def test_1_categories_count_in_range():
    cats = load("categories.json")
    assert 80 <= len(cats) <= 110, f"categories={len(cats)} 不在 [80,110]"
    assert len(cats) == 95, f"实际 categories={len(cats)},期望 95"
    # 每项必含 name/slug/n_items
    for c in cats:
        assert "name" in c and "slug" in c and "n_items" in c, f"分类字段缺失: {c}"
        assert isinstance(c["n_items"], int) and c["n_items"] >= 0
    print(f"OK categories={len(cats)} (区间 [80,110],目标 95)")


def test_2_services_count_in_range():
    svcs = load("services.json")
    assert 1200 <= len(svcs) <= 1350, f"services={len(svcs)} 不在 [1200,1350]"
    assert len(svcs) == 1260, f"实际 services={len(svcs)},期望 1260"
    print(f"OK services={len(svcs)} (区间 [1200,1350],目标 1260)")


def test_3_required_fields_present():
    svcs = load("services.json")
    required = {"cat", "name", "url", "desc", "licenses", "languages", "source_code_url", "has_warning"}
    bad: list[str] = []
    for s in svcs:
        missing = required - set(s.keys())
        if missing:
            bad.append(f"{s.get('name','?')}: missing {missing}")
    assert not bad, f"{len(bad)} services 缺字段,示例:\n" + "\n".join(bad[:5])
    # licenses 和 languages 必须是 list[str]
    for s in svcs[:50]:
        assert isinstance(s["licenses"], list), f"licenses 不是 list: {s['name']}"
        assert isinstance(s["languages"], list), f"languages 不是 list: {s['name']}"
        assert isinstance(s["has_warning"], bool), f"has_warning 不是 bool: {s['name']}"
    # cat 必须落在 categories.json 里
    cats = {c["name"] for c in load("categories.json")}
    orphan = [s["name"] for s in svcs if s["cat"] not in cats]
    assert not orphan, f"{len(orphan)} 条 service.cat 不在 categories.json,示例:{orphan[:3]}"
    print(f"OK {len(svcs)} services 字段完整 + cat 全部对齐 categories")


def test_4_nextcloud_sample_field_types():
    svcs = load("services.json")
    nx = [s for s in svcs if s["name"] == "Nextcloud"]
    assert len(nx) >= 1, "没找到 'Nextcloud' sample"
    s = nx[0]
    # URL 应是 http(s):// 开头
    assert s["url"].startswith("http"), f"url 异常: {s['url']}"
    # licenses 应包含 AGPL-3.0
    assert "AGPL-3.0" in s["licenses"], f"Nextcloud licenses 不含 AGPL-3.0: {s['licenses']}"
    # cat 应是 File Transfer & Synchronization
    assert s["cat"] == "File Transfer & Synchronization", f"Nextcloud cat 错: {s['cat']}"
    print(f"OK Nextcloud sample: url={s['url']} cat={s['cat']} licenses={s['licenses']}")


def test_5_matomo_has_source_code_url():
    svcs = load("services.json")
    m = [s for s in svcs if s["name"] == "Matomo"]
    assert len(m) >= 1, "没找到 'Matomo' sample"
    s = m[0]
    assert s["source_code_url"], f"Matomo 应有 source_code_url: {s['source_code_url']}"
    assert "github.com" in s["source_code_url"], f"source_code_url 异常: {s['source_code_url']}"
    print(f"OK Matomo source_code_url={s['source_code_url']}")


def test_6_postiz_has_warning():
    svcs = load("services.json")
    p = [s for s in svcs if s["name"] == "Postiz"]
    assert len(p) >= 1, "没找到 'Postiz' sample"
    s = p[0]
    assert s["has_warning"] is True, f"Postiz 应有 has_warning=True: {s['has_warning']}"
    print(f"OK Postiz has_warning={s['has_warning']}")


def test_7_licenses_no_slash():
    svcs = load("services.json")
    bad = []
    for s in svcs:
        for lic in s.get("licenses", []):
            if "/" in lic:
                bad.append(f"{s['name']}: {lic}")
    assert not bad, f"{len(bad)} 条 licenses 含 / 未拆分,示例:\n" + "\n".join(bad[:5])
    print(f"OK 所有 licenses 已拆 / ({len(svcs)} 条)")


def test_8_meta_distributions_present():
    meta = load("meta.json")
    assert "license_distribution" in meta, "meta 缺 license_distribution"
    assert "language_distribution" in meta, "meta 缺 language_distribution"
    # 顶层 license 应有 MIT/AGPL-3.0/GPL-3.0/Apache-2.0
    lic_top = set(list(meta["license_distribution"].keys())[:5])
    assert "MIT" in lic_top, f"MIT 应在 license top5: {lic_top}"
    assert "AGPL-3.0" in lic_top or "GPL-3.0" in lic_top, \
        f"AGPL-3.0 或 GPL-3.0 应在 top5: {lic_top}"
    # 顶层 language 应有 Docker
    lang_top = set(list(meta["language_distribution"].keys())[:5])
    assert "Docker" in lang_top, f"Docker 应在 language top5: {lang_top}"
    # ⚠ 不应在 languages 分布里(应被跳过)
    assert "⚠" not in meta["language_distribution"], \
        "⚠ 不应在 language_distribution 中"
    print(f"OK license_top5={lic_top}; language_top5={lang_top}")


def test_9_categories_n_items_sum_matches_services():
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
    print(f"OK categories.n_items 总和={sum_n} == services={len(svcs)},Top3: " +
          ", ".join(f"{c['name']}({c['n_items']})" for c in big3))


def test_10_meta_snapshot_and_license_note():
    meta = load("meta.json")
    assert "snapshot_date" in meta, "meta 缺 snapshot_date"
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", meta["snapshot_date"]), \
        f"snapshot_date 格式错: {meta['snapshot_date']}"
    assert meta["source"].startswith("https://github.com/awesome-selfhosted")
    # 协议是 CC-BY-SA-3.0(注意:不是 CC0-1.0)
    assert "CC-BY-SA" in meta["license_note"], f"license_note 应含 CC-BY-SA: {meta['license_note']}"
    assert "只读快照" in meta["agents_note"] or "snapshot" in meta["agents_note"].lower()
    assert meta["total_categories"] == len(load("categories.json"))
    assert meta["total_services"] == len(load("services.json"))
    print(f"OK meta.snapshot_date={meta['snapshot_date']} source={meta['source']}")
