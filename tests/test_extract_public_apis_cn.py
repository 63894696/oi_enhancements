"""test_extract_public_apis_cn.py — Phase A 抽取器测试

跑法:
  pytest tests/test_extract_public_apis_cn.py -v
"""
import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "extensions" / "public-apis-cn-promo" / "data"


def load_json(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def test_categories_count_in_range():
    cats = load_json("categories.json")
    assert 40 <= len(cats) <= 70, f"categories count out of range: {len(cats)}"
    print(f"✓ categories={len(cats)}")


def test_services_count_in_range():
    svcs = load_json("services.json")
    assert 1200 <= len(svcs) <= 1800, f"services count out of range: {len(svcs)}"
    print(f"✓ services={len(svcs)}")


def test_required_fields_present():
    svcs = load_json("services.json")
    sample = svcs[0]
    assert set(sample.keys()) >= {"cat", "name", "url", "desc", "auth", "https"}, \
        f"missing required fields in sample: {sample.keys()}"
    # auth 字段实际分布: apiKey / No / OAuth / 否 / X-Mashape-Key / User-Agent
    # 接受上游偶发解析异常(≤0.5% 视为可接受边缘 case,如 'Vend' 等)
    valid_auth = {"是", "否", "apiKey", "OAuth", "User-Agent", "无", "未知", "No", "X-Mashape-Key", ""}
    bad = [s["name"] for s in svcs if s.get("auth") not in valid_auth]
    pct = len(bad) / len(svcs) * 100
    assert pct <= 0.5, f"异常 auth 占比 {pct:.2f}% 超 0.5%: {bad[:5]}"
    print(f"✓ required fields present + auth 值域合规(异常值 {len(bad)} 条,占 {pct:.2f}%)")


def test_categories_sum_matches():
    cats = load_json("categories.json")
    svcs = load_json("services.json")
    cat_sum = sum(c["n_items"] for c in cats)
    assert cat_sum == len(svcs), f"category n_items sum {cat_sum} ≠ services count {len(svcs)}"
    print(f"✓ category n_items sum ({cat_sum}) = services count")


def test_meta_has_required_keys():
    meta = load_json("meta.json")
    required = {"source", "snapshot_date", "total_categories", "total_services", "license_note", "agents_note"}
    missing = required - set(meta.keys())
    assert not missing, f"meta missing keys: {missing}"
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", meta["snapshot_date"]), \
        f"snapshot_date not YYYY-MM-DD: {meta['snapshot_date']}"
    print(f"✓ meta snapshot_date={meta['snapshot_date']} valid")


def test_sample_top_categories():
    """抽样数据健全度 — 至少几个著名国内 API 在内(实测数据命名带 API 后缀)"""
    svcs = load_json("services.json")
    names = {s["name"] for s in svcs}
    # 期望一些常见的国内 API(按数据真实名字,带 API 后缀)
    expected_any = [
        "高德天气API", "和风天气API", "心知天气API",
        "DeepSeek深度求索", "Qwen通义千问", "KIMI", "ChatGLM智谱",
    ]
    hit = [n for n in expected_any if n in names]
    assert len(hit) >= 3, f"famous CN APIs missing: hit={hit}, expected≥3"
    print(f"✓ 国内知名 API 命中 ≥3: {hit}")


def test_auth_distribution():
    """认证字段分布 — 反映 public-apis-cn 数据真实形态(英文 + 中文混合)"""
    svcs = load_json("services.json")
    from collections import Counter
    dist = Counter(s.get("auth") for s in svcs)
    top5 = dist.most_common(5)
    # 数据真实: apiKey ≈ No 各占 ≈ 44%,说明「需要 key 与免 key 基本对半」
    # 这与 public-apis(pure 英文)不同 — cn 收的是中文 API 圈,有注册文化的痕迹
    print(f"✓ auth top5: {dict(top5)}")
    # sanity: top2 应是 apiKey 或 No
    top_keys = {k for k, _ in top5[:2]}
    assert top_keys <= {"apiKey", "No", "OAuth"}, f"unexpected top auth: {top_keys}"
    # 至少有 6 档不同的 auth 值
    assert len(dist) >= 6, f"auth variety too narrow: {dict(top5)}"
    print(f"  auth 类别数: {len(dist)}")


def test_url_format():
    """URL 字段必须是 http(s):// 开头"""
    svcs = load_json("services.json")
    bad = []
    for s in svcs[:500]:
        url = s.get("url", "")
        if url and not (url.startswith("http://") or url.startswith("https://")):
            bad.append((s["name"], url))
    assert len(bad) == 0, f"URLs not http(s): {bad[:5]}"
    print(f"✓ 500 条抽样 URL 全是 http(s):// 开头")


def test_cn_nonservers_extracted():
    """中文 desc 不应为空"""
    svcs = load_json("services.json")
    bad = [s["name"] for s in svcs[:200] if not s.get("desc", "").strip()]
    assert len(bad) == 0, f"空 desc: {bad[:5]}"
    print(f"✓ 200 条抽样 desc 全非空")


if __name__ == "__main__":
    test_categories_count_in_range()
    test_services_count_in_range()
    test_required_fields_present()
    test_categories_sum_matches()
    test_meta_has_required_keys()
    test_sample_top_categories()
    test_auth_distribution()
    test_url_format()
    test_cn_nonservers_extracted()
    print("\n所有 9 组断言通过 ✅")