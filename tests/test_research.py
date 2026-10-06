"""P2.5+16d research 多步研究测试"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from prisir_work import research as research_module  # noqa: E402
from prisir_work.research import plan_queries, research as research_fn  # noqa: E402
from prisir_work import capability, endpoints  # noqa: E402
from prisir_work import handlers  # noqa: E402,F401  # 注册 system.health/wallet/team 等 capability


def test_plan_default():
    qs = plan_queries("wigolo web tool")
    assert len(qs) >= 2
    assert qs[0] == "wigolo web tool"
    # 包含中文变体
    assert any("对比" in q or "是什么" in q for q in qs)


def test_plan_with_llm_mock():
    def mock_llm(prompt):
        return json.dumps(["alpha query", "beta query", "gamma query"], ensure_ascii=False)
    qs = plan_queries("anything", llm_call=mock_llm)
    assert qs == ["alpha query", "beta query", "gamma query"]


def test_plan_with_llm_bad_json_falls_back():
    def mock_llm(prompt):
        return "not json"
    qs = plan_queries("wigolo", llm_call=mock_llm)
    assert qs[0] == "wigolo"  # 降级到模板


def test_plan_empty():
    assert plan_queries("") == []


@patch("prisir_work.research._search_one")
def test_search_failure_isolated(mock_search):
    mock_search.side_effect = RuntimeError("network down")
    result = research_fn("test")
    assert result["ok"] is True  # 不抛
    assert "search_failed" in result["warnings"] or result["sources"] == []
    assert result["steps"][1]["step"] == "search"


@patch("prisir_work.research._fetch_one")
@patch("prisir_work.research._search_one")
def test_fetch_failure_skips_url(mock_search, mock_fetch):
    mock_search.return_value = [
        {"url": "https://a.example/", "title": "A", "snippet": "a", "score": 0.9},
        {"url": "https://b.example/", "title": "B", "snippet": "b", "score": 0.8},
    ]
    mock_fetch.side_effect = lambda u, t: None if "b.example" in u else {"url": u, "content": "ok", "meta": {}, "fetcher": "mock", "fetched_at": ""}
    result = research_fn("test")
    assert result["ok"]
    assert any("fetch_failed" in w for w in result["warnings"])
    # b 被跳过,只有 a
    urls = [s["url"] for s in result["sources"]]
    assert "https://a.example/" in urls
    assert not any("b.example" in u for u in urls)


def test_synthesize_llm_failure_degrades():
    def bad_llm(prompt):
        raise RuntimeError("rate limit")
    with patch("prisir_work.research._search_one") as ms, \
         patch("prisir_work.research._fetch_one") as mf:
        ms.return_value = [{"url": "https://a/", "title": "A", "snippet": "x", "score": 0.9}]
        mf.return_value = {"url": "https://a/", "content": "real content here", "meta": {}, "fetcher": "m", "fetched_at": ""}
        result = research_fn("test", llm_call=bad_llm)
    assert result["ok"]
    assert "synthesize_failed" in " ".join(result["warnings"])
    assert "原始素材" in result["answer"]


def test_synthesize_no_llm_provided():
    with patch("prisir_work.research._search_one") as ms, \
         patch("prisir_work.research._fetch_one") as mf:
        ms.return_value = [{"url": "https://a/", "title": "A", "snippet": "x", "score": 0.9}]
        mf.return_value = {"url": "https://a/", "content": "real", "meta": {}, "fetcher": "m", "fetched_at": ""}
        result = research_fn("test")
    assert result["ok"]
    assert result["answer"]  # 降级拼接
    assert len(result["sources"]) == 1


def test_citations_match_sources():
    with patch("prisir_work.research._search_one") as ms, \
         patch("prisir_work.research._fetch_one") as mf:
        ms.return_value = [
            {"url": "https://a/", "title": "A", "snippet": "a", "score": 0.9},
            {"url": "https://b/", "title": "B", "snippet": "b", "score": 0.8},
        ]
        mf.side_effect = lambda u, t: {"url": u, "content": "ok", "meta": {}, "fetcher": "m", "fetched_at": ""}
        result = research_fn("test", llm_call=lambda p: "[1] and [2]")
    assert len(result["citations"]) == len(result["sources"])
    assert result["citations"][0]["n"] == 1
    assert result["citations"][1]["n"] == 2


def test_empty_query():
    result = research_fn("")
    assert result["ok"] is False
    assert result["answer"] == ""
    assert "empty_query" in result["warnings"]


def test_capability_registered():
    ids = {c["id"] for c in capability.list_capabilities()}
    assert "web.research" in ids


def test_endpoint_whitelist():
    paths = {e["path"] for e in endpoints.catalog()}
    assert "/web/research" in paths


def test_endpoint_handler_empty():
    payload, status = endpoints.lookup("/web/research")["handler"]({"query": ""})
    assert status == 200 and payload["ok"]
    assert "empty_query" in payload["warnings"]


def test_existing_capabilities_not_broken():
    """原有 web.search / web.fetch / 8 原有 capability 不能动"""
    ids = {c["id"] for c in capability.list_capabilities()}
    for must in ("system.health", "wallet.status", "team.submit",
                 "web.search", "web.fetch", "web.research"):
        assert must in ids, f"{must} missing"


def main() -> int:
    import pytest as _pytest
    rc = _pytest.main([__file__, "-v"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
