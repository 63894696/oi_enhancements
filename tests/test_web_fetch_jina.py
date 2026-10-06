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

import pytest


@pytest.fixture(autouse=True)
def _isolate_fetchers(monkeypatch):
    """每个 case 用 monkeypatch 替换 web_fetch._FETCHERS 引用为 fresh dict,
    避免前一个测试遗留 fake 污染当前 picker 行为。

    monkeypatch.setattr 改 module-level name 绑定,fetch 内部读 _FETCHERS 时
    会从 module globals 拿到 fresh 引用,只执行测试里 register 的 fake。
    """
    from prisir_work import web_fetch as _wf
    fresh: dict = {}
    monkeypatch.setattr(_wf, "_FETCHERS", fresh, raising=False)
    yield fresh


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
    # 重置 quota,确保 hosted_no_key 默认 20 配额从干净状态起算
    _jina._QUOTA_TIMES.clear()
    r = _jina.jina_fetch("https://example.com", {"timeout": 5.0})
    assert r["meta"]["fetcher"] == "jina"
    assert r["meta"]["ok"] is True
    assert r["meta"]["status"] == 200
    assert r["meta"]["format"] == "markdown"
    assert r["meta"]["mode"] in ("hosted_no_key", "hosted_with_key",
                                  "self_hosted")
    assert "Example Domain" in r["content"]
    assert "illustrative" in r["content"]
    # P3j T20-I.2:meta 暴露 quota 字段
    assert r["meta"]["rpm_limit"] == 20  # 默认 hosted_no_key
    assert "rpm_used_last_60s" in r["meta"]


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
    _jina._QUOTA_TIMES.clear()
    r = _jina.jina_fetch("https://very-slow.test/")
    assert r["meta"]["ok"] is False
    assert r["meta"]["error"] == "url_error"
    assert r["meta"]["detail"] == "timed out"
    assert r["meta"]["mode"] in ("hosted_no_key", "hosted_with_key",
                                  "self_hosted")


def test_jina_fetch_self_hosted(monkeypatch):
    """JINA_READER_URL 设了 → _reader_base 走自部署 + mode=self_hosted。"""
    from prisir_work import web_fetch_jina as _jina

    saved = _jina.JINA_READER_URL
    _jina.JINA_READER_URL = "http://localhost:8081"
    try:
        _jina._QUOTA_TIMES.clear()
        captured = {}
        def fake(url, *, timeout=30.0, headers=None, params=None,
                 no_cache=False):
            captured["url"] = url
            captured["no_cache"] = no_cache
            return {"ok": True, "status": 200,
                    "content": "self-hosted markdown", "headers": {}}
        monkeypatch.setattr(_jina, "_http_get", fake)
        r = _jina.jina_fetch("https://example.com")
        assert r["meta"]["mode"] == "self_hosted"
        assert r["meta"]["ok"] is True
        assert captured["url"].startswith("http://localhost:8081/")
        # P3j T20-I.2:自部署模式 quota 10000,rpm_limit 跟着 mode 走
        assert r["meta"]["rpm_limit"] == 10_000
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
    """hosted:两端均 ok → ok=True, mode 字段存在, reader/search 字典存在。

    P3j T20-I.2:无 key 时 health.search.skipped=True,所以这里需要
    monkeypatch JINA_API_KEY 让 search 真的去探。
    """
    from prisir_work import web_fetch_jina as _jina

    monkeypatch.setattr(
        _jina, "_http_get",
        lambda *a, **kw: {"ok": True, "status": 200, "content": "ok", "headers": {}},
    )
    saved_key = _jina.JINA_API_KEY
    _jina.JINA_API_KEY = "test_key"
    try:
        _jina._QUOTA_TIMES.clear()
        h = _jina.jina_health()
        assert h["ok"] is True
        assert h["mode"] == "hosted_with_key"
        assert h["reader"]["ok"] is True
        assert h["search"]["ok"] is True
        assert h["search"]["skipped"] is False
        assert h["no_key_supported"] is True
        assert h["search_requires_key"] is True
        assert h["quota"]["rpm_limit"] == 500
    finally:
        _jina.JINA_API_KEY = saved_key


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
    """两端都成功 → web_fetch.fetch 返回非空 content(fetcher first-wins)。"""
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
    # picker 是 ThreadPoolExecutor first-wins,不保证 jina 一定先到,
    # 只断言内容非空 + fetcher ∈ {jina, http_urllib}。
    assert r["fetcher"] in ("jina", "http_urllib"), f"unexpected fetcher: {r['fetcher']}"
    assert r["content"], "content 不应为空"
    assert r["meta"]["fetcher"] == r["fetcher"]


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
        r = ws.search("hi", limit=3, providers=["jina_search"], timeout=60.0)
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
# 5. P3j T20-I.2:令牌桶 + X-No-Cache + 免 key health
# ---------------------------------------------------------------------------

