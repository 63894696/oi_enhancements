"""test_cache_query.py — P2.5+17a cache 查询门面单测
覆盖 cache_list / cache_invalidate / cache_stats,隔离用 monkeypatch 的 _CACHE_DIR_OVERRIDE。"""
import os, sys, json, tempfile, shutil
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, '.')

from prisir_work import cache as _cache
from prisir_work import cache_query as _cq


def _setup_tmp_cache(monkeypatch_module=True):
    """建立临时 cache 目录,monkeypatch _CACHE_DIR_OVERRIDE。"""
    tmpdir = Path(tempfile.mkdtemp(prefix="cache_query_test_"))
    _cache._CACHE_DIR_OVERRIDE = tmpdir
    return tmpdir


def _teardown_tmp_cache(orig_override):
    _cache._CACHE_DIR_OVERRIDE = orig_override
    if orig_override and Path(orig_override).exists():
        shutil.rmtree(orig_override, ignore_errors=True)


def _seed(url, html="<html>ok</html>", ttl_days=7):
    """写一条 cache 记录。"""
    _cache.cache_put(url, {"content": html, "fetcher": "mock"}, ttl_days=ttl_days)


# ── 1. 基础初始化 ──

def test_cache_list_empty_dir():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        r = _cq.cache_list()
        assert r["ok"] is True
        assert r["entries"] == []
        assert r["total"] == 0
    finally:
        _teardown_tmp_cache(orig)


def test_cache_list_returns_seeded_entries():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/a")
        _seed("https://example.com/b")
        _seed("https://other.com/x")
        r = _cq.cache_list()
        assert r["total"] == 3
        urls = {e["url"] for e in r["entries"]}
        assert urls == {"https://example.com/a", "https://example.com/b", "https://other.com/x"}
        for e in r["entries"]:
            assert "host" in e
            assert "fetched_at" in e
            assert "expires_at" in e
            assert "size_bytes" in e
            assert e["expired"] is False
    finally:
        _teardown_tmp_cache(orig)


def test_cache_list_host_filter():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://github.com/foo")
        _seed("https://github.com/bar")
        _seed("https://gitlab.com/x")
        r = _cq.cache_list(host="github.com")
        assert r["total"] == 2
        assert all("github.com" in e["host"] for e in r["entries"])
    finally:
        _teardown_tmp_cache(orig)


def test_cache_list_host_filter_case_insensitive():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://GitHub.com/foo")
        r = _cq.cache_list(host="github.com")
        assert r["total"] == 1
    finally:
        _teardown_tmp_cache(orig)


def test_cache_list_limit():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        for i in range(5):
            _seed(f"https://example.com/{i}")
        r = _cq.cache_list(limit=3)
        assert r["total"] == 3
        assert r["limit"] == 3
    finally:
        _teardown_tmp_cache(orig)


def test_cache_list_excludes_expired_by_default():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/fresh", ttl_days=7)
        _seed("https://example.com/old", ttl_days=0)
        # ttl_days=0 → expires_at == now → 立即过期
        import time
        time.sleep(0.05)
        r = _cq.cache_list()
        urls = {e["url"] for e in r["entries"]}
        assert "https://example.com/fresh" in urls
        assert "https://example.com/old" not in urls
    finally:
        _teardown_tmp_cache(orig)


def test_cache_list_include_expired():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/fresh", ttl_days=7)
        _seed("https://example.com/old", ttl_days=0)
        import time
        time.sleep(0.05)
        r = _cq.cache_list(include_expired=True)
        assert r["total"] == 2
        old = next(e for e in r["entries"] if "old" in e["url"])
        assert old["expired"] is True
    finally:
        _teardown_tmp_cache(orig)


def test_cache_list_sorted_by_fetched_at_desc():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/first")
        import time
        time.sleep(0.05)
        _seed("https://example.com/second")
        r = _cq.cache_list()
        urls = [e["url"] for e in r["entries"]]
        assert urls[0].endswith("/second")  # 最新的排前
    finally:
        _teardown_tmp_cache(orig)


