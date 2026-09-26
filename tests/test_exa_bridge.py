# -*- coding: utf-8 -*-
"""tests/test_exa_bridge.py — P3j T22-A Exa MCP 桥接测试。

8 个 mock case 覆盖:
  · exa_health 3 路(无 key + 有 key + key 失效)
  · exa_search 3 路(成功 + empty query + HTTP 错误)
  · exa_find_similar 1 路(成功)
  · exa_answer 1 路(成功)

所有 HTTP 调用都 mock,不需要真 EXA_API_KEY。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _patch_http_post(monkeypatch, side_effect):
    """monkeypatch exa_bridge._http_post 到给定 side_effect。

    side_effect 可以是 fn 或 list[fn]
    """
    if isinstance(side_effect, list):
        iter_func = iter(side_effect)
        def fn(path, payload, *, timeout=30.0):
            try:
                return next(iter_func)(path, payload, timeout=timeout)
            except StopIteration:
                return {"ok": False, "error": "no_more_responses"}
        monkeypatch.setattr("prisir_work.exa_bridge._http_post", fn)
    else:
        monkeypatch.setattr("prisir_work.exa_bridge._http_post", side_effect)


# ---------------------------------------------------------------------------
# 1. exa_health 无 key
# ---------------------------------------------------------------------------

def test_exa_health_no_key(monkeypatch):
    """EXA_API_KEY 未设 → installed=False,mode=missing_key。"""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    from prisir_work import exa_bridge as _ex
    h = _ex.exa_health()
    assert h["ok"] is True
    assert h["installed"] is False
    assert h["key_set"] is False
    assert h["mode"] == "missing_key"
    assert "EXA_API_KEY" in h["hint"]


# ---------------------------------------------------------------------------
# 2. exa_health 有 key + 探活成功
# ---------------------------------------------------------------------------

def test_exa_health_ok(monkeypatch):
    """key 在 + ping 返 searchTime → mode=live。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key-7232f072-xyz")
    def fake_post(path, payload, *, timeout=30.0):
        return {"ok": True,
                "data": {"requestId": "abc",
                         "results": [], "searchTime": 123.4}}
    _patch_http_post(monkeypatch, fake_post)
    from prisir_work import exa_bridge as _ex
    h = _ex.exa_health()
    assert h["installed"] is True
    assert h["mode"] == "live"
    assert h["key_prefix"].startswith("test-key")
    assert h["search_time_ms"] == 123.4


# ---------------------------------------------------------------------------
# 3. exa_health INVALID_API_KEY
# ---------------------------------------------------------------------------

def test_exa_health_invalid_key(monkeypatch):
    """key 在但 Exa 返 INVALID_API_KEY → mode=key_invalid。"""
    monkeypatch.setenv("EXA_API_KEY", "bad-key-xxxxx")
    def fake_post(path, payload, *, timeout=30.0):
        return {"ok": False, "status": 401,
                "error": "exa_http_401",
                "tag": "INVALID_API_KEY",
                "detail": "API key invalid"}
    _patch_http_post(monkeypatch, fake_post)
    from prisir_work import exa_bridge as _ex
    h = _ex.exa_health()
    assert h["installed"] is True
    assert h["mode"] == "key_invalid"
    assert "INVALID_API_KEY" in h.get("warning", "")


# ---------------------------------------------------------------------------
# 4. exa_search 成功
# ---------------------------------------------------------------------------

