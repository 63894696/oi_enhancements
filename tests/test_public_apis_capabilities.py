"""test_public_apis_capabilities.py — Phase C capability 层测试

跑法:
  pytest tests/test_public_apis_capabilities.py -v

测什么:
- 4 capability 元数据完整性(id/title/endpoint/method/risk/auth/keywords)
- 4 endpoint 注册到 _REGISTRY
- handler 透传扩展 result(用 monkeypatch 替换 _ext_rpc_call)
- 扩展不可达时降级 200 + ok=False
- intent_summary 含 4 EXEC 写法示例
"""
from __future__ import annotations

import importlib
from unittest.mock import patch

# 关键:让 register_all() 自动跑注册到全局 _REGISTRY
import prisir_work.public_apis_capabilities as cap_mod


def test_module_imports_and_auto_registers():
    """import 副作用:register_all() 跑了,且能力清单齐全"""
    assert hasattr(cap_mod, "PUBLIC_APIS_CAPABILITIES")
    assert hasattr(cap_mod, "intent_summary")
    assert hasattr(cap_mod, "register_all")
    assert hasattr(cap_mod, "EXT_ID")
    assert cap_mod.EXT_ID == "public-apis-promo"
    assert len(cap_mod.PUBLIC_APIS_CAPABILITIES) == 4, \
        f"expect 4 capability, got {len(cap_mod.PUBLIC_APIS_CAPABILITIES)}"
    print(f"✓ module imports ok + EXT_ID={cap_mod.EXT_ID} + {len(cap_mod.PUBLIC_APIS_CAPABILITIES)} capability registered")


def test_capability_ids_and_risk_levels():
    expected_ids = {"api.find", "api.list_categories", "api.detail", "api.random"}
    actual = {c["id"] for c in cap_mod.PUBLIC_APIS_CAPABILITIES}
    assert actual == expected_ids, f"cap ids: {actual}"
    # 全部 L0(读本地 JSON,符合 ship 中「不出图不发外」红线)
    risks = {c["risk"] for c in cap_mod.PUBLIC_APIS_CAPABILITIES}
    assert risks == {"L0"}, f"all risk should be L0, got {risks}"
    print(f"✓ 4 capability ids 全在 + risk 全 L0")


def test_capability_endpoints_unique():
    eps = [c["endpoint"] for c in cap_mod.PUBLIC_APIS_CAPABILITIES]
    assert len(set(eps)) == 4, f"endpoint duplicate: {eps}"
    # endpoint 必须以 /api/ 开头
    for ep in eps:
        assert ep.startswith("/api/"), f"endpoint should start with /api/: {ep}"
    print(f"✓ 4 endpoint 唯一 + 全是 /api/ 前缀: {eps}")


def test_keywords_nonempty_strings():
    for c in cap_mod.PUBLIC_APIS_CAPABILITIES:
        kw = c["keywords"]
        assert isinstance(kw, (tuple, list)), f"keywords not iterable: {type(kw)}"
        assert len(kw) >= 3, f"keywords too few for {c['id']}: {len(kw)}"
        for k in kw:
            assert isinstance(k, str) and k.strip(), f"empty keyword in {c['id']}"
    print(f"✓ 所有 capability keywords 非空 + ≥3 条")


def test_intent_summary_contains_exec_examples():
    summary = cap_mod.intent_summary()
    # 4 个 EXEC 写法示例必须全在
    for must in ("api.find", "api.list_categories", "api.detail", "api.random"):
        assert must in summary, f"intent_summary 不含 {must}"
    # EXEC 标记协议
    assert "[[EXEC:" in summary
    # 关键词要点
    assert "API" in summary
    # 风险提示
    assert "免费" in summary or "快照" in summary
    # 中文为主
    assert len(summary) >= 400, f"intent_summary 太短: {len(summary)} 字符"
    print(f"✓ intent_summary 长度={len(summary)} 字符,含 4 EXEC 示例")