# ── 2. invalidate ──

def test_cache_invalidate_no_args_noop():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/x")
        r = _cq.cache_invalidate()
        assert r["deleted"] == 0
        assert r["mode"] == "noop"
        # 没真删
        assert _cq.cache_list()["total"] == 1
    finally:
        _teardown_tmp_cache(orig)


def test_cache_invalidate_by_url():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/a")
        _seed("https://example.com/b")
        r = _cq.cache_invalidate(url="https://example.com/a")
        assert r["deleted"] == 1
        assert r["mode"] == "url"
        remaining = {e["url"] for e in _cq.cache_list()["entries"]}
        assert "https://example.com/a" not in remaining
        assert "https://example.com/b" in remaining
    finally:
        _teardown_tmp_cache(orig)


def test_cache_invalidate_by_host():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://github.com/a")
        _seed("https://github.com/b")
        _seed("https://other.com/c")
        r = _cq.cache_invalidate(host="github.com")
        assert r["deleted"] == 2
        assert r["mode"] == "host"
        remaining = {e["url"] for e in _cq.cache_list()["entries"]}
        assert "https://other.com/c" in remaining
        assert not any("github.com" in u for u in remaining)
    finally:
        _teardown_tmp_cache(orig)


def test_cache_invalidate_all_expired():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/fresh", ttl_days=7)
        _seed("https://example.com/old", ttl_days=0)
        import time
        time.sleep(0.05)
        r = _cq.cache_invalidate(all_expired=True)
        assert r["deleted"] == 1
        assert r["mode"] == "all_expired"
        remaining = {e["url"] for e in _cq.cache_list()["entries"]}
        assert "https://example.com/fresh" in remaining
    finally:
        _teardown_tmp_cache(orig)


def test_cache_invalidate_nonexistent_url_noop():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/a")
        r = _cq.cache_invalidate(url="https://nope.com/")
        assert r["deleted"] == 0
        assert _cq.cache_list()["total"] == 1
    finally:
        _teardown_tmp_cache(orig)


# ── 3. stats ──

def test_cache_stats_empty():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        r = _cq.cache_stats()
        assert r["total_entries"] == 0
        assert r["expired_entries"] == 0
        assert r["total_bytes"] == 0
        assert r["by_host"] == []
    finally:
        _teardown_tmp_cache(orig)


def test_cache_stats_aggregates_by_host():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://github.com/a")
        _seed("https://github.com/b")
        _seed("https://github.com/c")
        _seed("https://other.com/x")
        r = _cq.cache_stats()
        assert r["total_entries"] == 4
        assert r["expired_entries"] == 0
        assert r["total_bytes"] > 0
        top = r["by_host"][0]
        assert top["host"] == "github.com"
        assert top["count"] == 3
    finally:
        _teardown_tmp_cache(orig)


def test_cache_stats_counts_expired():
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        _setup_tmp_cache()
        _seed("https://example.com/fresh", ttl_days=7)
        _seed("https://example.com/old", ttl_days=0)
        import time
        time.sleep(0.05)
        r = _cq.cache_stats()
        assert r["total_entries"] == 2
        assert r["expired_entries"] == 1
    finally:
        _teardown_tmp_cache(orig)


# ── 4. 异常兜底 ──

def test_cache_list_handles_corrupted_files(monkeypatch=None):
    """损坏的 cache 文件应被跳过,不影响其他条目。"""
    orig = _cache._CACHE_DIR_OVERRIDE
    try:
        tmp = _setup_tmp_cache()
        _seed("https://example.com/good")
        # 写一个坏 JSON
        (tmp / "badfile.json").write_text("{not json", encoding="utf-8")
        r = _cq.cache_list()
        assert r["total"] == 1  # 只算好的
        assert r["entries"][0]["url"] == "https://example.com/good"
    finally:
        _teardown_tmp_cache(orig)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))