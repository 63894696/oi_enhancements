"""P2.5+16f find_similar 测试"""
from __future__ import annotations
from unittest.mock import patch

from prisir_work import find_similar, capability, endpoints, handlers  # noqa: F401,E402  -- handlers 触发现有 capability 注册
from prisir_work.find_similar import _normalize_host, _extract_keywords


SAMPLE_HTML = """
<html>
<head>
  <title>Local Web Intelligence MCP Server</title>
  <meta name="description" content="Wigolo is a local-first web intelligence server for AI agents">
</head>
<body>
  <h1>Local AI Research Tools</h1>
  <h2>Compare with Tavily and Exa</h2>
  <p>Open source alternative to Firecrawl.</p>
</body>
</html>
"""


def test_normalize_host():
    assert _normalize_host("https://Example.com/path") == "example.com"
    assert _normalize_host("http://localhost:8000/") == "localhost"


def test_extract_keywords():
    import re
    kw = _extract_keywords(SAMPLE_HTML)
    assert isinstance(kw, list)
    assert len(kw) > 0
    # 至少有一个英文词或中文短语
    assert any(re.match(r"[a-z]{3,}", k) for k in kw) or any(re.match(r"[一-鿿]{2,4}", k) for k in kw)


def test_empty_url():
    result = find_similar.find_similar("")
    assert result["ok"] is False
    assert "empty_url" in result["warnings"]


@patch("prisir_work.web_search.search")
@patch("prisir_work.web_fetch.fetch")
def test_full_flow_with_mock(mock_fetch, mock_search):
    mock_fetch.return_value = {"ok": True, "url": "https://a.example/page", "content": SAMPLE_HTML,
                                "meta": {}, "fetcher": "mock", "cached": False, "fetched_at": ""}
    mock_search.return_value = [
        {"url": "https://b.example/similar", "title": "Similar Tool", "snippet": "snip",
         "score": 0.9, "sources": ["mock"]},
        {"url": "https://a.example/other", "title": "Same host", "snippet": "x",
         "score": 0.8, "sources": ["mock"]},  # 同 host 应被过滤
        {"url": "https://c.example/another", "title": "Another", "snippet": "y",
         "score": 0.7, "sources": ["mock"]},
    ]
    result = find_similar.find_similar("https://a.example/page")
    assert result["ok"]
    urls = [s["url"] for s in result["similar"]]
    assert "https://b.example/similar" in urls
    assert "https://c.example/another" in urls
    assert not any("a.example" in u and "page" not in u for u in urls)  # 同 host 过滤
    assert len(result["keywords"]) > 0


@patch("prisir_work.web_search.search")
@patch("prisir_work.web_fetch.fetch")
def test_fetch_failure(mock_fetch, mock_search):
    mock_fetch.return_value = {"ok": False, "url": "x", "content": "",
                                "meta": {"error": "all_failed"}, "fetcher": "",
                                "cached": False, "fetched_at": ""}
    mock_search.return_value = []
    result = find_similar.find_similar("https://example.com/x")
    assert result["ok"] is True
    assert "fetch_failed" in result["warnings"]


@patch("prisir_work.web_search.search")
@patch("prisir_work.web_fetch.fetch")
def test_search_failure_isolated(mock_fetch, mock_search):
    mock_fetch.return_value = {"ok": True, "url": "x", "content": SAMPLE_HTML,
                                "meta": {}, "fetcher": "mock", "cached": False, "fetched_at": ""}
    mock_search.side_effect = RuntimeError("network down")
    result = find_similar.find_similar("https://example.com/x")
    assert result["ok"] is True
    assert "search_failed" in result["warnings"]
    # 不抛


@patch("prisir_work.web_search.search")
@patch("prisir_work.web_fetch.fetch")
def test_serper_no_key_skipped(mock_fetch, mock_search):
    mock_fetch.return_value = {"ok": True, "url": "x", "content": SAMPLE_HTML,
                                "meta": {}, "fetcher": "mock", "cached": False, "fetched_at": ""}
    mock_search.return_value = [{"url": "https://b/", "title": "B", "snippet": "x",
                                  "score": 0.9, "sources": ["mock"]}]
    result = find_similar.find_similar("https://a.example/x")
    # 无 SERPER_API_KEY env,应跳过 + warning
    assert "serper_no_key" in result["warnings"]


@patch("prisir_work.web_search.search")
@patch("prisir_work.web_fetch.fetch")
def test_max_results_cap(mock_fetch, mock_search):
    mock_fetch.return_value = {"ok": True, "url": "x", "content": SAMPLE_HTML,
                                "meta": {}, "fetcher": "mock", "cached": False, "fetched_at": ""}
    mock_search.return_value = [
        {"url": f"https://b{i}.example/", "title": f"B{i}", "snippet": "x",
         "score": 0.9 - i * 0.05, "sources": ["mock"]} for i in range(20)
    ]
    result = find_similar.find_similar("https://a.example/x", max_results=5)
    assert len(result["similar"]) <= 5


@patch("prisir_work.web_search.search")
@patch("prisir_work.web_fetch.fetch")
def test_same_host_limit_3(mock_fetch, mock_search):
    mock_fetch.return_value = {"ok": True, "url": "x", "content": SAMPLE_HTML,
                                "meta": {}, "fetcher": "mock", "cached": False, "fetched_at": ""}
    # 同一 host 5 条,只应留 3
    mock_search.return_value = [
        {"url": f"https://same.example/page{i}", "title": f"P{i}", "snippet": "x",
         "score": 0.9 - i * 0.01, "sources": ["mock"]} for i in range(5)
    ]
    result = find_similar.find_similar("https://input.example/x")
    same_count = sum(1 for s in result["similar"] if "same.example" in s["url"])
    assert same_count <= 3


def test_capability_registered():
    ids = {c["id"] for c in capability.list_capabilities()}
    assert "web.find_similar" in ids


def test_endpoint_whitelist():
    paths = {e["path"] for e in endpoints.catalog()}
    assert "/web/find_similar" in paths


def test_existing_capabilities_not_broken():
    """所有原有 + web 系列都不能丢"""
    ids = {c["id"] for c in capability.list_capabilities()}
    for must in ("system.health", "web.search", "web.fetch", "web.research",
                 "web.extract", "web.find_similar"):
        assert must in ids, f"{must} missing"