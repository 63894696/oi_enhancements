# -*- coding: utf-8 -*-
"""tests/test_web_search.py — P2.5+16 web_search.py 测试。

覆盖(全部必绿,无 skip):
  · test_rrf_fusion           — 用 mock provider 验证 A 被两个 provider 投时 score 最高
  · test_provider_failure_iso — 一个 provider raise,另一个正常 → 整体不抛,结果只含正常
  · test_provider_timeout     — future.result(timeout=0.5) 模拟超时,降级不抛
  · test_url_dedup            — 同一 URL 带 fragment / trailing slash 视为一条
  · test_empty_query          — 返 []
  · test_no_providers         — 显式传空 providers → []
  · test_lru_cache_hit        — 第二次同 query 命中 LRU,provider 只被调一次
  · test_url_normalize        — 单元测 url_normalize 函数
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import prisir_work.web_search as _ws  # noqa: E402


def _reset_providers() -> None:
    """清空 provider 列表 + LRU 缓存(测试要插 mock)。"""
    _ws._PROVIDERS.clear()
    _ws.clear_cache()


def _restore_builtin() -> None:
    """恢复内置 provider(ddg_html / baidu / bing_public 总是注册;可选 key 重读 env)。"""
    import os
    _ws.register_provider("ddg_html", _ws.ddg_html)
    _ws.register_provider("baidu", _ws.baidu)
    _ws.register_provider("bing_public", _ws.bing_public)
    if os.environ.get("TAVILY_API_KEY", "").strip():
        _ws.register_provider("tavily", _ws.tavily)
    if os.environ.get("SERPER_API_KEY", "").strip():
        _ws.register_provider("serper", _ws.serper)


# ---------------------------------------------------------------------------
# url_normalize 单元测试
# ---------------------------------------------------------------------------


def test_url_normalize():
    """strip fragment + lowercase host + strip trailing slash;默认端口也清掉。"""
    assert _ws.url_normalize("https://Example.com/Page") == "https://example.com/Page"
    assert _ws.url_normalize("https://example.com/page/") == "https://example.com/page"
    assert _ws.url_normalize("https://example.com/page/#section-1") == "https://example.com/page"
    # 根目录的 / 保留
    assert _ws.url_normalize("https://example.com/") == "https://example.com/"
    # 默认端口清掉
    assert _ws.url_normalize("https://example.com:443/x") == "https://example.com/x"
    assert _ws.url_normalize("http://example.com:80/x") == "http://example.com/x"
    # 保留非默认端口
    assert _ws.url_normalize("http://example.com:8080/x") == "http://example.com:8080/x"
    # query 保留
    assert _ws.url_normalize("https://example.com/p?q=1#frag") == "https://example.com/p?q=1"
    # 空字符串
    assert _ws.url_normalize("") == ""
    print("✓ url_normalize 各分支正确")


# ---------------------------------------------------------------------------
# 空 / 无 provider 兜底
# ---------------------------------------------------------------------------


def test_empty_query():
    """空 query → []。"""
    _reset_providers()
    _restore_builtin()
    _ws.clear_cache()

    assert _ws.search("") == []
    assert _ws.search("   ") == []
    print("✓ 空 query → []")


def test_no_providers():
    """显式传空 providers → [];无任何 provider 注册时 → []。"""
    _reset_providers()
    assert _ws.search("anything", limit=5, providers=[]) == []

    # 没有 provider 的情况(空注册表)
    _reset_providers()
    assert _ws.search("anything", limit=5) == []

    # 注册一个,但显式 pass 一个不存在的 provider → 也应 []
    _reset_providers()
    _ws.register_provider("mock", lambda q, n: [{"url": "https://x", "title": "t", "snippet": "s"}])
    assert _ws.search("anything", limit=5, providers=["nonexistent"]) == []

    print("✓ 空 providers / 无注册 / 不存在 provider → []")


# ---------------------------------------------------------------------------
# rank fusion
# ---------------------------------------------------------------------------


def test_rrf_fusion():
    """两个 provider 都投 A → A 的 score 应该最高(同时被两个 provider 投的胜出)。"""
    _reset_providers()
    _ws.clear_cache()

    def p1(q, limit):
        return [
            {"url": "https://example.com/a", "title": "A", "snippet": "a-snippet"},
            {"url": "https://example.com/b", "title": "B", "snippet": "b-snippet"},
        ]

    def p2(q, limit):
        return [
            {"url": "https://example.com/c", "title": "C", "snippet": "c-snippet"},
            {"url": "https://example.com/a", "title": "A", "snippet": "a-snippet"},
        ]

    _ws.register_provider("p1", p1)
    _ws.register_provider("p2", p2)

    out = _ws.search("anything", limit=10)
    assert out, "RRF 应有结果"

    # A 必须排第一
    assert out[0]["url"] == "https://example.com/a", f"A 应排第一,got {out[0]}"
    # A 同时被两个 provider 投
    assert sorted(out[0]["sources"]) == ["p1", "p2"], f"sources={out[0]['sources']}"
    # 验证 score 字段存在且为 float
    assert isinstance(out[0]["score"], float)
    # B 和 C 各只被一个 provider 投 → 排名靠后
    rest_urls = [r["url"] for r in out[1:]]
    assert set(rest_urls) == {"https://example.com/b", "https://example.com/c"}

    # 排序按 score 严格非增
    scores = [r["score"] for r in out]
    assert scores == sorted(scores, reverse=True), f"应按 score 降序,got {scores}"
    print(f"✓ A 同时被两 provider 投,score={out[0]['score']:.4f},排第一")


# ---------------------------------------------------------------------------
# 异常隔离
# ---------------------------------------------------------------------------


def test_provider_failure_isolated():
    """一个 provider raise,另一个正常 → 整体不抛,结果只含正常的。"""
    _reset_providers()
    _ws.clear_cache()

    def boom(q, limit):
        raise RuntimeError("boom")

    def ok(q, limit):
        return [{"url": "https://ok.com/x", "title": "ok-title", "snippet": "ok-snippet"}]

    _ws.register_provider("boom", boom)
    _ws.register_provider("ok", ok)

    out = _ws.search("anything", limit=5)
    assert len(out) == 1, f"应只一条,got {out}"
    assert out[0]["url"] == "https://ok.com/x"
    assert out[0]["sources"] == ["ok"]
    print("✓ boom provider raise 不影响 ok,整体不抛")


def test_provider_timeout():
    """future.result(timeout=0.5) 模拟超时 → 降级不抛。"""
    _reset_providers()
    _ws.clear_cache()

    def slow(q, limit):
        time.sleep(2.0)
        return [{"url": "https://slow.com/x", "title": "s", "snippet": "s"}]

    def ok(q, limit):
        return [{"url": "https://ok.com/x", "title": "ok", "snippet": "ok"}]

    _ws.register_provider("slow", slow)
    _ws.register_provider("ok", ok)

    t0 = time.monotonic()
    out = _ws.search("anything", limit=5, timeout=0.5)
    dt = time.monotonic() - t0

    assert dt < 1.5, f"应被 timeout 切断,实际 {dt:.2f}s"
    # ok 仍应入选;slow 因超时被丢
    urls = [r["url"] for r in out]
    assert "https://ok.com/x" in urls, f"ok 应入选,got {urls}"
    assert "https://slow.com/x" not in urls, f"slow 应被超时丢,got {urls}"
    print(f"✓ slow provider 超时被切断({dt:.2f}s),ok 仍入选")


# ---------------------------------------------------------------------------
# URL 去重
# ---------------------------------------------------------------------------


def test_url_dedup():
    """同一 URL 带 fragment / trailing slash → RRF 视为一条(sources 合并)。"""
    _reset_providers()
    _ws.clear_cache()

    def p1(q, limit):
        return [
            {"url": "https://example.com/page/", "title": "with slash", "snippet": "s1"},
        ]

    def p2(q, limit):
        return [
            {"url": "https://example.com/page#section-1", "title": "with frag", "snippet": "s2"},
        ]

    _ws.register_provider("p1", p1)
    _ws.register_provider("p2", p2)

    out = _ws.search("anything", limit=5)
    assert len(out) == 1, f"两 url 规范化后应合并为 1 条,got {len(out)}: {out}"
    assert sorted(out[0]["sources"]) == ["p1", "p2"], f"sources={out[0]['sources']}"
    # 规范化 url 输出(无 fragment, 无尾部 slash)
    assert out[0]["url"] == "https://example.com/page", f"normalized url 应 = https://example.com/page,got {out[0]['url']!r}"
    print("✓ 同一 URL 不同写法 → 1 条结果,sources=[p1,p2]")


# ---------------------------------------------------------------------------
# LRU 缓存
# ---------------------------------------------------------------------------


def test_lru_cache_hit():
    """第二次同 query 命中 LRU,provider 只被调一次。"""
    _reset_providers()
    _ws.clear_cache()

    call_count = {"n": 0}
    lock = threading.Lock()

    def mock(q, limit):
        with lock:
            call_count["n"] += 1
        return [
            {"url": "https://mock.com/1", "title": "M1", "snippet": "s1"},
            {"url": "https://mock.com/2", "title": "M2", "snippet": "s2"},
        ]

    _ws.register_provider("mock", mock)

    r1 = _ws.search("q1", limit=5)
    assert len(r1) == 2
    assert call_count["n"] == 1

    r2 = _ws.search("q1", limit=5)
    assert len(r2) == 2
    # 关键:缓存命中 → mock 只跑一次
    assert call_count["n"] == 1, f"第二次应命中 LRU,mock 仍被调 → 共 {call_count['n']} 次"

    # 不同 limit → 不同 cache key → 应再调一次
    r3 = _ws.search("q1", limit=3)
    assert call_count["n"] == 2, f"不同 limit 应 miss 缓存,mock 应再跑一次,got {call_count['n']}"
    print(f"✓ 第二次同 query 命中 LRU(mock 只跑 {call_count['n']} 次)")


# ---------------------------------------------------------------------------
# main — 直接 python tests/test_web_search.py 也跑得起来
# ---------------------------------------------------------------------------

def main() -> int:
    import pytest as _pytest
    rc = _pytest.main([__file__, "-v"])
    return rc


if __name__ == "__main__":
    sys.exit(main())
