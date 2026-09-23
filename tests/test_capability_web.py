# -*- coding: utf-8 -*-
"""tests/test_capability_web.py — P2.5+16 capability 注册测试

验证:
  · web.search / web.fetch 已注册到能力表
  · 关键词(中文/英文)命中
  · /web/search / /web/fetch 在 endpoint 白名单
  · handler 真调底层函数(空 query / 空 url → 200 + ok=True)
  · 现有 8 个 capability 不能动
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from prisir_work import capability, endpoints, handlers  # noqa: F401,E402  -- handlers 触发现有 capability 注册


def test_web_search_in_list():
    caps = capability.list_capabilities()
    ids = {c["id"] for c in caps}
    assert "web.search" in ids
    assert "web.fetch" in ids


def test_web_search_keyword_hit():
    res = capability.search("搜索")
    ids = {c["id"] for c in res}
    assert "web.search" in ids


def test_web_fetch_keyword_hit():
    res = capability.search("抓取")
    ids = {c["id"] for c in res}
    assert "web.fetch" in ids


def test_english_keyword_hit():
    res = capability.search("web")
    ids = {c["id"] for c in res}
    assert "web.search" in ids
    assert "web.fetch" in ids


def test_endpoint_whitelist():
    cat = endpoints.catalog()
    paths = {e["path"] for e in cat}
    assert "/web/search" in paths
    assert "/web/fetch" in paths


def test_web_search_handler_empty():
    body = {"query": ""}
    payload, status = endpoints.lookup("/web/search")["handler"](body)
    assert status == 200
    assert payload["ok"] is True
    assert payload["results"] == []


def test_web_fetch_handler_empty():
    body = {"url": ""}
    payload, status = endpoints.lookup("/web/fetch")["handler"](body)
    assert status == 200
    assert payload["ok"] is True
    assert payload["content"] == ""


def test_existing_capabilities_not_broken():
    """现有 8 个 capability 不能动 — 至少 system.health / wallet.status / team.submit 还得在。"""
    ids = {c["id"] for c in capability.list_capabilities()}
    for must in ("system.health", "wallet.status", "team.submit", "web.search", "web.fetch"):
        assert must in ids, f"{must} missing"


def main() -> int:
    import pytest as _pytest
    rc = _pytest.main([__file__, "-v"])
    return rc


if __name__ == "__main__":
    sys.exit(main())