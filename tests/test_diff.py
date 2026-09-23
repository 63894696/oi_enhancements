"""test_diff.py — P2.5+17b diff 页面版本对比单测"""
import os, sys, json, tempfile, shutil
from pathlib import Path
import pytest

sys.path.insert(0, '.')

from prisir_work import diff as diff_mod
from prisir_work import web_fetch as wf_mod
from prisir_work import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch):
    """每个测试用 tmpdir 隔离 cache,避免不同测试间或与磁盘历史冲突。"""
    tmp = Path(tempfile.mkdtemp(prefix="diff_test_"))
    monkeypatch.setattr(cache_mod, '_CACHE_DIR_OVERRIDE', tmp)
    # 也清内存 LRU
    try:
        wf_mod._MEM_CACHE.clear()
    except Exception:
        pass
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ── mock fetcher ──
def make_mock(pages: dict):
    """pages: dict[url] = html"""
    def fetcher(url, options=None):
        if url in pages:
            return {'ok': True, 'url': url, 'content': pages[url],
                    'meta': {'status': 200, 'fetcher': 'mock'},
                    'fetcher': 'mock', 'cached': False, 'ok': True}
        return {'ok': False, 'url': url, 'content': '',
                'warnings': ['mock_not_found'], 'ok': False}
    return fetcher


def setup_mock(pages):
    wf_mod._FETCHERS.clear()
    try:
        wf_mod._MEM_CACHE.clear()
    except Exception:
        pass
    wf_mod.register_fetcher('e2e_mock', make_mock(pages))


# ── 1. mode='url' 基础 ──

def test_diff_url_mode_identical():
    setup_mock({'https://a.example': '<html><body><h1>Same</h1><p>text</p></body></html>',
                'https://b.example': '<html><body><h1>Same</h1><p>text</p></body></html>'})
    r = diff_mod.diff('https://a.example', 'https://b.example', mode='url')
    assert r['ok'] is True
    assert r['mode'] == 'url'
    assert r['unchanged'] is True
    assert r['diff'] == ''
    assert r['summary']['changed_lines'] == 0


def test_diff_url_mode_differs():
    setup_mock({'https://a.example': '<html><body><h1>OldHeading</h1><p>foo</p></body></html>',
                'https://b.example': '<html><body><h1>NewHeading</h1><p>bar</p></body></html>'})
    r = diff_mod.diff('https://a.example', 'https://b.example', mode='url')
    assert r['unchanged'] is False
    # strip 后 OldHeading/NewHeading 留作单行,foo/bar 各一行
    assert '-OldHeading' in r['diff']
    assert '+NewHeading' in r['diff']
    assert r['summary']['added_lines'] >= 1
    assert r['summary']['removed_lines'] >= 1


def test_diff_url_mode_strips_html_by_default():
    """HTML tag 差异不应进入 diff(默认 raw=False)"""
    setup_mock({'https://a.example': '<html><body><p>hello</p></body></html>',
                'https://b.example': '<html><body><span>hello</span></body></html>'})
    r = diff_mod.diff('https://a.example', 'https://b.example', mode='url')
    assert r['unchanged'] is True  # strip 后都只剩 hello


def test_diff_url_mode_raw_compares_raw_html():
    setup_mock({'https://a.example': '<p>hello world</p>\n<p>line2</p>',
                'https://b.example': '<span>hello world</span>\n<p>line2 changed</p>'})
    r = diff_mod.diff('https://a.example', 'https://b.example', mode='url', raw=True)
    assert r['unchanged'] is False
    assert r['summary']['changed_lines'] > 0


# ── 2. mode='time' 缓存快照 ──

def test_diff_time_mode_with_cache_history():
    setup_mock({'https://a.example': '<html><body><h1>New</h1></body></html>'})
    # 先存一个旧快照到 cache
    from prisir_work import cache as cache_mod
    cache_mod.cache_put('https://a.example', {'content': '<html><body><h1>Old</h1></body></html>'})
    r = diff_mod.diff('https://a.example', mode='time')
    assert r['ok'] is True
    assert r['mode'] == 'time'
    assert r['a_meta']['source'] == 'cache'
    assert r['b_meta']['source'] == 'live'
    assert r['unchanged'] is False
    assert r['summary']['changed_lines'] >= 1


def test_diff_time_mode_no_cache_snapshots_current():
    """cache 没历史 + snapshot_b=True → 把当前存到 cache,warnings=snapshot_saved"""
    setup_mock({'https://a.example': '<html><body><h1>First</h1></body></html>'})
    # 清掉 cache 该 url
    from prisir_work import cache_query as cq
    cq.cache_invalidate(url='https://a.example')
    r = diff_mod.diff('https://a.example', mode='time', snapshot_b=True)
    assert r['ok'] is True
    assert 'snapshot_saved_for_baseline' in r['warnings']
    # cache 现在应有这条
    from prisir_work import cache as cache_mod
    rec = cache_mod.cache_get('https://a.example')
    assert rec is not None
    assert 'First' in rec['payload']['content']


def test_diff_time_mode_no_cache_no_snapshot():
    """cache 没历史 + snapshot_b=False → a 空,b 当前 → unchanged=False(从无到有算变化),
    但应警告 cache_miss 且 b_meta.source='live'。"""
    setup_mock({'https://a.example': '<html><body><h1>Now</h1></body></html>'})
    from prisir_work import cache_query as cq
    cq.cache_invalidate(url='https://a.example')
    r = diff_mod.diff('https://a.example', mode='time', snapshot_b=False)
    assert r['ok'] is True
    assert r['unchanged'] is False
    assert 'cache_miss' in r['warnings']
    assert r['a_meta']['source'] == 'empty'
    assert r['b_meta']['source'] == 'live'
    # a 空('') vs b 'Now' → 至少一侧有变更行
    assert r['summary']['changed_lines'] >= 1


# ── 3. 失败兜底 ──

def test_diff_url_mode_fetch_failure():
    setup_mock({})  # 啥都没
    r = diff_mod.diff('https://nope1.example', 'https://nope2.example', mode='url')
    assert r['ok'] is True
    assert 'fetch_failed' in r['warnings']
    assert r['unchanged'] is True  # 都空


def test_diff_empty_url():
    r = diff_mod.diff('', 'https://b.example', mode='url')
    assert r['ok'] is False
    assert 'empty_url' in r['warnings']


def test_diff_invalid_mode():
    r = diff_mod.diff('https://a.example', mode='bogus')
    assert r['ok'] is False
    assert 'invalid_mode' in r['warnings']


def test_diff_url_mode_empty_b():
    r = diff_mod.diff('https://a.example', '', mode='url')
    assert r['ok'] is False


# ── 4. summary 字段完整性 ──

def test_diff_summary_fields():
    setup_mock({'https://a.example': 'aaa\nbbb',
                'https://b.example': 'aaa\nccc\nddd'})
    r = diff_mod.diff('https://a.example', 'https://b.example', mode='url', raw=True)
    assert 'added_lines' in r['summary']
    assert 'removed_lines' in r['summary']
    assert 'changed_lines' in r['summary']
    assert 'a_chars' in r['summary']
    assert 'b_chars' in r['summary']
    assert r['summary']['changed_lines'] == r['summary']['added_lines'] + r['summary']['removed_lines']


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))