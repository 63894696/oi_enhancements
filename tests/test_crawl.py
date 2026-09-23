"""test_crawl.py — P2.5+17c BFS 爬虫单测"""
import sys, tempfile, shutil
from pathlib import Path
import pytest

sys.path.insert(0, '.')

from prisir_work import crawl as crawl_mod
from prisir_work import web_fetch as wf_mod
from prisir_work import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="crawl_test_"))
    monkeypatch.setattr(cache_mod, '_CACHE_DIR_OVERRIDE', tmp)
    try:
        wf_mod._MEM_CACHE.clear()
    except Exception:
        pass
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ── mock:树状站点 ──
TREE = {
    # /  含 3 个链接 → /a, /b, /external
    "https://x.example/": '''<html>
<head><title>Home</title></head>
<body>
<a href="/a">A</a>
<a href="/b">B</a>
<a href="https://other.com/x">External</a>
<a href="#anchor">Anchor</a>
<a href="javascript:void(0)">JS</a>
</body></html>''',
    "https://x.example/a": '''<html>
<head><title>Page A</title></head>
<body><a href="/">Home</a><a href="/a/sub">Sub</a></body></html>''',
    "https://x.example/b": '''<html>
<head><title>Page B</title></head>
<body><a href="/">Home</a></body></html>''',
    "https://x.example/a/sub": '''<html>
<head><title>Subpage</title></head>
<body><a href="/a">Back</a></body></html>''',
}


def setup_tree_mock():
    def fetcher(url, options=None):
        if url in TREE:
            return {'ok': True, 'url': url, 'content': TREE[url],
                    'meta': {'status': 200, 'fetcher': 'mock'},
                    'fetcher': 'mock', 'cached': False, 'ok': True}
        return {'ok': False, 'url': url, 'content': '', 'warnings': ['mock_miss'], 'ok': False}
    wf_mod._FETCHERS.clear()
    try:
        wf_mod._MEM_CACHE.clear()
    except Exception:
        pass
    wf_mod.register_fetcher('e2e_mock', fetcher)


# ── 1. BFS 基础 ──

def test_crawl_bfs_visits_start_first():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=10, max_depth=1, rate_per_host=0)
    assert r['ok'] is True
    assert r['start_url'] == 'https://x.example/'
    urls = [p['url'] for p in r['pages']]
    assert urls[0] == 'https://x.example/'


def test_crawl_bfs_depth_1_finds_a_b():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=10, max_depth=1, rate_per_host=0)
    urls = {p['url'] for p in r['pages']}
    assert 'https://x.example/a' in urls
    assert 'https://x.example/b' in urls


def test_crawl_bfs_depth_2_finds_sub():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=10, max_depth=2, rate_per_host=0)
    urls = {p['url'] for p in r['pages']}
    assert 'https://x.example/a/sub' in urls


def test_crawl_depth_limit_respected():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=10, max_depth=0, rate_per_host=0)
    urls = {p['url'] for p in r['pages']}
    assert urls == {'https://x.example/'}


def test_crawl_max_pages_limit():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=2, max_depth=2, rate_per_host=0)
    assert len(r['pages']) <= 2


def test_crawl_same_host_filters_external():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=20, max_depth=1, rate_per_host=0)
    assert all(p['host'] == 'x.example' for p in r['pages'])
    # external 在 skipped
    assert any(s['url'] == 'https://other.com/x' for s in r['skipped'])


def test_crawl_filters_anchor_and_javascript():
    """#anchor 和 javascript: 不应进入 seen,也不应作为新链接被爬"""
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=20, max_depth=2, rate_per_host=0)
    urls = {p['url'] for p in r['pages']}
    assert not any('#anchor' in u for u in urls)
    assert not any('javascript:' in u for u in urls)


# ── 2. 去重 ──

def test_crawl_no_duplicate_pages():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=20, max_depth=3, rate_per_host=0)
    urls = [p['url'] for p in r['pages']]
    assert len(urls) == len(set(urls))  # 无重复


# ── 3. 失败兜底 ──

def test_crawl_empty_url():
    r = crawl_mod.crawl('', max_pages=10, max_depth=2)
    assert r['ok'] is False
    assert 'empty_url' in r['warnings']


def test_crawl_bad_start_url():
    r = crawl_mod.crawl('not-a-url', max_pages=10, max_depth=2)
    # urlparse hostname 失败 → bad_start_url
    assert r['ok'] is False or len(r['pages']) == 0


def test_crawl_skipped_records_failures():
    """mock 拿不到的 URL 应进 skipped,不影响其他页面"""
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=20, max_depth=2, rate_per_host=0)
    # /a/sub 应该在 pages(树中所有 URL 都能 mock 到)
    urls = {p['url'] for p in r['pages']}
    assert 'https://x.example/a/sub' in urls
    # 外部被 skip
    assert any('other.com' in s['url'] for s in r['skipped'])


# ── 4. 页面元数据 ──

def test_crawl_pages_have_metadata():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=20, max_depth=2, rate_per_host=0)
    for p in r['pages']:
        assert 'url' in p
        assert 'host' in p
        assert 'depth' in p
        assert 'size_chars' in p
        assert 'fetched_at' in p
        assert p['size_chars'] > 0


def test_crawl_title_extracted():
    setup_tree_mock()
    r = crawl_mod.crawl('https://x.example/', max_pages=10, max_depth=1, rate_per_host=0)
    home = next(p for p in r['pages'] if p['url'] == 'https://x.example/')
    assert home['title'] == 'Home'


def test_crawl_rate_limit_respected():
    """rate_per_host=1.0 抓 2 页应该 ≥ 1s"""
    setup_tree_mock()
    t0 = time_mod()
    crawl_mod.crawl('https://x.example/', max_pages=2, max_depth=1, rate_per_host=1.0)
    elapsed = time_mod() - t0
    assert elapsed >= 0.9  # 至少等 1s


def time_mod():
    import time
    return time.monotonic()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))