"""test_nokeyapi_capabilities.py — Phase C capability 层测试(nokeyapi.*)

跑法:
  pytest tests/test_nokeyapi_capabilities.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import prisir_work.nokeyapi_capabilities as cap_mod


def test_module_imports_and_auto_registers():
    assert hasattr(cap_mod, "NOKEYAPI_CAPABILITIES")
    assert hasattr(cap_mod, "intent_summary")
    assert hasattr(cap_mod, "register_all")
    assert hasattr(cap_mod, "EXT_ID")
    assert cap_mod.EXT_ID == "n0shake-public-apis-promo"
    assert len(cap_mod.NOKEYAPI_CAPABILITIES) == 4
    print(f"✓ module imports ok + EXT_ID={cap_mod.EXT_ID}")


def test_capability_ids_and_risk_levels():
    expected_ids = {"nokeyapi.find", "nokeyapi.list_categories", "nokeyapi.detail", "nokeyapi.random"}
    actual = {c["id"] for c in cap_mod.NOKEYAPI_CAPABILITIES}
    assert actual == expected_ids, f"cap ids: {actual}"
    risks = {c["risk"] for c in cap_mod.NOKEYAPI_CAPABILITIES}
    assert risks == {"L0"}, f"all risk should be L0, got {risks}"
    print("✓ 4 capability ids 全在 + risk 全 L0")


def test_capability_endpoints_unique():
    eps = [c["endpoint"] for c in cap_mod.NOKEYAPI_CAPABILITIES]
    assert len(set(eps)) == 4
    for ep in eps:
        assert ep.startswith("/nokeyapi/"), f"endpoint should start with /nokeyapi/: {ep}"
    print(f"✓ 4 endpoint 唯一 + 全是 /nokeyapi/ 前缀: {eps}")


def test_keywords_priority_chinese():
    """免 key / 免注册 / 开源 / 试用 关键词必须有"""
    for c in cap_mod.NOKEYAPI_CAPABILITIES:
        kw = c["keywords"]
        joined = " ".join(kw)
        has_priority = any(k in joined for k in ("免 key", "免注册", "免授权", "开源", "试用", "免费"))
        assert has_priority, f"keywords 缺核心中文 for {c['id']}: {kw}"
    print("✓ 所有 capability 含核心中文(免 key/免注册/开源/试用)")


def test_intent_summary_contains_exec_examples():
    summary = cap_mod.intent_summary()
    for must in ("nokeyapi.find", "nokeyapi.list_categories", "nokeyapi.detail", "nokeyapi.random"):
        assert must in summary, f"intent_summary 不含 {must}"
    assert "[[EXEC:" in summary
    assert "🔓" in summary or "免 key" in summary
    assert "快照" in summary or "Open/Trial" in summary or "Trial" in summary
    assert len(summary) >= 400
    print(f"✓ intent_summary 长度={len(summary)} 字符,含 4 EXEC 示例")


def test_handler_passes_args_and_returns_payload():
    captured = {}

    def fake_rpc(ext_id, m, body, timeout=4.0):
        captured["ext_id"] = ext_id
        captured["method"] = m
        captured["body"] = body
        captured["timeout"] = timeout
        return {"ok": True, "result": {"type": "card", "html": "<i>nk</i>", "meta": {"n": 3}}}

    with patch.dict("sys.modules", {"prisiragent_web": type("_M", (), {"_ext_rpc_call": staticmethod(fake_rpc)})()}):
        handler = cap_mod._make_handler("nokeyapi.find")
        result, status = handler({"query": "weather", "limit": 5})
        assert status == 200
        assert result["ok"] is True
        assert result["html"] == "<i>nk</i>"
        assert result["meta"] == {"n": 3}
    assert captured["ext_id"] == "n0shake-public-apis-promo"
    assert captured["method"] == "find"
    assert captured["body"] == {"query": "weather", "limit": 5}
    assert captured["timeout"] == 4.0
    print(f"✓ handler 透传 + ext_id={captured['ext_id']} method={captured['method']}")


def test_handler_fallback_when_rpc_returns_error():
    def fake_rpc(ext_id, m, body, timeout=4.0):
        return {"ok": False, "error": "ext subprocess not running"}

    with patch.dict("sys.modules", {"prisiragent_web": type("_M", (), {"_ext_rpc_call": staticmethod(fake_rpc)})()}):
        handler = cap_mod._make_handler("nokeyapi.detail")
        result, status = handler({"name": "Spotify"})
        assert status == 200
        assert result["ok"] is False
        assert result.get("warning") == "ext_unavailable"
    print("✓ handler 扩展不可达降级 200 + ok=False + warning")


def test_handler_fallback_when_ext_bridge_missing():
    import sys
    saved = sys.modules.pop("prisiragent_web", None)
    try:
        handler = cap_mod._make_handler("nokeyapi.random")
        result, status = handler({"category": "Music"})
        assert status == 200
        assert result["ok"] is False
        assert "ext_bridge_unavailable" in result.get("error", "")
    finally:
        if saved is not None:
            sys.modules["prisiragent_web"] = saved
    print("✓ handler ext_bridge 缺失降级 200 + ok=False")


def test_handler_fallback_when_bad_result_type():
    def fake_rpc(ext_id, m, body, timeout=4.0):
        return ["bad", "list", "shape"]

    with patch.dict("sys.modules", {"prisiragent_web": type("_M", (), {"_ext_rpc_call": staticmethod(fake_rpc)})()}):
        handler = cap_mod._make_handler("nokeyapi.list_categories")
        result, status = handler({})
        assert status == 200
        assert result["ok"] is False
    print("✓ handler 返坏类型降级 200 + ok=False")


def test_capability_doesnt_clobber_existing_endpoint():
    n1 = cap_mod.register_all()
    n2 = cap_mod.register_all()
    assert n1 == n2 == 4
    print("✓ register_all() idempotent: 两次均返回 4")


def test_namespace_separation_from_all_other_caps():
    """nokeyapi.* 与 free.* / api.* / api_cn.* 命名空间完全独立"""
    import prisir_work.free_for_dev_capabilities as cap_free
    import prisir_work.public_apis_capabilities as cap_api
    import prisir_work.public_apis_cn_capabilities as cap_cn
    all_other = set()
    all_other |= {c["id"] for c in cap_free.FREE_FOR_DEV_CAPABILITIES}
    all_other |= {c["id"] for c in cap_api.PUBLIC_APIS_CAPABILITIES}
    all_other |= {c["id"] for c in cap_cn.PUBLIC_APIS_CN_CAPABILITIES}
    nokey = {c["id"] for c in cap_mod.NOKEYAPI_CAPABILITIES}
    overlap = nokey & all_other
    assert not overlap, f"命名空间重叠: {overlap}"
    print(f"✓ nokeyapi.* 与 free.* / api.* / api_cn.* 命名空间隔离")


if __name__ == "__main__":
    test_module_imports_and_auto_registers()
    test_capability_ids_and_risk_levels()
    test_capability_endpoints_unique()
    test_keywords_priority_chinese()
    test_intent_summary_contains_exec_examples()
    test_handler_passes_args_and_returns_payload()
    test_handler_fallback_when_rpc_returns_error()
    test_handler_fallback_when_ext_bridge_missing()
    test_handler_fallback_when_bad_result_type()
    test_capability_doesnt_clobber_existing_endpoint()
    test_namespace_separation_from_all_other_caps()
    print("\n所有 11 组断言通过 ✅")