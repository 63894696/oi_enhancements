# -*- coding: utf-8 -*-
"""tests/test_exa_endpoints.py — P3j T22-A Exa 端点 + capability + provider 测试。

11 个 case:
  · endpoint 5 个(health/search/find_similar/answer + 缺字段)
  · capability 1 个(4 capability 已注册)
  · provider 1 个(env EXA_API_KEY 在时 exa_search 注册)
  · 3 个 monkeypatch 集成(health/search/answer 走完整路径)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _patch_post(monkeypatch, fn):
    """monkeypatch exa_bridge._http_post 到给定 fn。"""
    monkeypatch.setattr("prisir_work.exa_bridge._http_post", fn)


def _ok_post(data):
    """构造一个返 ok 的 _http_post fake。"""
    def fn(path, payload, *, timeout=30.0):
        return {"ok": True, "data": data}
    return fn


# ---------------------------------------------------------------------------
# 1. endpoint /web/exa/health 集成
# ---------------------------------------------------------------------------

def test_web_exa_health_endpoint(monkeypatch):
    """mock exa_bridge.exa_health 返 live → endpoint 透传。"""
    monkeypatch.setattr("prisir_work.exa_bridge.exa_health",
                        lambda: {"ok": True, "installed": True,
                                 "key_set": True, "mode": "live",
                                 "key_prefix": "7232f072...",
                                 "search_time_ms": 800})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_exa_health({})
    assert status == 200
    assert body["ok"] is True
    assert body["installed"] is True
    assert body["mode"] == "live"


# ---------------------------------------------------------------------------
# 2. endpoint /web/exa/search 集成
# ---------------------------------------------------------------------------

def test_web_exa_search_endpoint(monkeypatch):
    """mock exa_bridge.exa_search → endpoint 透传。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    fake_data = {
        "results": [
            {"url": "https://example.com/a", "title": "A",
             "text": "body", "highlights": ["h1"]},
            {"url": "https://example.com/b", "title": "B",
             "text": "body", "highlights": []},
        ],
        "costDollars": {"total": 0.005},
        "searchTime": 250.0,
    }
    monkeypatch.setattr("prisir_work.exa_bridge.exa_search",
                        lambda q, **kw: {"ok": True, "query": q,
                                         "results": [
                                             {"url": "https://example.com/a",
                                              "title": "A",
                                              "snippet": "h1",
                                              "text": "body"},
                                             {"url": "https://example.com/b",
                                              "title": "B",
                                              "snippet": "body",
                                              "text": "body"},
                                         ],
                                         "sources": ["exa_search"] * 2,
                                         "cost_dollars": {"total": 0.005},
                                         "search_time_ms": 250.0})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_exa_search({"query": "test", "num_results": 5})
    assert status == 200
    assert body["ok"] is True
    assert body["query"] == "test"
    assert len(body["results"]) == 2
    assert body["sources"] == ["exa_search"] * 2


# ---------------------------------------------------------------------------
# 3. endpoint /web/exa/search 空 query
# ---------------------------------------------------------------------------

def test_web_exa_search_empty_query():
    """空 query → error=empty_query。"""
    from prisir_work import endpoints as _ep
    body, status = _ep._web_exa_search({"query": ""})
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "empty_query"


# ---------------------------------------------------------------------------
# 4. endpoint /web/exa/find_similar 集成
# ---------------------------------------------------------------------------

def test_web_exa_find_similar_endpoint(monkeypatch):
    """mock exa_bridge.exa_find_similar → endpoint 透传。"""
    monkeypatch.setattr("prisir_work.exa_bridge.exa_find_similar",
                        lambda u, **kw: {"ok": True, "url": u,
                                         "results": [{"url": "https://x/y",
                                                      "title": "Y",
                                                      "snippet": "snip",
                                                      "text": "body"}],
                                         "sources": ["exa_find_similar"],
                                         "cost_dollars": {"total": 0.003},
                                         "search_time_ms": 100.0})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_exa_find_similar({"url": "https://x",
                                              "num_results": 5})
    assert status == 200
    assert body["ok"] is True
    assert body["url"] == "https://x"
    assert len(body["results"]) == 1


