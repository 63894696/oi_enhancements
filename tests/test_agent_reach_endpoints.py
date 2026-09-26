# -*- coding: utf-8 -*-
"""tests/test_agent_reach_endpoints.py — P3j T20-B 端点 + capability 注册测试。

3 个 endpoint + 4 个 capability 的注册检查 + handler 调通(适配真实 CLI)。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. endpoints 注册
# ---------------------------------------------------------------------------

def test_endpoints_registered():
    from prisir_work import endpoints as ep
    expected = {
        "/web/reach/doctor": ("POST", "L0"),
        "/web/reach/read": ("POST", "L0"),
        "/web/reach/search": ("POST", "L0"),
        "/web/reach/platforms": ("POST", "L0"),
    }
    for path, (method, risk) in expected.items():
        e = ep._REGISTRY.get(path)
        assert e is not None, f"endpoint {path} 未注册"
        assert e["method"] == method, f"{path} method={e['method']}, want {method}"
        assert e["risk"] == risk, f"{path} risk={e['risk']}, want {risk}"


# ---------------------------------------------------------------------------
# 2. capability 注册
# ---------------------------------------------------------------------------

def test_capabilities_registered():
    from prisir_work import capability as cap
    expected_ids = [
        "web.reach.doctor",
        "web.reach.read",
        "web.reach.search",
        "web.reach.platforms",
    ]
    for cid in expected_ids:
        e = cap._REGISTRY.get(cid)
        assert e is not None, f"capability {cid} 未注册"
        assert e["risk"] == "L0", f"{cid} 应为 L0,实际 {e['risk']}"
        assert e["endpoint"].startswith("/web/reach/"), \
            f"{cid} endpoint={e['endpoint']}, want /web/reach/*"


def test_capability_keywords_chinese():
    """reach capability keywords 必须含中文 + 英文别名。"""
    from prisir_work import capability as cap
    e = cap._REGISTRY["web.reach.read"]
    kw = " ".join(e["keywords"])
    for cn in ("读小红书", "看视频字幕", "看 GitHub"):
        assert cn in kw, f"web.reach.read keywords 缺中文 '{cn}': {kw}"

    e2 = cap._REGISTRY["web.reach.search"]
    kw2 = " ".join(e2["keywords"])
    for cn in ("搜小红书", "搜 B站"):
        assert cn in kw2, f"web.reach.search keywords 缺中文 '{cn}': {kw2}"


# ---------------------------------------------------------------------------
# 3. 端点 handler 调通(用 mock subprocess)
# ---------------------------------------------------------------------------

def test_endpoint_read_returns_shape(monkeypatch):
    """测 _web_reach_read handler 返回 {ok, content, title} 形状。"""
    import json as _json
    from prisir_work import endpoints as ep

    payload = _json.dumps({
        "channel": "rss",
        "command": "feed",
        "items": [
            {"title": "B站视频", "url": "https://test/1",
             "text": "字幕内容", "author": "u", "published_at": ""}
        ],
    })
    monkeypatch.setattr("shutil.which", lambda x: "/fake/agent-reach")
    def _ok(*a, **kw):
        class _P:
            returncode = 0
            stdout = payload
            stderr = ""
        return _P()
    monkeypatch.setattr("subprocess.run", _ok)

    body = {"platform": "rss", "url": "https://hnrss.org/frontpage"}
    fn = ep._REGISTRY["/web/reach/read"]["handler"]
    payload_out, status = fn(body)
    assert payload_out["ok"] is True
    assert "字幕内容" in payload_out["content"]
    assert payload_out["title"] == "B站视频"


def test_endpoint_read_missing_fields():
    """_web_reach_read 缺参数 → ok=False + missing_fields。"""
    from prisir_work import endpoints as ep
    fn = ep._REGISTRY["/web/reach/read"]["handler"]
    r, status = fn({})
    assert r["ok"] is False
    assert r["error"] == "missing_fields"

    r2, _ = fn({"platform": "rss"})
    assert r2["ok"] is False


def test_endpoint_doctor_not_installed(monkeypatch):
    """_web_reach_doctor 在 agent-reach 未装时返 installed=False。"""
    monkeypatch.setattr("shutil.which", lambda x: None)

    from prisir_work import endpoints as ep
    fn = ep._REGISTRY["/web/reach/doctor"]["handler"]
    r, status = fn({})
    assert r["ok"] is True
    assert r["installed"] is False


def test_endpoint_read_channel_not_installed(monkeypatch):
    """_web_reach_read 在 channel 未装时返 reach_channel_not_installed + hint。"""
    monkeypatch.setattr("shutil.which", lambda x: "/fake/agent-reach")
    def _fail(*a, **kw):
        class _P:
            returncode = 1
            stdout = ""
            stderr = "agent-reach: no channel named 'xhs' in the index"
        return _P()
    monkeypatch.setattr("subprocess.run", _fail)

    from prisir_work import endpoints as ep
    fn = ep._REGISTRY["/web/reach/read"]["handler"]
    r, status = fn({"platform": "xhs", "url": "https://test"})
    assert r["ok"] is False
    assert r["error"] == "reach_channel_not_installed"
    assert "channel 未安装" in r.get("hint", "")


# ---------------------------------------------------------------------------
# 4. error translation 全覆盖
# ---------------------------------------------------------------------------

def test_reach_error_translations():
    """5 条 reach 错误 → zh 翻译 + link 跳转 /extensions。"""
    from prisir_work.agent_main_chat_hook import translate_exec_error
    expected = [
        ("agent_reach_not_installed",    "/extensions"),
        ("reach_channel_not_installed",  "/extensions"),
        ("reach_unknown_platform",       "/extensions"),
        ("reach_missing_query",          "/extensions"),
        ("agent_reach_timeout",          "/extensions"),
    ]
    for err, link in expected:
        tr = translate_exec_error(err)
        assert tr["zh"], f"{err} 没翻译成中文"
        assert tr["link"] == link, f"{err} link={tr['link']},want {link}"


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))