def test_exa_search_ok(monkeypatch):
    """mock /search 返 results → exa_search 返 [{url, title, snippet}]。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    def fake_post(path, payload, *, timeout=30.0):
        return {"ok": True,
                "data": {
                    "requestId": "abc",
                    "results": [
                        {"url": "https://example.com/a",
                         "title": "Article A",
                         "text": "Body text for A",
                         "highlights": ["highlight1", "highlight2"],
                         "publishedDate": "2026-09-20",
                         "author": "Alice"},
                        {"url": "https://example.com/b",
                         "title": "Article B",
                         "text": "Body B",
                         "highlights": [],
                         "publishedDate": "",
                         "author": ""},
                    ],
                    "costDollars": {"total": 0.005,
                                    "search": {"neural": 0.005}},
                    "searchTime": 456.7,
                }}
    _patch_http_post(monkeypatch, fake_post)
    from prisir_work import exa_bridge as _ex
    r = _ex.exa_search("test query", num_results=5)
    assert r["ok"] is True
    assert r["query"] == "test query"
    assert len(r["results"]) == 2
    assert r["results"][0]["url"] == "https://example.com/a"
    # snippet 应优先 highlights
    assert "highlight1" in r["results"][0]["snippet"]
    assert r["cost_dollars"]["total"] == 0.005
    assert r["search_time_ms"] == 456.7
    assert r["sources"] == ["exa_search"] * 2


# ---------------------------------------------------------------------------
# 5. exa_search 空 query
# ---------------------------------------------------------------------------

def test_exa_search_empty_query(monkeypatch):
    """空 query → ok=False,error=empty_query(不调 HTTP)。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    called = []
    def fake_post(path, payload, *, timeout=30.0):
        called.append(1)
        return {"ok": True, "data": {"results": []}}
    _patch_http_post(monkeypatch, fake_post)
    from prisir_work import exa_bridge as _ex
    r = _ex.exa_search("")
    assert r["ok"] is False
    assert r["error"] == "empty_query"
    assert called == [], "空 query 不应调 HTTP"


# ---------------------------------------------------------------------------
# 6. exa_search HTTP 错误
# ---------------------------------------------------------------------------

def test_exa_search_http_error(monkeypatch):
    """Exa 返 429 RATE_LIMIT_EXCEEDED → ok=False + tag。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    def fake_post(path, payload, *, timeout=30.0):
        return {"ok": False, "status": 429,
                "error": "exa_http_429",
                "tag": "RATE_LIMIT_EXCEEDED",
                "detail": "limit exceeded"}
    _patch_http_post(monkeypatch, fake_post)
    from prisir_work import exa_bridge as _ex
    r = _ex.exa_search("x", num_results=3)
    assert r["ok"] is False
    assert r["status"] == 429
    assert r["tag"] == "RATE_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------
# 7. exa_find_similar 成功
# ---------------------------------------------------------------------------

def test_exa_find_similar_ok(monkeypatch):
    """mock /findSimilar → exa_find_similar 返 list。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    def fake_post(path, payload, *, timeout=30.0):
        return {"ok": True,
                "data": {
                    "results": [
                        {"url": "https://example.com/similar1",
                         "title": "Similar 1",
                         "text": "text content"}
                    ],
                    "costDollars": {"total": 0.003,
                                    "search": {"neural": 0.003}},
                    "searchTime": 200.0,
                }}
    _patch_http_post(monkeypatch, fake_post)
    from prisir_work import exa_bridge as _ex
    r = _ex.exa_find_similar("https://example.com/origin", num_results=3)
    assert r["ok"] is True
    assert len(r["results"]) == 1
    assert r["results"][0]["url"] == "https://example.com/similar1"
    assert r["sources"] == ["exa_find_similar"]


# ---------------------------------------------------------------------------
# 8. exa_answer 成功
# ---------------------------------------------------------------------------

def test_exa_answer_ok(monkeypatch):
    """mock /answer → exa_answer 返 answer + citations。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    def fake_post(path, payload, *, timeout=30.0):
        return {"ok": True,
                "data": {
                    "answer": "Yes, Claude Code has built-in /review.",
                    "citations": [
                        {"url": "https://docs.anthropic.com/claude-code",
                         "title": "Claude Code Docs"}
                    ],
                    "costDollars": {"total": 0.01},
                    "searchTime": 1234.5,
                }}
    _patch_http_post(monkeypatch, fake_post)
    from prisir_work import exa_bridge as _ex
    r = _ex.exa_answer("Does Claude Code have built-in /review?")
    assert r["ok"] is True
    assert "Claude Code" in r["answer"]
    assert len(r["citations"]) == 1
    assert "docs.anthropic.com" in r["citations"][0]["url"]


# ---------------------------------------------------------------------------
# 9. exa_search numResults 上限截断
# ---------------------------------------------------------------------------

def test_exa_search_num_results_clamped(monkeypatch):
    """numResults=999 → 上限 100,传给 API。"""
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    captured = {}
    def fake_post(path, payload, *, timeout=30.0):
        captured["payload"] = payload
        return {"ok": True, "data": {"results": [],
                                     "costDollars": {},
                                     "searchTime": 0}}
    _patch_http_post(monkeypatch, fake_post)
    from prisir_work import exa_bridge as _ex
    r = _ex.exa_search("x", num_results=999)
    assert captured["payload"]["numResults"] == 100


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))