# ---------------------------------------------------------------------------
# 5. endpoint /web/exa/find_similar 缺 url
# ---------------------------------------------------------------------------

def test_web_exa_find_similar_empty_url():
    """空 url → error=empty_url。"""
    from prisir_work import endpoints as _ep
    body, status = _ep._web_exa_find_similar({"url": ""})
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "empty_url"


# ---------------------------------------------------------------------------
# 6. endpoint /web/exa/answer 集成
# ---------------------------------------------------------------------------

def test_web_exa_answer_endpoint(monkeypatch):
    """mock exa_bridge.exa_answer → endpoint 透传。"""
    monkeypatch.setattr("prisir_work.exa_bridge.exa_answer",
                        lambda q, **kw: {"ok": True, "query": q,
                                         "answer": "Yes.",
                                         "citations": [
                                             {"url": "https://docs/a",
                                              "title": "A"}],
                                         "cost_dollars": {"total": 0.01},
                                         "search_time_ms": 1234.0})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_exa_answer({"query": "Does X have Y?"})
    assert status == 200
    assert body["ok"] is True
    assert body["answer"] == "Yes."
    assert len(body["citations"]) == 1


# ---------------------------------------------------------------------------
# 7. endpoint /web/exa/answer 空 query
# ---------------------------------------------------------------------------

def test_web_exa_answer_empty_query():
    """空 query → error=empty_query。"""
    from prisir_work import endpoints as _ep
    body, status = _ep._web_exa_answer({"query": ""})
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "empty_query"


# ---------------------------------------------------------------------------
# 8. capability 4 个 web.exa.* 已注册
# ---------------------------------------------------------------------------

def test_capability_registered():
    """web.exa.{health,search,find_similar,answer} 4 个已注册。"""
    from prisir_work import capability as _cap
    caps = {c["id"] for c in _cap.list_capabilities()}
    for cid in ("web.exa.health", "web.exa.search",
                "web.exa.find_similar", "web.exa.answer"):
        assert cid in caps, f"{cid} missing"


# ---------------------------------------------------------------------------
# 9. provider exa_search 注册(若 EXA_API_KEY env 在)
# ---------------------------------------------------------------------------

def test_search_provider_registered(monkeypatch):
    """EXA_API_KEY env 在 → exa_search provider 注册到 web_search。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    import importlib
    # 强制重新加载 web_search 让 provider 注册跑一遍
    import prisir_work.web_search as _ws
    importlib.reload(_ws)
    providers = list(_ws._PROVIDERS.keys())
    assert "exa_search" in providers, \
        f"exa_search missing from _PROVIDERS,当前: {providers}"


# ---------------------------------------------------------------------------
# 10. provider 没注册(EXA_API_KEY 不在)
# ---------------------------------------------------------------------------

def test_search_provider_not_registered_without_key(monkeypatch):
    """EXA_API_KEY 不在 → exa_search provider 不注册。"""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    import importlib
    import prisir_work.web_search as _ws
    importlib.reload(_ws)
    providers = list(_ws._PROVIDERS.keys())
    assert "exa_search" not in providers, \
        f"exa_search 误注册,_PROVIDERS: {providers}"


# ---------------------------------------------------------------------------
# 11. health 模式:expiry_key_invalid 也返 installed=True
# ---------------------------------------------------------------------------

def test_exa_health_key_invalid_mode(monkeypatch):
    """key 在但 Exa 返 INVALID_API_KEY → mode=key_invalid。"""
    monkeypatch.setenv("EXA_API_KEY", "bad-key")
    _patch_post(monkeypatch,
                lambda *a, **kw: {"ok": False, "status": 401,
                                 "error": "exa_http_401",
                                 "tag": "INVALID_API_KEY",
                                 "detail": "key invalid"})
    from prisir_work import exa_bridge as _ex
    h = _ex.exa_health()
    assert h["installed"] is True
    assert h["mode"] == "key_invalid"
    assert "INVALID_API_KEY" in h.get("warning", "")


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))