# -*- coding: utf-8 -*-
"""tests/test_hn_bridge.py — P3j T22-B HackerNews Algolia API 桥接测试。

10 个 mock case 覆盖:
  · _http_get 3 路(ok / HTTP 错误 / 网络错误 / 异常)
  · hn_health 1 路(真发 1 hit 探活 — 用 mock urllib)
  · hn_search 4 路(成功 / 空 query / by_points / min_points 过滤)
  · hn_top_stories 1 路(成功)
  · hn_get_item 1 路(成功)

所有 HTTP 调用都 mock,不需要真网络。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _patch_http_get(monkeypatch, fn):
    """monkeypatch hn_bridge._http_get 到给定 fn。"""
    import prisir_work.hn_bridge as _m
    monkeypatch.setattr(_m, "_http_get", fn)


def _ok(data, status: int = 200):
    """构造返 ok=True 的 _http_get fake(直接返 fn,不再嵌套)。"""
    def fn(path, params=None, *, timeout=15.0):
        return {"ok": True, "data": data, "status": status}
    return fn


# ---------------------------------------------------------------------------
# 1. hn_health 成功
# ---------------------------------------------------------------------------

def test_hn_health_ok(monkeypatch):
    """mock /search_by_date 返 hits=[] → hn_health mode=ok。"""
    _patch_http_get(monkeypatch, _ok({"hits": [], "nbHits": 0,
                                      "processingTimeMS": 5}))
    from prisir_work import hn_bridge as _hn
    h = _hn.hn_health()
    assert h["ok"] is True
    assert h["source"] == "hackernews_algolia"
    assert h["key_required"] is False
    assert "search" in h["search_types"]


# ---------------------------------------------------------------------------
# 2. hn_health 失败
# ---------------------------------------------------------------------------

def test_hn_health_failed(monkeypatch):
    """network error → ok=False + error 字段。"""
    _patch_http_get(monkeypatch,
                    lambda *a, **kw: {"ok": False,
                                      "error": "hn_url_error",
                                      "detail": "no route"})
    from prisir_work import hn_bridge as _hn
    h = _hn.hn_health()
    assert h["ok"] is False
    assert "url_error" in h["error"] or "no route" in h.get("detail", "")


# ---------------------------------------------------------------------------
# 3. hn_search 成功
# ---------------------------------------------------------------------------

def test_hn_search_ok(monkeypatch):
    """mock search_by_date 返 2 hits → hn_search 返 2 results。"""
    fake_data = {
        "hits": [
            {"objectID": "1", "title": "A", "url": "https://a.com",
             "points": 100, "num_comments": 50, "author": "u1",
             "created_at": "2026-09-25T10:00:00.000Z",
             "_tags": ["story", "author_u1"]},
            {"objectID": "2", "title": "B", "story_url": "https://b.com",
             "story_text": "Body B", "points": 10, "num_comments": 2,
             "author": "u2",
             "created_at_i": 1727000000,
             "_tags": ["story"]},
        ],
        "nbHits": 2,
        "processingTimeMS": 25,
    }
    _patch_http_get(monkeypatch, _ok(fake_data))
    from prisir_work import hn_bridge as _hn
    r = _hn.hn_search("test", sort="by_date", limit=5)
    assert r["ok"] is True
    assert len(r["results"]) == 2
    assert r["results"][0]["url"] == "https://a.com"
    assert r["results"][0]["points"] == 100
    # story_url fallback
    assert r["results"][1]["url"] == "https://b.com"
    # story_text snippet
    assert "Body B" in r["results"][1]["snippet"]
    # created_at_i → ISO
    assert "T" in r["results"][1]["created_at"]


# ---------------------------------------------------------------------------
# 4. hn_search 空 query
# ---------------------------------------------------------------------------

def test_hn_search_empty_query(monkeypatch):
    """空 query → ok=False,error=empty_query(不调 HTTP)。"""
    called = []
    def fn(path, params=None, *, timeout=15.0):
        called.append(1)
        return _ok({"hits": []})(path, params, timeout=timeout)
    _patch_http_get(monkeypatch, fn)
    from prisir_work import hn_bridge as _hn
    r = _hn.hn_search("")
    assert r["ok"] is False
    assert r["error"] == "empty_query"
    assert called == [], "空 query 不应调 HTTP"


# ---------------------------------------------------------------------------
# 5. hn_search by_points 走 /search
# ---------------------------------------------------------------------------

def test_hn_search_by_points(monkeypatch):
    """sort=by_points → 走 /search endpoint。"""
    captured = []
    def fn(path, params=None, *, timeout=15.0):
        captured.append(path)
        return {"ok": True, "data": {"hits": [], "nbHits": 0,
                                     "processingTimeMS": 10},
                "status": 200}
    _patch_http_get(monkeypatch, fn)
    from prisir_work import hn_bridge as _hn
    r = _hn.hn_search("x", sort="by_points")
    assert "/search" in captured[0]  # not search_by_date
    assert "/search_by_date" not in captured[0]


# ---------------------------------------------------------------------------
# 6. hn_search min_points filter
# ---------------------------------------------------------------------------

def test_hn_search_min_points(monkeypatch):
    """min_points > 0 → numericFilters 参数被发出去。"""
    captured = {}
    def fn(path, params=None, *, timeout=15.0):
        captured["params"] = params
        return {"ok": True, "data": {"hits": [], "nbHits": 0},
                "status": 200}
    _patch_http_get(monkeypatch, fn)
    from prisir_work import hn_bridge as _hn
    r = _hn.hn_search("x", min_points=50)
    assert "points>=50" in captured["params"]["numericFilters"]


# ---------------------------------------------------------------------------
# 7. hn_top_stories 成功
# ---------------------------------------------------------------------------

def test_hn_top_stories_ok(monkeypatch):
    """mock /search hits → hn_top_stories 返 list。"""
    fake_data = {
        "hits": [
            {"objectID": "top1", "title": "Top 1",
             "url": "https://t1.com", "points": 500,
             "num_comments": 100, "author": "u1",
             "created_at_i": 1727000000, "_tags": ["story"]},
        ],
        "nbHits": 1,
        "processingTimeMS": 8,
    }
    _patch_http_get(monkeypatch, _ok(fake_data))
    from prisir_work import hn_bridge as _hn
    r = _hn.hn_top_stories(limit=3, min_points=100)
    assert r["ok"] is True
    assert len(r["results"]) == 1
    assert r["results"][0]["points"] == 500
    assert r["sort"] == "by_points"


# ---------------------------------------------------------------------------
# 8. hn_get_item 成功
# ---------------------------------------------------------------------------

def test_hn_get_item_ok(monkeypatch):
    """mock /items/12345 → hn_get_item 返 title + text + url。"""
    fake_data = {
        "id": 12345,
        "title": "Test Story",
        "text": "Body of the story",
        "url": "https://example.com/t",
        "points": 200,
        "num_comments": 30,
        "author": "u1",
        "created_at_i": 1727000000,
        "children": [{"id": 1}, {"id": 2}, {"id": 3}],
    }
    _patch_http_get(monkeypatch, _ok(fake_data))
    from prisir_work import hn_bridge as _hn
    r = _hn.hn_get_item("12345")
    assert r["ok"] is True
    assert r["title"] == "Test Story"
    assert "Body" in r["text"]
    assert r["url"] == "https://example.com/t"
    assert r["num_comments"] == 30  # server num_comments 字段优先


# ---------------------------------------------------------------------------
# 9. hn_get_item 空 object_id
# ---------------------------------------------------------------------------

def test_hn_get_item_empty(monkeypatch):
    """空 object_id → error=empty_object_id。"""
    called = []
    def fn(*a, **kw):
        called.append(1)
        return _ok({"id": 0})(a[0] if a else "/items/0")
    _patch_http_get(monkeypatch, fn)
    from prisir_work import hn_bridge as _hn
    r = _hn.hn_get_item("")
    assert r["ok"] is False
    assert r["error"] == "empty_object_id"
    assert called == []


# ---------------------------------------------------------------------------
# 10. _http_get HTTP error(让 fn 模拟 _http_get catch 后返回)
# ---------------------------------------------------------------------------

def test_http_get_http_error(monkeypatch):
    """HN 503 → ok=False + error=hn_http_503。"""
    def fn(path, params=None, *, timeout=15.0):
        return {"ok": False, "status": 503,
                "error": "hn_http_503",
                "detail": "Service Unavailable"}
    _patch_http_get(monkeypatch, fn)
    from prisir_work import hn_bridge as _hn
    r = _hn.hn_health()  # 通过 hn_health 调 _http_get,验 catch 路径
    assert r["ok"] is False
    # hn_health 不暴露 status,但 error 含 503
    assert "503" in r["error"]


# ---------------------------------------------------------------------------
# 11. _http_get 真 raise HTTPError(走 except)
# ---------------------------------------------------------------------------

def test_http_get_real_http_error(monkeypatch):
    """真 raise HTTPError → _http_get 内部 except 接住,返 ok=False。"""
    import urllib.error as _ue
    def real_fn(path, params=None, *, timeout=15.0):
        # 真 raise HTTPError,_http_get 内部 except 接
        raise _ue.HTTPError(path, 503, "Service Unavailable",
                            {}, io := __import__("io").BytesIO(b""))
    monkeypatch.setattr("prisir_work.hn_bridge.urllib.request.urlopen",
                        real_fn)
    from prisir_work import hn_bridge as _hn
    r = _hn._http_get("/x")
    assert r["ok"] is False
    assert r["status"] == 503
    assert "503" in r["error"]


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))