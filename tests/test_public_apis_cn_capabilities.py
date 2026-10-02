"""test_public_apis_cn_capabilities.py — Phase C capability 层测试(国内 API 版本)

跑法:
  pytest tests/test_public_apis_cn_capabilities.py -v
"""
from __future__ import annotations

from unittest.mock import patch

import prisir_work.public_apis_cn_capabilities as cap_mod


def test_module_imports_and_auto_registers():
    assert hasattr(cap_mod, "PUBLIC_APIS_CN_CAPABILITIES")
    assert hasattr(cap_mod, "intent_summary")
    assert hasattr(cap_mod, "register_all")
    assert hasattr(cap_mod, "EXT_ID")
    assert cap_mod.EXT_ID == "public-apis-cn-promo"
    assert len(cap_mod.PUBLIC_APIS_CN_CAPABILITIES) == 4
    print(f"✓ module imports ok + EXT_ID={cap_mod.EXT_ID}")


def test_capability_ids_and_risk_levels():
    expected_ids = {"api_cn.find", "api_cn.list_categories", "api_cn.detail", "api_cn.random"}
    actual = {c["id"] for c in cap_mod.PUBLIC_APIS_CN_CAPABILITIES}
    assert actual == expected_ids, f"cap ids: {actual}"
    risks = {c["risk"] for c in cap_mod.PUBLIC_APIS_CN_CAPABILITIES}
    assert risks == {"L0"}, f"all risk should be L0, got {risks}"
    print("✓ 4 capability ids 全在 + risk 全 L0")


def test_capability_endpoints_unique():
    eps = [c["endpoint"] for c in cap_mod.PUBLIC_APIS_CN_CAPABILITIES]
    assert len(set(eps)) == 4
    for ep in eps:
        assert ep.startswith("/api_cn/"), f"endpoint should start with /api_cn/: {ep}"
    print(f"✓ 4 endpoint 唯一 + 全是 /api_cn/ 前缀: {eps}")


def test_keywords_chinese_priority():
    """国内 API 关键词必须有中文(国内/中国/中文/中国接口)"""
    for c in cap_mod.PUBLIC_APIS_CN_CAPABILITIES:
        kw = c["keywords"]
        joined = " ".join(kw)
        # 至少有一个中文关键词(国内/中国/中文/接口/服务/API)
        has_cn = any(k in joined for k in ("国内", "中国", "中文", "接口", "服务"))
        assert has_cn, f"keywords 缺中文关键词 for {c['id']}: {kw}"
    print("✓ 所有 capability 含中文关键词")


def test_intent_summary_contains_exec_examples():
    summary = cap_mod.intent_summary()
    for must in ("api_cn.find", "api_cn.list_categories", "api_cn.detail", "api_cn.random"):
        assert must in summary, f"intent_summary 不含 {must}"
    assert "[[EXEC:" in summary
    assert "🇨🇳" in summary or "国内" in summary
    assert "快照" in summary or "可访问" in summary
    assert len(summary) >= 400
    print(f"✓ intent_summary 长度={len(summary)} 字符,含 4 EXEC 示例")


def test_handler_passes_args_and_returns_payload():
    captured = {}

    def fake_rpc(ext_id, m, body, timeout=4.0):
        captured["ext_id"] = ext_id
        captured["method"] = m
        captured["body"] = body
        captured["timeout"] = timeout
        return {"ok": True, "result": {"type": "card", "html": "<b>cn</b>", "meta": {"n": 5}}}

    with patch.dict("sys.modules", {"prisiragent_web": type("_M", (), {"_ext_rpc_call": staticmethod(fake_rpc)})()}):
        handler = cap_mod._make_handler("api_cn.find")
        result, status = handler({"query": "天气", "limit": 3})
        assert status == 200
        assert result["ok"] is True
        assert result["html"] == "<b>cn</b>"
        assert result["meta"] == {"n": 5}
    assert captured["ext_id"] == "public-apis-cn-promo"
    assert captured["method"] == "find"
    assert captured["body"] == {"query": "天气", "limit": 3}
    assert captured["timeout"] == 4.0
    print(f"✓ handler 透传 + ext_id={captured['ext_id']} method={captured['method']}")


def test_handler_fallback_when_rpc_returns_error():
    def fake_rpc(ext_id, m, body, timeout=4.0):
        return {"ok": False, "error": "ext subprocess not running"}

    with patch.dict("sys.modules", {"prisiragent_web": type("_M", (), {"_ext_rpc_call": staticmethod(fake_rpc)})()}):
        handler = cap_mod._make_handler("api_cn.detail")
        result, status = handler({"name": "高德地图"})
        assert status == 200
        assert result["ok"] is False
        assert result.get("warning") == "ext_unavailable"
    print("✓ handler 扩展不可达降级 200 + ok=False + warning")


def test_handler_fallback_when_ext_bridge_missing():
    import sys
    saved = sys.modules.pop("prisiragent_web", None)
    try:
        handler = cap_mod._make_handler("api_cn.random")
        result, status = handler({"category": "天气"})
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
        handler = cap_mod._make_handler("api_cn.list_categories")
        result, status = handler({})
        assert status == 200
        assert result["ok"] is False
    print("✓ handler 返坏类型降级 200 + ok=False")


def test_capability_doesnt_clobber_existing_endpoint():
    n1 = cap_mod.register_all()
    n2 = cap_mod.register_all()
    assert n1 == n2 == 4, f"register not idempotent: n1={n1} n2={n2}"
    print("✓ register_all() idempotent: 两次均返回 4")


def test_namespace_separation_from_public_apis():
    """api.* 和 api_cn.* 命名空间完全独立,互不干扰"""
    import prisir_work.public_apis_capabilities as cap_public
    pub_ids = {c["id"] for c in cap_public.PUBLIC_APIS_CAPABILITIES}
    cn_ids = {c["id"] for c in cap_mod.PUBLIC_APIS_CN_CAPABILITIES}
    overlap = pub_ids & cn_ids
    assert not overlap, f"命名空间重叠: {overlap}"
    print(f"✓ api.* 和 api_cn.* 命名空间隔离,无 id 重叠")


if __name__ == "__main__":
    test_module_imports_and_auto_registers()
    test_capability_ids_and_risk_levels()
    test_capability_endpoints_unique()
    test_keywords_chinese_priority()
    test_intent_summary_contains_exec_examples()
    test_handler_passes_args_and_returns_payload()
    test_handler_fallback_when_rpc_returns_error()
    test_handler_fallback_when_ext_bridge_missing()
    test_handler_fallback_when_bad_result_type()
    test_capability_doesnt_clobber_existing_endpoint()
    test_namespace_separation_from_public_apis()
    print("\n所有 11 组断言通过 ✅")