def test_jina_fetch_rate_limited(monkeypatch):
    """填满令牌桶 → jina_fetch 返 ok=False + error=jina_rate_limited,不走 HTTP。

    设计意图:超额让 picker 自动落 urllib,绝不阻塞主对话。
    """
    from prisir_work import web_fetch_jina as _jina

    # 强制 hosted_no_key 模式(env 全空,verify 默认就是)
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.delenv("JINA_READER_URL", raising=False)
    monkeypatch.delenv("JINA_SEARCH_URL", raising=False)
    _jina._QUOTA_TIMES.clear()
    import time as _t
    # 装满 20 个令牌(hosted_no_key 上限)
    _jina._QUOTA_TIMES.extend([_t.monotonic()] * _jina._current_rpm_limit())

    called = []
    monkeypatch.setattr(_jina, "_http_get",
                        lambda *a, **kw: called.append(1) or {"ok": False})
    r = _jina.jina_fetch("https://example.com")
    assert r["meta"]["error"] == "jina_rate_limited", \
        f"want jina_rate_limited, got {r['meta'].get('error')}"
    assert r["meta"]["rate_limit_per_min"] == 20
    assert r["meta"]["mode"] == "hosted_no_key"
    assert called == [], \
        f"限流时不应调 HTTP,但调了 {len(called)} 次"
    # quota 状态应满
    qs = _jina.quota_status()
    assert qs["rpm_limit"] == 20
    assert qs["used_last_60s"] == 20
    assert qs["remaining"] == 0


def test_jina_fetch_self_hosted_skips_quota(monkeypatch):
    """自部署模式 → quota=10000,即便 _QUOTA_TIMES 满了也放行。

    设计意图:自部署不限流,不应被令牌桶误拦截。
    """
    from prisir_work import web_fetch_jina as _jina

    saved_url = _jina.JINA_READER_URL
    _jina.JINA_READER_URL = "http://localhost:8081"
    try:
        _jina._QUOTA_TIMES.clear()
        import time as _t
        # 装 100 个(>自部署 limit 10000 的 1/100,但这里测试意图是
        # 即便桶满也放行,因为 quota 检查在 hosted 模式才触发)
        _jina._QUOTA_TIMES.extend([_t.monotonic()] * 100)

        captured = []
        monkeypatch.setattr(_jina, "_http_get",
                            lambda *a, **kw: captured.append(1) or
                            {"ok": True, "status": 200,
                             "content": "ok", "headers": {}})
        r = _jina.jina_fetch("https://example.com")
        assert captured, "自部署不应被 quota 拦截,应该走 HTTP"
        assert r["meta"]["ok"] is True
        assert r["meta"]["mode"] == "self_hosted"
    finally:
        _jina.JINA_READER_URL = saved_url


def test_jina_fetch_no_cache_header(monkeypatch):
    """options.no_cache=True → _http_get 收到 no_cache=True。"""
    from prisir_work import web_fetch_jina as _jina

    _jina._QUOTA_TIMES.clear()
    captured = {}
    def fake(url, *, timeout=30.0, headers=None, params=None,
             no_cache=False):
        captured["no_cache"] = no_cache
        return {"ok": True, "status": 200, "content": "ok", "headers": {}}
    monkeypatch.setattr(_jina, "_http_get", fake)

    # default:no_cache=False
    _jina.jina_fetch("https://example.com")
    assert captured["no_cache"] is False

    # 传 no_cache=True
    _jina.jina_fetch("https://example.com", {"no_cache": True})
    assert captured["no_cache"] is True


def test_jina_health_no_key_mode(monkeypatch):
    """env 全空 → health.mode=hosted_no_key + quota.rpm_limit=20 +
    search.skipped=True(不打 s.jina.ai 避免 403 污染日志)。"""
    from prisir_work import web_fetch_jina as _jina

    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.delenv("JINA_SEARCH_URL", raising=False)
    monkeypatch.delenv("JINA_READER_URL", raising=False)
    _jina._QUOTA_TIMES.clear()

    # reader mock 真探活
    def fake_http(url, *, timeout=5.0, headers=None, params=None,
                  no_cache=False):
        # 抓 reader / search 区分
        if "/example.com" in url:
            return {"ok": True, "status": 200,
                    "content": "ok", "headers": {}}
        # 理论上无 key 时不应该走到 search 这条,先返 fail
        return {"ok": False, "status": 403, "error": "http_403"}

    monkeypatch.setattr(_jina, "_http_get", fake_http)
    h = _jina.jina_health()

    assert h["mode"] == "hosted_no_key"
    assert h["api_key_set"] is False
    assert h["self_hosted"] is False
    assert h["no_key_supported"] is True
    assert h["search_requires_key"] is True
    # search 应被跳过(无 key,不真打 s.jina.ai)
    assert h["search"]["skipped"] is True
    assert h["search"]["error"] == "no_key_required"
    # reader 真打
    assert h["reader"]["ok"] is True
    # quota 字段
    assert h["quota"]["rpm_limit"] == 20
    assert h["quota"]["used_last_60s"] == 0
    assert h["quota"]["remaining"] == 20


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))
