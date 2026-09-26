# -*- coding: utf-8 -*-
"""tests/test_hn_endpoints.py — P3j T22-B HackerNews endpoint + capability + provider 测试。

9 个 case:
  · endpoint 5 个(health/search/top/item + 缺字段)
  · capability 1 个(4 capability 已注册)
  · provider 1 个(hn_search 始终注册)
  · 集成 2 个(health 走完整路径 + search 走 monkeypatch bridge)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. endpoint /web/hn/health 集成
# ---------------------------------------------------------------------------

def test_web_hn_health_endpoint(monkeypatch):
    """mock hn_bridge.hn_health 返 ok=True → endpoint 透传。"""
    monkeypatch.setattr("prisir_work.hn_bridge.hn_health",
                        lambda: {"ok": True, "source": "hackernews_algolia",
                                 "version": "v1",
                                 "url": "https://hn.algolia.com/api/v1",
                                 "key_required": False,
                                 "search_types": ("search",
                                                  "search_by_date"),
                                 "rate_limit": "no_auth_required"})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_hn_health({})
    assert status == 200
    assert body["ok"] is True
    assert body["key_required"] is False


# ---------------------------------------------------------------------------
# 2. endpoint /web/hn/search 集成
# ---------------------------------------------------------------------------

def test_web_hn_search_endpoint(monkeypatch):
    """mock hn_bridge.hn_search → endpoint 透传。"""
    monkeypatch.setattr("prisir_work.hn_bridge.hn_search",
                        lambda q, **kw: {"ok": True, "query": q,
                                         "results": [
                                             {"url": "https://x/a",
                                              "title": "A",
                                              "snippet": "snip",
                                              "points": 100,
                                              "num_comments": 30}],
                                         "sources": ["hackernews"],
                                         "total_hits": 1,
                                         "search_time_ms": 20})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_hn_search({"query": "test", "limit": 5})
    assert status == 200
    assert body["ok"] is True
    assert len(body["results"]) == 1
    assert body["query"] == "test"


# ---------------------------------------------------------------------------
# 3. endpoint /web/hn/search 空 query
# ---------------------------------------------------------------------------

def test_web_hn_search_empty_query():
    """空 query → error=empty_query。"""
    from prisir_work import endpoints as _ep
    body, status = _ep._web_hn_search({"query": ""})
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "empty_query"


# ---------------------------------------------------------------------------
# 4. endpoint /web/hn/top 集成
# ---------------------------------------------------------------------------

def test_web_hn_top_endpoint(monkeypatch):
    """mock hn_top_stories → endpoint 透传。"""
    monkeypatch.setattr("prisir_work.hn_bridge.hn_top_stories",
                        lambda **kw: {"ok": True,
                                      "sort": "by_points",
                                      "results": [
                                          {"url": "https://t/1",
                                           "title": "Top 1",
                                           "points": 999,
                                           "num_comments": 200}],
                                      "sources": ["hackernews"]})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_hn_top({"limit": 3})
    assert status == 200
    assert body["ok"] is True
    assert body["results"][0]["points"] == 999


# ---------------------------------------------------------------------------
# 5. endpoint /web/hn/item 集成
# ---------------------------------------------------------------------------

def test_web_hn_item_endpoint(monkeypatch):
    """mock hn_get_item → endpoint 透传。"""
    monkeypatch.setattr("prisir_work.hn_bridge.hn_get_item",
                        lambda oid, **kw: {"ok": True, "object_id": oid,
                                           "title": "Story", "text": "Body",
                                           "url": "https://example.com/x",
                                           "points": 200,
                                           "num_comments": 30})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_hn_item({"object_id": "12345"})
    assert status == 200
    assert body["ok"] is True
    assert body["object_id"] == "12345"
    assert body["title"] == "Story"


# ---------------------------------------------------------------------------
# 6. endpoint /web/hn/item 空 object_id
# ---------------------------------------------------------------------------

def test_web_hn_item_empty():
    """空 object_id → error=empty_object_id。"""
    from prisir_work import endpoints as _ep
    body, status = _ep._web_hn_item({"object_id": ""})
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "empty_object_id"


# ---------------------------------------------------------------------------
# 7. capability 4 个 web.hn.* 已注册
# ---------------------------------------------------------------------------

def test_capability_registered():
    """web.hn.{health,search,top,item} 4 个已注册。"""
    from prisir_work import capability as _cap
    caps = {c["id"] for c in _cap.list_capabilities()}
    for cid in ("web.hn.health", "web.hn.search",
                "web.hn.top", "web.hn.item"):
        assert cid in caps, f"{cid} missing"


# ---------------------------------------------------------------------------
# 8. provider hn_search 始终注册
# ---------------------------------------------------------------------------

def test_search_provider_always_registered():
    """hn_search provider 不依赖 env,始终注册。"""
    import importlib
    import prisir_work.web_search as _ws
    importlib.reload(_ws)
    providers = list(_ws._PROVIDERS.keys())
    assert "hn_search" in providers, \
        f"hn_search missing from _PROVIDERS,当前: {providers}"


# ---------------------------------------------------------------------------
# 9. endpoint 4 个 web.hn.* 已注册
# ---------------------------------------------------------------------------

def test_endpoints_registered():
    """4 个 hn endpoint 在 _REGISTRY。"""
    from prisir_work import endpoints as _ep
    expected = ("/web/hn/health", "/web/hn/search",
                "/web/hn/top", "/web/hn/item")
    for p in expected:
        assert p in _ep._REGISTRY, f"{p} missing"


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))