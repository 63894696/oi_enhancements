# -*- coding: utf-8 -*-
"""P2.5+16e extract 测试"""
from __future__ import annotations
import json
from unittest.mock import patch

from prisir_work import extract, capability, endpoints
from prisir_work.extract import _normalize_schema, _strip_html


SAMPLE_HTML = """
<html>
<head>
  <title>Test Product Page</title>
  <meta name="description" content="A wonderful product for testing extraction">
  <meta name="author" content="Jane Doe">
  <meta name="keywords" content="test,product,extraction">
</head>
<body>
  <h1>Main Heading</h1>
  <p>The price is $29.99 and rating is 4 stars out of 247 reviews.</p>
  <ul>
    <li>Feature 1: Easy to use</li>
    <li>Feature 2: Fast</li>
    <li>Feature 3: Reliable</li>
  </ul>
</body>
</html>
"""


def test_normalize_schema_list():
    s = [{"name": "title", "type": "string", "required": True}]
    n = _normalize_schema(s)
    assert n["fields"] == s


def test_normalize_schema_dict_with_fields():
    s = {"type": "object", "fields": [{"name": "x", "type": "number"}]}
    assert _normalize_schema(s) == s


def test_normalize_schema_dict_with_properties():
    s = {"type": "object", "properties": {"title": {"type": "string"}, "price": {"type": "number"}}}
    n = _normalize_schema(s)
    names = [f["name"] for f in n["fields"]]
    assert "title" in names and "price" in names


def test_strip_html_basic():
    assert _strip_html("<p>hello <b>world</b></p>") == "hello world"


@patch("prisir_work.web_fetch.fetch")
def test_extract_via_regex_basic(mock_fetch):
    mock_fetch.return_value = {"ok": True, "url": "x", "content": SAMPLE_HTML,
                                "meta": {}, "fetcher": "mock", "cached": False,
                                "fetched_at": "2026-09-23T22:00:00Z"}
    schema = [
        {"name": "title", "type": "string", "required": True},
        {"name": "description", "type": "string"},
        {"name": "author", "type": "string"},
        {"name": "price", "type": "number"},
        {"name": "rating_count", "type": "integer"},
        {"name": "features", "type": "list"},
    ]
    result = extract.extract("https://example.com", schema)
    assert result["ok"] is True
    assert result["mode"] == "regex"
    d = result["data"]
    assert d["title"] == "Test Product Page"
    assert "wonderful" in (d["description"] or "")
    assert d["author"] == "Jane Doe"
    assert d["price"] == 29.99
    assert d["rating_count"] == 247
    assert len(d["features"]) == 3


@patch("prisir_work.web_fetch.fetch")
def test_extract_with_llm_success(mock_fetch):
    mock_fetch.return_value = {"ok": True, "url": "x", "content": SAMPLE_HTML,
                                "meta": {}, "fetcher": "mock", "cached": False,
                                "fetched_at": ""}
    def good_llm(prompt):
        return json.dumps({"title": "LLM Title", "price": 99.99}, ensure_ascii=False)
    schema = [{"name": "title", "type": "string"}, {"name": "price", "type": "number"}]
    result = extract.extract("https://example.com", schema, llm_call=good_llm)
    assert result["mode"] == "llm"
    assert result["data"]["title"] == "LLM Title"
    assert result["data"]["price"] == 99.99


@patch("prisir_work.web_fetch.fetch")
def test_extract_with_llm_failure_falls_back_to_regex(mock_fetch):
    mock_fetch.return_value = {"ok": True, "url": "x", "content": SAMPLE_HTML,
                                "meta": {}, "fetcher": "mock", "cached": False,
                                "fetched_at": ""}
    def bad_llm(prompt):
        raise RuntimeError("rate limit")
    schema = [{"name": "title", "type": "string"}]
    result = extract.extract("https://example.com", schema, llm_call=bad_llm)
    assert "llm_failed" in result["warnings"]
    assert result["mode"] == "regex"
    assert result["data"]["title"] == "Test Product Page"


@patch("prisir_work.web_fetch.fetch")
def test_extract_fetch_failure_isolated(mock_fetch):
    mock_fetch.return_value = {"ok": False, "url": "x", "content": "",
                                "meta": {"error": "all_failed"}, "fetcher": "",
                                "cached": False, "fetched_at": ""}
    result = extract.extract("https://example.com", [{"name": "title", "type": "string"}])
    assert result["ok"] is True  # 不抛
    assert "fetch_failed" in result["warnings"]
    assert result["data"] == {}


def test_extract_empty_url():
    result = extract.extract("", [{"name": "title", "type": "string"}])
    assert result["ok"] is False
    assert "empty_url" in result["warnings"]


def test_extract_empty_schema():
    with patch("prisir_work.web_fetch.fetch") as m:
        m.return_value = {"ok": True, "url": "x", "content": "html", "meta": {},
                          "fetcher": "m", "cached": False, "fetched_at": ""}
        result = extract.extract("https://x", [])
    assert result["ok"] is False
    assert "empty_schema" in result["warnings"]


def test_capability_registered():
    ids = {c["id"] for c in capability.list_capabilities()}
    assert "web.extract" in ids


def test_endpoint_whitelist():
    paths = {e["path"] for e in endpoints.catalog()}
    assert "/web/extract" in paths


def test_existing_capabilities_not_broken():
    ids = {c["id"] for c in capability.list_capabilities()}
    for must in ("system.health", "web.search", "web.fetch", "web.research", "web.extract"):
        assert must in ids, f"{must} missing"
