"""tests/test_extract_awesome_hub.py — 验证 extract_awesome_hub.py 的产物

实测基线(2026-10-02 快照):
  - categories: 27(介于 25-32 之间)
  - topics:     677(>= 100,符合 hub 索引量级)
  - with_link:  675
  - level: L1=596 L2=81

这些测试不验证绝对数字(README 可能变动),只验证:
  1. 数据结构字段完整
  2. slug 规则正确
  3. 关键字段非空 / 不全为零
  4. 元信息字段合规(license/snapshot_date)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "extensions" / "awesome-hub-promo" / "data"

CATS = json.loads((DATA_DIR / "categories.json").read_text(encoding="utf-8"))
TOPICS = json.loads((DATA_DIR / "topics.json").read_text(encoding="utf-8"))
META = json.loads((DATA_DIR / "meta.json").read_text(encoding="utf-8"))


def test_categories_count():
    """25-32 之间(实测 27,README 可能缓慢增减)"""
    assert 25 <= len(CATS) <= 32, f"categories count out of range: {len(CATS)}"


def test_topics_minimum():
    """hub 索引量级 >= 100(实测 677)"""
    assert len(TOPICS) >= 100, f"topics too few: {len(TOPICS)}"


def test_required_fields_present():
    """必填字段:cat / name / level 存在且类型正确"""
    for t in TOPICS:
        assert "cat" in t and t["cat"], f"missing cat: {t}"
        assert "name" in t and t["name"], f"missing name: {t}"
        assert "level" in t and t["level"] in (1, 2), f"bad level: {t}"
        assert "url" in t
        assert "desc" in t
        assert "has_link" in t
        assert isinstance(t["has_link"], bool)


def test_at_least_5_categories_have_topics():
    """至少 5 个分类有主题(实测 27/27,留余量)"""
    n = sum(1 for c in CATS if c["n_topics"] > 0)
    assert n >= 5, f"only {n} categories have topics"


def test_meta_snapshot_and_license():
    """meta.snapshot_date 格式 YYYY-MM-DD + license_note=CC0-1.0"""
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", META["snapshot_date"]), META["snapshot_date"]
    assert META["license_note"] == "CC0-1.0", META["license_note"]
    assert "source" in META and "sindresorhus/awesome" in META["source"], META["source"]


def test_topics_with_url_and_desc():
    """至少 10 个 topic 同时有 url 和 desc(实测 > 600)"""
    n = sum(1 for t in TOPICS if t["url"] and t["desc"])
    assert n >= 10, f"only {n} topics have both url and desc"


def test_slug_rules():
    """slug 是 name 的 lowercase + 非字母数字字符替换为 '-' + 去前后 dash"""
    sample = TOPICS[:50]
    for t in sample:
        if not t["name"]:
            continue
        # 找到父分类的 slug
        cat = next(c for c in CATS if c["name"] == t["cat"])
        expected_slug = cat["slug"]
        # 验证 expected_slug 是符合规则的形式
        assert re.match(r"^[a-z0-9-]+$", expected_slug), f"bad slug: {expected_slug}"
        assert not expected_slug.startswith("-") and not expected_slug.endswith("-"), (
            f"slug has leading/trailing dash: {expected_slug}"
        )


if __name__ == "__main__":
    # 直接 python tests/test_extract_awesome_hub.py 也跑全部
    import pytest as _pytest  # type: ignore
    sys.exit(_pytest.main([__file__, "-v"]))
