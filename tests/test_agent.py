"""test_agent.py — P2.5+17d LLM 驱动自主采集单测"""
import os, sys, tempfile, shutil
from pathlib import Path
import pytest

sys.path.insert(0, '.')

# 限定 web_search provider 走 mock(测试机无外网,ddg/baidu 会超时)
os.environ.setdefault('PRISIR_WEB_SEARCH_PROVIDERS', 'e2e_mock')

from prisir_work import agent as agent_mod
from prisir_work import web_fetch as wf_mod
from prisir_work import web_search as ws_mod
from prisir_work import cache as cache_mod


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    tmp = Path(tempfile.mkdtemp(prefix="agent_test_"))
    monkeypatch.setattr(cache_mod, '_CACHE_DIR_OVERRIDE', tmp)
    try:
        wf_mod._MEM_CACHE.clear()
    except Exception:
        pass
    ws_mod.clear_cache()
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ── mocks ──

SAMPLE = {
    "https://a.example/page": "<html><head><title>Title A</title></head>"
                              "<body><p>Content A</p></body></html>",
    "https://b.example/page": "<html><head><title>Title B</title></head>"
                              "<body><p>Content B</p></body></html>",
}

def setup_fetch_mock():
    def fetcher(url, options=None):
        if url in SAMPLE:
            return {'ok': True, 'url': url, 'content': SAMPLE[url],
                    'meta': {'status': 200, 'fetcher': 'mock'},
                    'fetcher': 'mock', 'cached': False, 'ok': True}
        return {'ok': False, 'url': url, 'content': '', 'warnings': ['mock_miss'], 'ok': False}
    wf_mod._FETCHERS.clear()
    wf_mod.register_fetcher('e2e_mock', fetcher)


def setup_search_mock():
    def provider(query, limit=10, providers=None, timeout=8.0):
        return [
            {'url': 'https://a.example/page', 'title': 'Title A',
             'snippet': 'snip A', 'score': 0.9, 'sources': ['mock']},
            {'url': 'https://b.example/page', 'title': 'Title B',
             'snippet': 'snip B', 'score': 0.7, 'sources': ['mock']},
        ]
    ws_mod.register_provider('e2e_mock', provider)


# ── 1. 模板决策(no LLM) ──

def test_agent_template_mode_basic():
    setup_fetch_mock()
    setup_search_mock()
    r = agent_mod.agent("test query", max_steps=5)
    assert r['ok'] is True
    assert r['mode'] == 'template'
    assert r['query'] == 'test query'
    assert len(r['findings']) >= 1
    # 第一个 action 应该是 search
    assert r['findings'][0]['action'] == 'search'


def test_agent_template_search_step_records_urls():
    setup_fetch_mock()
    setup_search_mock()
    r = agent_mod.agent("test query", max_steps=5)
    search = next(f for f in r['findings'] if f['action'] == 'search')
    assert 'urls' in search
    assert len(search['urls']) >= 1
    assert search['urls'][0]['url'].startswith('http')


def test_agent_template_stops_at_max_steps_or_sooner():
    setup_fetch_mock()
    setup_search_mock()
    r = agent_mod.agent("test query", max_steps=3)
    # 模板决策:step 0=search, 1=fetch(如果有 URL),2=stop
    actions = [f['action'] for f in r['findings']]
    assert 'stop' in actions or len(actions) <= 3


def test_agent_template_final_answer_non_empty():
    setup_fetch_mock()
    setup_search_mock()
    r = agent_mod.agent("test query", max_steps=5)
    assert r['answer']  # 非空(模板拼凑或 LLM 给)


# ── 2. LLM 决策 ──

def test_agent_llm_mode_search_action():
    setup_fetch_mock()
    setup_search_mock()

    def fake_llm(prompt):
        # 第一个 decision → search, 第二个 → stop
        if 'Step 0' in prompt:
            return json_dumps_action('search', {'query': 'test'})
        return json_dumps_action('stop', {'answer': 'LLM answer'})

    r = agent_mod.agent("test query", max_steps=5, llm_call=fake_llm)
    assert r['ok'] is True
    assert r['mode'] == 'llm'
    actions = [f['action'] for f in r['findings']]
    assert 'search' in actions
    assert 'stop' in actions
    assert r['answer'] == 'LLM answer'


def test_agent_llm_mode_fetch_action():
    setup_fetch_mock()
    setup_search_mock()

    def fake_llm(prompt):
        if 'Step 0' in prompt:
            return json_dumps_action('fetch', {'url': 'https://a.example/page'})
        return json_dumps_action('stop', {'answer': 'done'})

    r = agent_mod.agent("test query", max_steps=5, llm_call=fake_llm)
    fetch_steps = [f for f in r['findings'] if f['action'] == 'fetch']
    assert len(fetch_steps) == 1
    assert fetch_steps[0]['url'] == 'https://a.example/page'
    assert 'chars fetched' in fetch_steps[0]['result_summary']


def test_agent_llm_mode_extract_action():
    setup_fetch_mock()
    setup_search_mock()

    def fake_llm(prompt):
        if 'Step 0' in prompt:
            return json_dumps_action('extract',
                {'url': 'https://a.example/page', 'schema': ['title']})
        return json_dumps_action('stop', {'answer': 'extracted'})

    r = agent_mod.agent("test query", max_steps=5, llm_call=fake_llm)
    extract_steps = [f for f in r['findings'] if f['action'] == 'extract']
    assert len(extract_steps) == 1
    # extract 提取 title
    assert 'extracted' in extract_steps[0]['result_summary'] or 'title' in extract_steps[0]['result_summary']


def test_agent_llm_bad_json_falls_back_to_template():
    setup_fetch_mock()
    setup_search_mock()

    def fake_llm(prompt):
        return "this is not JSON"  # 解析失败 → 模板降级

    r = agent_mod.agent("test query", max_steps=4, llm_call=fake_llm)
    assert r['ok'] is True
    # 模式应该标记 fallback
    assert r['mode'] in ('template_fallback', 'template')
    # 第一步仍是 search(模板默认)
    assert r['findings'][0]['action'] == 'search'


def test_agent_llm_exception_falls_back_to_template():
    setup_fetch_mock()
    setup_search_mock()

    def fake_llm(prompt):
        raise RuntimeError("LLM offline")

    r = agent_mod.agent("test query", max_steps=4, llm_call=fake_llm)
    assert r['ok'] is True
    assert any(w.startswith('llm_failed_step_') for w in r['warnings'])
    assert r['mode'] == 'template_fallback'


# ── 3. 边界 ──

def test_agent_empty_query():
    r = agent_mod.agent('', max_steps=3)
    assert r['ok'] is False
    assert 'empty_query' in r['warnings']


def test_agent_max_steps_caps_findings():
    setup_fetch_mock()
    setup_search_mock()

    def fake_llm(prompt):
        # 永远不停
        return json_dumps_action('search', {'query': 'more'})

    r = agent_mod.agent("test query", max_steps=2, llm_call=fake_llm)
    # 找到 stop OR max_steps 用尽
    assert len(r['findings']) <= 2
    # 没有 stop 时 answer 仍然非空(从 findings 拼)
    assert r['answer']


def test_agent_findings_have_step_field():
    setup_fetch_mock()
    setup_search_mock()
    r = agent_mod.agent("test", max_steps=3)
    for f in r['findings']:
        assert 'step' in f
        assert 'action' in f


# ── helper ──

def json_dumps_action(action, args):
    import json
    return json.dumps({"action": action, "args": args, "reason": "test"})


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))