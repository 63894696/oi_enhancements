"""test_health.py — P2.5+18a web health 诊断单测"""
import os, sys, tempfile, shutil
from pathlib import Path
import pytest

sys.path.insert(0, '.')

from prisir_work import health as health_mod
from prisir_work import web_fetch as wf_mod
from prisir_work import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    tmp_parent = Path(tempfile.mkdtemp(prefix="health_parent_"))
    tmp = tmp_parent / "web"
    tmp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cache_mod, '_CACHE_DIR_OVERRIDE', tmp)
    yield tmp
    shutil.rmtree(tmp_parent, ignore_errors=True)


# ── 1. cache 子项 ──

def test_health_cache_writable():
    r = health_mod._cache_dir_writable()
    assert r['ok'] is True
    assert 'path' in r
    assert r['entries'] == 0


def test_health_cache_writable_with_entries():
    cache_mod.cache_put('https://x.example/a', {'content': 'a'})
    cache_mod.cache_put('https://x.example/b', {'content': 'b'})
    r = health_mod._cache_dir_writable()
    assert r['ok'] is True
    assert r['entries'] == 2


def test_health_cache_writable_handles_overrides():
    """monkeypatch 已经在 fixture 里做;测 ok 字段。"""
    r = health_mod._cache_dir_writable()
    assert 'ok' in r
    assert 'path' in r


# ── 2. fetcher 子项 ──

def test_health_fetcher_lists_registered():
    wf_mod._FETCHERS.clear()
    def m1(url, options=None): return {'ok': True, 'url': url, 'content': ''}
    def m2(url, options=None): return {'ok': True, 'url': url, 'content': ''}
    wf_mod.register_fetcher('http_urllib', m1)
    wf_mod.register_fetcher('e2e_mock', m2)
    r = health_mod._fetcher_health()
    assert r['ok'] is True
    assert r['count'] == 2
    assert 'http_urllib' in r['names']
    assert 'e2e_mock' in r['names']
    assert r['has_mocks'] is True


def test_health_fetcher_empty():
    wf_mod._FETCHERS.clear()
    r = health_mod._fetcher_health()
    assert r['ok'] is True
    assert r['count'] == 0


# ── 3. provider 子项 ──

def test_health_search_provider_lists():
    from prisir_work import web_search as ws_mod
    initial = len(ws_mod._PROVIDERS)
    r = health_mod._search_provider_health()
    assert r['ok'] is True
    assert r['count'] == initial
    assert 'ddg_html' in r['names']


# ── 4. endpoint/capability 子项 ──

def test_health_endpoints_lists_web_paths():
    r = health_mod._endpoint_health()
    assert r['ok'] is True
    assert r['total_endpoints'] >= 10  # 至少有基础 + web 套件
    assert '/web/search' in r['web_endpoints']
    assert '/web/fetch' in r['web_endpoints']
    assert '/web/diff' in r['web_endpoints']
    assert '/web/crawl' in r['web_endpoints']
    assert '/web/agent' in r['web_endpoints']
    assert '/web/health' in r['web_endpoints']
    # capabilities
    assert 'web.search' in r['web_capabilities']


# ── 5. tune 子项 ──

def test_health_tune_no_file():
    """没 tune.json → ok=True + note + 0 domains"""
    r = health_mod._tune_health()
    assert r['ok'] is True
    assert r['tuned_domains'] == 0
    assert 'note' in r


# ── 6. web_health 聚合 ──

def test_web_health_basic():
    r = health_mod.web_health()
    assert 'checked_at' in r
    assert isinstance(r['warnings'], list)
    assert 'cache' in r
    assert 'fetchers' in r
    assert 'search_providers' in r
    assert 'endpoints_capabilities' in r
    assert 'tune' in r


def test_web_health_warns_no_fetchers():
    wf_mod._FETCHERS.clear()
    r = health_mod.web_health()
    assert 'no_fetchers_registered' in r['warnings']


def test_web_health_exception_safety():
    """任何子项异常 → 整个仍返 ok 字段,不抛"""
    import prisir_work.health as h2
    orig = h2._cache_dir_writable
    def broken():
        raise RuntimeError("disk error")
    h2._cache_dir_writable = broken
    try:
        r = h2.web_health()
        # 子项自身不抛,但 web_health 聚合层 catch 住
        assert any('cache_error' in w or 'cache_unwritable' in w for w in r['warnings']), \
            'warnings=' + str(r['warnings'])
    finally:
        h2._cache_dir_writable = orig


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))