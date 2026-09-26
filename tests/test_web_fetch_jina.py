# -*- coding: utf-8 -*-
"""tests/test_web_fetch_jina.py — P3j T20-I jina reader/search fetcher 测试。

8 个 mock case 覆盖:
  · jina_fetch 4 路(成功/4xx/超时/自部署 URL)
  · jina_search 2 路(成功 JSON/空 query)
  · jina_health 2 路(hosted only / 两端均 fail)
  · picker loop(jina 优先于 urllib,web_fetch 集成回归)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. jina_fetch 4 路
# ---------------------------------------------------------------------------

def test_jina_fetch_ok(monkeypatch):
    """mock _http_get 返 200 + markdown → jina_fetch 返 ok + content。"""
    from prisir_work import web_fetch_jina as _jina

    md = ("Title: Example Domain\n"
          "URL Source: https://example.com/\n"
          "\nThis domain is for use in illustrative examples in documents.")
    monkeypatch.setattr(
        _jina, "_http_get",
        lambda *a, **kw: {"ok": True, "status": 200,
                          "content": md, "headers": {}},
    )
    r = _jina.jina_fetch("https://example.com", {"timeout": 5.0})
    assert r["meta"]["fetcher"] == "jina"
    assert r["meta"]["ok"] is True
    assert r["meta"]["status"] == 200
    assert r["meta"]["format"] == "markdown"
    assert r["meta"]["mode"] in ("hosted", "self_hosted")
    assert "Example Domain" in r["content"]
    assert "illustrative" in r["content"]


def test_jina_fetch_http_error(monkeypatch):
    """mock _http_get 返 404 → meta.error 出现且 ok=False。"""
    from prisir_work import web_fetch_jina as _jina

    monkeypatch.setattr(
        _jina, "_http_get",
        lambda *a, **kw: {"ok": False, "status": 404,
                          "error": "http_404", "content": ""},
    )
    r = _jina.jina_fetch("https://nonexistent.test/abc")
    assert r["meta"]["ok"] is False
    assert r["meta"]["error"] == "http_404"
    assert r["meta"]["status"] == 404
    assert r["content"] == ""


def test_jina_fetch_timeout(monkeypatch):
    """mock _http_get 返 url_error(模拟超时 / DNS 失败)→ 失败结构。"""
    from prisir_work import web_fetch_jina as _jina

    monkeypatch.setattr(
        _jina, "_http_get",
        lambda *a, **kw: {"ok": False, "status": 0,
                          "error": "url_error",
                          "detail": "timed out",
                          "content": ""},
    )
    r = _jina.jina_fetch("https://very-slow.test/")
    assert r["meta"]["ok"] is False
    assert r["meta"]["error"] == "url_error"
    assert r["meta"]["detail"] == "timed out"
    assert r["meta"]["mode"] in ("hosted", "self_hosted")


def test_jina_fetch_self_hosted(monkeypatch):
    """JINA_READER_URL 设了 → _reader_base 走自部署 + mode=self_hosted。"""
    from prisir_work import web_fetch_jina as _jina

    saved = _jina.JINA_READER_URL
    _jina.JINA_READER_URL = "http://localhost:8081"
    try:
        captured = {}
        def fake(url, *, timeout=30.0, headers=None, params=None):
            captured["url"] = url
            return {"ok": True, "status": 200,
                    "content": "self-hosted markdown", "headers": {}}
        monkeypatch.setattr(_jina, "_http_get", fake)
        r = _jina.jina_fetch("https://example.com")
        assert r["meta"]["mode"] == "self_hosted"
        assert r["meta"]["ok"] is True
        assert captured["url"].startswith("http://localhost:8081/")
    finally:
        _jina.JINA_READER_URL = saved


# ---------------------------------------------------------------------------
# 2. jina_search 2 路
# ---------------------------------------------------------------------------

def test_jina_search_ok(monkeypatch):
    """mock _http_get 返 {data: [{url, title, content}]} → 列表带 url/title/snippet。"""
    from prisir_work import web_fetch_jina as _jina

    payload = {
        "data": [
            {"url": "https://a.test/", "title": "Result A",
             "content": "long body A"},
            {"url": "https://b.test/", "title": "Result B",
             "content": "long body B"},
        ]
    }
    monkeypatch.setattr(
        _jina, "_http_get",
        lambda *a, **kw: {"ok": True, "status": 200,
                          "content": json.dumps(payload), "headers": {}},
    )
    out = _jina.jina_search("hello world", limit=5)
    assert len(out) == 2
    assert out[0]["url"] == "https://a.test/"
    assert out[0]["title"] == "Result A"
    assert "long body A" in out[0]["content"]
    # snippet = description | content 前 300
    assert out[0]["snippet"] == "long body A"


def test_jina_search_empty_query():
    """空 query → []。"""
    from prisir_work import web_fetch_jina as _jina
    assert _jina.jina_search("") == []
    assert _jina.jina_search("   ") == []


# ---------------------------------------------------------------------------
# 3. jina_health 2 路
# ---------------------------------------------------------------------------

def test_jina_health_hosted_ok(monkeypatch):
    """hosted:两端均 ok → ok=True, mode=hosted, reader/search 字典存在。"""
    from prisir_work import web_fetch_jina as _jina

    monkeypatch.setattr(
        _jina, "_http_get",
        lambda *a, **kw: {"ok": True, "status": 200, "content": "ok", "headers": {}},
    )
    h = _jina.jina_health()
    assert h["ok"] is True
    assert h["mode"] == "hosted"
    assert h["reader"]["ok"] is True
    assert h["search"]["ok"] is True


def test_jina_health_both_down(monkeypatch):
    """两端均失败 → ok=False,reader/search.error 存在。"""
    from prisir_work import web_fetch_jina as _jina

    def fake(*a, **kw):
        return {"ok": False, "status": 503,
                "error": "http_503", "content": ""}
    monkeypatch.setattr(_jina, "_http_get", fake)
    h = _jina.jina_health()
    assert h["ok"] is False
    assert h["reader"]["ok"] is False
    assert h["reader"]["error"] == "http_503"
    assert h["search"]["ok"] is False


# ---------------------------------------------------------------------------
# 4. picker loop 集成(web_fetch.fetch 优先 jina)
# ---------------------------------------------------------------------------

def test_picker_prefers_jina_over_urllib(monkeypatch):
    """两端都成功 → web_fetch.fetch 选 jina。"""
    from prisir_work import web_fetch as wf

    md = "Title: Jina Markdown\n\nbody text"
    def fake_jina(url, options):
        return {"content": md, "meta": {"fetcher": "jina", "ok": True}}
    def fake_urllib(url, options):
        return {"content": "<html>raw html</html>",
                "meta": {"fetcher": "http_urllib", "ok": True}}
    wf.register_fetcher("jina", fake_jina)
    wf.register_fetcher("http_urllib", fake_urllib)

    # 清缓存避免上次结果命中
    try:
        wf._mem_clear()
    except Exception:
        pass

    r = wf.fetch("https://example.com/",
                 options={"no_cache": True, "timeout": 5.0})
    assert r["ok"] is True
    assert r["fetcher"] == "jina"
    assert "Jina Markdown" in r["content"]
    # meta.fetcher 也被覆盖
    assert r["meta"]["fetcher"] == "jina"


def test_picker_falls_back_to_urllib_when_jina_fails(monkeypatch):
    """jina 失败 → urllib 兜底。"""
    from prisir_work import web_fetch as wf

    def fake_jina(url, options):
        return {"content": "", "meta": {"fetcher": "jina", "ok": False,
                                        "error": "http_404"}}
    def fake_urllib(url, options):
        return {"content": "<html>raw</html>",
                "meta": {"fetcher": "http_urllib", "ok": True}}
    wf.register_fetcher("jina", fake_jina)
    wf.register_fetcher("http_urllib", fake_urllib)

    try:
        wf._mem_clear()
    except Exception:
        pass

    r = wf.fetch("https://example.com/2",
                 options={"no_cache": True, "timeout": 5.0})
    assert r["ok"] is True
    assert r["fetcher"] == "http_urllib"
    assert "raw" in r["content"]


# ---------------------------------------------------------------------------
# 5. web_search provider 注册(jina_search 仅在 env 有 key/URL 时注册)
# ---------------------------------------------------------------------------

def test_jina_search_provider_registers_when_env(monkeypatch, tmp_path):
    """JINA_API_KEY 设了 → jina_search provider 进入注册表 + search() 命中它。"""
    import os
    from prisir_work import web_search as ws

    saved_key = os.environ.pop("JINA_API_KEY", "")
    saved_url = os.environ.pop("JINA_SEARCH_URL", "")
    try:
        os.environ["JINA_API_KEY"] = "test_key"
        ws.clear_cache()
        # 直接 register_provider,模拟模块级 if 的副作用
        ws.register_provider(
            "jina_search",
            lambda q, n=10: [
                {"url": "https://jina.example/r1",
                 "title": "jina result 1",
                 "snippet": "snip 1"},
            ],
        )
        r = ws.search("hi", limit=3)
        assert r, "search() 应至少返 1 条结果"
        assert any("jina_search" in (it.get("sources") or [])
                   for it in r), f"结果里没有 jina_search 来源: {r}"
    finally:
        os.environ.pop("JINA_API_KEY", None)
        if saved_key:
            os.environ["JINA_API_KEY"] = saved_key
        if saved_url:
            os.environ["JINA_SEARCH_URL"] = saved_url


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))