def test_handler_passes_args_and_returns_payload():
    """handle 透传 type/html/meta,不做翻译"""
    captured = {}

    def fake_rpc(ext_id, m, body, timeout=4.0):
        captured["ext_id"] = ext_id
        captured["method"] = m
        captured["body"] = body
        captured["timeout"] = timeout
        return {"ok": True, "result": {"type": "card", "html": "<i>x</i>", "meta": {"n": 3}}}

    with patch.dict("sys.modules", {"prisiragent_web": type("_M", (), {"_ext_rpc_call": staticmethod(fake_rpc)})()}):
        handler = cap_mod._make_handler("api.find")
        result, status = handler({"query": "weather", "limit": 5})
        assert status == 200
        assert result["ok"] is True
        assert result["type"] == "card"
        assert result["html"] == "<i>x</i>"
        assert result["meta"] == {"n": 3}
    # 验证 _ext_rpc_call 入参
    assert captured["ext_id"] == "public-apis-promo", f"ext_id={captured['ext_id']}"
    assert captured["method"] == "find", f"method={captured['method']}"
    assert captured["body"] == {"query": "weather", "limit": 5}
    assert captured["timeout"] == 4.0
    print(f"✓ handler 透传 + ext_id={captured['ext_id']} method={captured['method']} timeout={captured['timeout']}")


def test_handler_fallback_when_rpc_returns_error():
    """扩展不可达 → 200 + ok=False + warning"""

    def fake_rpc(ext_id, m, body, timeout=4.0):
        return {"ok": False, "error": "ext subprocess not running"}

    with patch.dict("sys.modules", {"prisiragent_web": type("_M", (), {"_ext_rpc_call": staticmethod(fake_rpc)})()}):
        handler = cap_mod._make_handler("api.find")
        result, status = handler({"query": "weather"})
        assert status == 200
        assert result["ok"] is False
        assert "error" in result
        assert result.get("warning") == "ext_unavailable"
    print("✓ handler 扩展不可达降级 200 + ok=False + warning")


def test_handler_fallback_when_ext_bridge_missing():
    """prisIragent_web 不存在 → 200 + ok=False"""
    import builtins, sys
    saved = sys.modules.pop("prisiragent_web", None)
    try:
        handler = cap_mod._make_handler("api.detail")
        result, status = handler({"name": "GitHub"})
        assert status == 200
        assert result["ok"] is False
        assert "ext_bridge_unavailable" in result.get("error", "")
    finally:
        if saved is not None:
            sys.modules["prisiragent_web"] = saved
    print("✓ handler ext_bridge 缺失降级 200 + ok=False")


def test_handler_fallback_when_bad_result_type():
    """扩展返非 dict → 200 + ok=False"""

    def fake_rpc(ext_id, m, body, timeout=4.0):
        return ["bad", "list", "shape"]   # 错误:不是 dict

    with patch.dict("sys.modules", {"prisiragent_web": type("_M", (), {"_ext_rpc_call": staticmethod(fake_rpc)})()}):
        handler = cap_mod._make_handler("api.random")
        result, status = handler({})
        assert status == 200
        assert result["ok"] is False
    print("✓ handler 返坏类型降级 200 + ok=False")


def test_capability_doesnt_clobber_existing_endpoint():
    """idempotent:再次 register_all() 不抛 KeyError"""
    n1 = cap_mod.register_all()
    n2 = cap_mod.register_all()
    assert n1 == n2 == 4, f"register not idempotent: n1={n1} n2={n2}"
    print(f"✓ register_all() idempotent: 两次均返回 4")


if __name__ == "__main__":
    test_module_imports_and_auto_registers()
    test_capability_ids_and_risk_levels()
    test_capability_endpoints_unique()
    test_keywords_nonempty_strings()
    test_intent_summary_contains_exec_examples()
    test_handler_passes_args_and_returns_payload()
    test_handler_fallback_when_rpc_returns_error()
    test_handler_fallback_when_ext_bridge_missing()
    test_handler_fallback_when_bad_result_type()
    test_capability_doesnt_clobber_existing_endpoint()
    print("\n所有 10 组断言通过 ✅")