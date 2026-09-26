# -*- coding: utf-8 -*-
"""tests/test_web_fetch_feedparser.py — P3j T21-A feedparser 直接 fetcher 测试。

8 个 mock case 覆盖:
  · feedparser_fetch 4 路(成功 RSS / 304 Not Modified / 5xx / not_a_feed_url)
  · feedparser_fetch 2 路(bozo=True 解析成功 / 空 entries)
  · feedparser_health 1 路
  · picker 集成 1 路(web_fetch 优先选 feedparser)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. feedparser_fetch 成功 RSS
# ---------------------------------------------------------------------------

def test_feedparser_fetch_rss_ok(monkeypatch):
    """mock feedparser.parse → 返 RSS,3 条 entry → jina_fetch 返 ok + content。"""
    from prisir_work import web_fetch_feedparser as _fp

    class FakeFeed(dict):
        status = 200
        bozo = False
        bozo_exception = None
        feed = {"title": "Hacker News",
                "link": "https://news.ycombinator.com/",
                "subtitle": "links for the intellectually curious"}
        entries = [
            {"title": "Post A", "link": "https://example.com/a",
             "published": "2026-09-26T10:00:00Z",
             "author": "alice", "summary": "<p>summary A</p>"},
            {"title": "Post B", "link": "https://example.com/b",
             "published": "2026-09-26T11:00:00Z",
             "author": "bob", "summary": "<p>summary B</p>"},
            {"title": "Post C", "link": "https://example.com/c",
             "published": "2026-09-26T12:00:00Z",
             "author": "carol", "summary": "<p>summary C</p>"},
        ]

    monkeypatch.setattr(_fp.feedparser, "parse",
                        lambda *a, **kw: FakeFeed())

    r = _fp.feedparser_fetch("https://news.ycombinator.com/rss")
    assert r["meta"]["fetcher"] == "feedparser"
    assert r["meta"]["ok"] is True
    assert r["meta"]["status"] == 200
    assert r["meta"]["format"] == "markdown"
    assert r["meta"]["feed_title"] == "Hacker News"
    assert r["meta"]["item_count"] == 3
    assert r["meta"]["version"] == "6.0.14"
    # markdown 含 3 个 ## entries
    assert "Post A" in r["content"]
    assert "Post B" in r["content"]
    assert "Post C" in r["content"]
    assert "alice" in r["content"]
    # HTML 已被剥掉
    assert "<p>" not in r["content"]
    assert "summary A" in r["content"]


def test_feedparser_fetch_304_not_modified(monkeypatch):
    """mock status=304 → ok=False + error=not_modified。"""
    from prisir_work import web_fetch_feedparser as _fp

    class FakeFeed(dict):
        status = 304
        bozo = False
        bozo_exception = None
        feed = {}
        entries = []

    monkeypatch.setattr(_fp.feedparser, "parse",
                        lambda *a, **kw: FakeFeed())

    r = _fp.feedparser_fetch("https://example.com/feed.xml")
    assert r["meta"]["ok"] is False
    assert r["meta"]["status"] == 304
    assert r["meta"]["error"] == "not_modified"
    assert r["content"] == ""


def test_feedparser_fetch_500(monkeypatch):
    """mock status=500 → ok=False + error=http_500。"""
    from prisir_work import web_fetch_feedparser as _fp

    class FakeFeed(dict):
        status = 500
        bozo = True
        bozo_exception = "Internal Server Error"
        feed = {}
        entries = []

    monkeypatch.setattr(_fp.feedparser, "parse",
                        lambda *a, **kw: FakeFeed())

    r = _fp.feedparser_fetch("https://example.com/feed")
    assert r["meta"]["ok"] is False
    assert r["meta"]["status"] == 500
    assert r["meta"]["error"] == "http_500"


def test_feedparser_fetch_not_a_feed_url(monkeypatch):
    """不像 feed 的 URL → 提前返 not_a_feed_url(不调 feedparser.parse)。"""
    from prisir_work import web_fetch_feedparser as _fp

    called = []
    monkeypatch.setattr(_fp.feedparser, "parse",
                        lambda *a, **kw: called.append(1) or None)

    r = _fp.feedparser_fetch("https://example.com/blog/post-123")
    assert r["meta"]["error"] == "not_a_feed_url"
    assert r["meta"]["ok"] is False
    assert called == [], f"不应调 feedparser.parse,但调了 {len(called)} 次"


def test_feedparser_fetch_bozo_but_entries(monkeypatch):
    """feed XML 有错(bozo=True)但仍解析出 entries → 成功 + bozo 字段标记。"""
    from prisir_work import web_fetch_feedparser as _fp

    class FakeFeed(dict):
        status = 200
        bozo = True
        bozo_exception = "XML syntax error at line 3"
        feed = {"title": "Broken Feed"}
        entries = [
            {"title": "Survived entry",
             "link": "https://example.com/x",
             "summary": "ok"},
        ]

    monkeypatch.setattr(_fp.feedparser, "parse",
                        lambda *a, **kw: FakeFeed())

    r = _fp.feedparser_fetch("https://example.com/feed.xml")
    assert r["meta"]["ok"] is True
    assert r["meta"]["bozo"] is True
    assert "XML syntax error" in r["meta"]["bozo_exception"]
    assert "Survived entry" in r["content"]


def test_feedparser_fetch_empty_feed(monkeypatch):
    """status=200 但 entries 为空 → ok=False + error=empty_feed。"""
    from prisir_work import web_fetch_feedparser as _fp

    class FakeFeed(dict):
        status = 200
        bozo = False
        bozo_exception = None
        feed = {"title": "Empty Feed"}
        entries = []

    monkeypatch.setattr(_fp.feedparser, "parse",
                        lambda *a, **kw: FakeFeed())

    r = _fp.feedparser_fetch("https://example.com/feed")
    assert r["meta"]["ok"] is False
    assert r["meta"]["error"] == "empty_feed"
    assert r["meta"]["feed_title"] == "Empty Feed"


# ---------------------------------------------------------------------------
# 2. feedparser_health
# ---------------------------------------------------------------------------

def test_feedparser_health_version():
    """health 返 ok=True + version=6.0.14 + supports_* 全 True。"""
    from prisir_work import web_fetch_feedparser as _fp

    h = _fp.feedparser_health()
    assert h["ok"] is True
    assert h["version"] == "6.0.14"
    assert h["supports_rss"] is True
    assert h["supports_atom"] is True
    assert h["supports_json_feed"] is True
    assert h["supports_conditional_get"] is True
    assert h["user_agent"].startswith("feedparser/")
    assert h["max_items_default"] == 50
    assert h["max_chars_default"] == 50_000


# ---------------------------------------------------------------------------
# 3. web_fetch picker 集成
# ---------------------------------------------------------------------------

def test_picker_prefers_feedparser_for_feed_url(monkeypatch):
    """feed URL + feedparser 成功 → web_fetch.fetch 选 feedparser。"""
    from prisir_work import web_fetch as wf

    md = "# Mock Feed\n\n## 1. Item 1\nbody"
    def fake_feedparser(url, options):
        return {"content": md, "meta": {"fetcher": "feedparser", "ok": True}}
    def fake_urllib(url, options):
        return {"content": "<rss>raw xml</rss>",
                "meta": {"fetcher": "http_urllib", "ok": True}}
    def fake_jina(url, options):
        return {"content": "jina markdown",
                "meta": {"fetcher": "jina", "ok": True}}

    wf.register_fetcher("feedparser", fake_feedparser)
    wf.register_fetcher("http_urllib", fake_urllib)
    wf.register_fetcher("jina", fake_jina)

    try:
        wf._mem_clear()
    except Exception:
        pass

    r = wf.fetch("https://example.com/feed.xml",
                 options={"no_cache": True, "timeout": 5.0})
    assert r["ok"] is True
    # picker 顺序:jina > feedparser > first-wins;此处 jina 也成功所以会选 jina
    # 但若 jina 失败则 feedparser 应被选上(下面另测)
    assert r["fetcher"] in ("jina", "feedparser")


def test_picker_falls_back_to_feedparser_when_jina_fails(monkeypatch):
    """jina 失败 → feedparser 第二顺位。"""
    from prisir_work import web_fetch as wf

    md = "# Mock Feed\n\nbody"
    def fake_feedparser(url, options):
        return {"content": md, "meta": {"fetcher": "feedparser", "ok": True}}
    def fake_urllib(url, options):
        return {"content": "<rss>raw xml</rss>",
                "meta": {"fetcher": "http_urllib", "ok": True}}
    def fake_jina(url, options):
        return {"content": "", "meta": {"fetcher": "jina", "ok": False,
                                        "error": "jina_rate_limited"}}

    wf.register_fetcher("feedparser", fake_feedparser)
    wf.register_fetcher("http_urllib", fake_urllib)
    wf.register_fetcher("jina", fake_jina)

    try:
        wf._mem_clear()
    except Exception:
        pass

    r = wf.fetch("https://example.com/feed.xml",
                 options={"no_cache": True, "timeout": 5.0})
    assert r["ok"] is True
    assert r["fetcher"] == "feedparser"


# ---------------------------------------------------------------------------
# 4. 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))