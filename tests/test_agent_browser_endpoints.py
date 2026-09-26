# -*- coding: utf-8 -*-
"""tests/test_agent_browser_endpoints.py — P3j T26 端点白名单 + capability 集成测试。

5 个 mock case 覆盖:
  · endpoints._REGISTRY 含 8 agent-browser 端点 + risk 正确(click/fill=L1,余 L0)
  · capability 8 个全部注册 + 含中英 keywords
  · endpoint handler 真调底层函数(mock 掉 ab_bridge 8 fn)
  · 6 错误翻译(ab_*)入 agent_main_chat_hook._TRANSLATIONS
  · endpoint handler 异常时返 200 + warnings,不抛栈
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. endpoints._REGISTRY 含 8 agent-browser + risk 正确
# ---------------------------------------------------------------------------

def test_endpoints_registry_has_8_paths():
    """8 agent-browser 端点全部登记 + click/fill=L1 + 6=L0。"""
    from prisir_work import endpoints as _ep
    expected = ("/web/agent-browser/health", "/web/agent-browser/open",
                "/web/agent-browser/snapshot", "/web/agent-browser/click",
                "/web/agent-browser/fill", "/web/agent-browser/eval",
                "/web/agent-browser/screenshot", "/web/agent-browser/close")
    for p in expected:
        assert p in _ep._REGISTRY, f"端点 {p} 未注册"
        assert _ep._REGISTRY[p]["method"] == "POST"
        assert _ep._REGISTRY[p]["auth"] is True
    # click/fill = L1
    assert _ep._REGISTRY["/web/agent-browser/click"]["risk"] == "L1"
    assert _ep._REGISTRY["/web/agent-browser/fill"]["risk"] == "L1"
    # 其余 = L0
    for p in ("/web/agent-browser/health", "/web/agent-browser/open",
              "/web/agent-browser/snapshot", "/web/agent-browser/eval",
              "/web/agent-browser/screenshot", "/web/agent-browser/close"):
        assert _ep._REGISTRY[p]["risk"] == "L0", f"{p} 应为 L0"


# ---------------------------------------------------------------------------
# 2. capability 8 个注册 + keywords + click/fill 标 L1 + confirm
# ---------------------------------------------------------------------------

def test_capability_8_registered_with_keywords():
    """8 capability 全部注册 + keywords 非空 + click/fill 标 L1 + confirm。"""
    from prisir_work import capability as _cap
    caps = {c["id"]: c for c in _cap.list_capabilities()}
    expected = ("web.agent-browser.health", "web.agent-browser.open",
                "web.agent-browser.snapshot", "web.agent-browser.click",
                "web.agent-browser.fill", "web.agent-browser.eval",
                "web.agent-browser.screenshot", "web.agent-browser.close")
    for cid in expected:
        assert cid in caps, f"capability {cid} 未注册"
        assert caps[cid]["keywords"], f"{cid} 缺 keywords"
    # click/fill = L1 + confirm
    assert caps["web.agent-browser.click"]["risk"] == "L1"
    assert caps["web.agent-browser.click"]["confirm"]
    assert caps["web.agent-browser.fill"]["risk"] == "L1"
    assert caps["web.agent-browser.fill"]["confirm"]


# ---------------------------------------------------------------------------
# 3. endpoint handler 真调底层函数(mock 掉 ab_bridge 8 fn)
# ---------------------------------------------------------------------------

def test_endpoint_open_ok(monkeypatch):
    """mock ab_open → /web/agent-browser/open handler 透传 url + ok。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr(_ab, "ab_open",
                        lambda url, *, timeout=30.0: {
                            "ok": True, "url": url,
                            "data": {"title": "Example"},
                            "message": "Opened"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/open"]["handler"]
    payload, status = handler({"url": "https://example.com"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["url"] == "https://example.com"
    assert "Opened" in payload["message"]


def test_endpoint_snapshot_ok(monkeypatch):
    """mock ab_snapshot → handler 透传 tree + refs。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr(_ab, "ab_snapshot",
                        lambda *, depth=3, interactive_only=False, timeout=30.0: {
                            "ok": True, "tree": "@e1 Login\n@e2 Email",
                            "refs": ["@e1", "@e2"],
                            "raw": {}})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/snapshot"]["handler"]
    payload, status = handler({"depth": 5})
    assert status == 200
    assert payload["ok"] is True
    assert "@e1" in payload["refs"]
    assert "@e2" in payload["refs"]


def test_endpoint_click_ok(monkeypatch):
    """mock ab_click → /web/agent-browser/click handler 透传 ref + ok。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr(_ab, "ab_click",
                        lambda ref, *, timeout=30.0: {
                            "ok": True, "ref": ref,
                            "message": "Clicked"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/click"]["handler"]
    payload, status = handler({"ref": "@e1"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["ref"] == "@e1"


def test_endpoint_fill_ok(monkeypatch):
    """mock ab_fill → /web/agent-browser/fill handler 透传 text + submit。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr(_ab, "ab_fill",
                        lambda ref, text, *, submit=False, slowly=False,
                               timeout=30.0: {
                            "ok": True, "ref": ref, "text": text,
                            "submit": submit, "slowly": slowly,
                            "message": "Filled"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/fill"]["handler"]
    payload, status = handler({"ref": "@e2", "text": "hello",
                               "submit": True})
    assert status == 200
    assert payload["ok"] is True
    assert payload["text"] == "hello"
    assert payload["submit"] is True


def test_endpoint_eval_ok(monkeypatch):
    """mock ab_eval → /web/agent-browser/eval handler 透传 result。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr(_ab, "ab_eval",
                        lambda js, *, timeout=30.0: {
                            "ok": True, "result": "Example Domain",
                            "js": js[:200]})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/eval"]["handler"]
    payload, status = handler({"js": "() => document.title"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["result"] == "Example Domain"


def test_endpoint_close_ok(monkeypatch):
    """mock ab_close → /web/agent-browser/close handler。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr(_ab, "ab_close",
                        lambda: {"ok": True, "message": "Browser closed"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/close"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["message"] == "Browser closed"


def test_endpoint_health_returns_mode(monkeypatch):
    """mock ab_health → /web/agent-browser/health handler。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr(_ab, "ab_health",
                        lambda *, timeout=10.0: {
                            "ok": True, "mode": "ready",
                            "installed": True,
                            "version": "0.5.0"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/health"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["mode"] == "ready"


def test_endpoint_click_missing_ref():
    """click 缺 ref → 200 + error=missing_ref(不调底层)。"""
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/click"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "missing_ref"


def test_endpoint_fill_missing_ref():
    """fill 缺 ref → 200 + error=missing_ref。"""
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/fill"]["handler"]
    payload, status = handler({"text": "hi"})
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "missing_ref"


def test_endpoint_open_empty_url():
    """open 缺 url → 200 + error=empty_url。"""
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/open"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "empty_url"


def test_endpoint_eval_empty_js():
    """eval 缺 js → 200 + error=empty_js。"""
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/eval"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "empty_js"


# ---------------------------------------------------------------------------
# 4. 6 错误翻译入 agent_main_chat_hook._TRANSLATIONS
# ---------------------------------------------------------------------------

def test_agent_browser_error_translations_registered():
    """6 条 ab_ 错误翻译 + 命中后返中文 + link。"""
    from prisir_work import agent_main_chat_hook as _h
    keys = [t[0] for t in _h._TRANSLATIONS]
    required = ("ab_cli_not_found", "ab_install_failed", "ab_daemon_failed",
                "ab_timeout", "ab_invalid_ref", "ab_unsupported_engine")
    missing = [k for k in required if k not in keys]
    assert not missing, f"_TRANSLATIONS 缺 ab_ key: {missing}"
    # 命中测试
    r = _h.translate_exec_error("ab_cli_not_found: 不在 PATH")
    assert "agent-browser" in r["zh"]
    assert "npm install" in r["zh"] or "agent-browser" in r["zh"]
    r2 = _h.translate_exec_error("ab_invalid_ref: @e99 失效")
    assert "ref" in r2["zh"].lower() or "snapshot" in r2["zh"]
    r3 = _h.translate_exec_error("ab_timeout: 30s")
    assert r3["link"] == "/extensions"


# ---------------------------------------------------------------------------
# 5. 上游报错时 handler 返 200 + warnings,不抛栈
# ---------------------------------------------------------------------------

def test_endpoint_handler_does_not_raise(monkeypatch):
    """ab_bridge 上游 raise → handler 吞异常,返 200 + warnings。"""
    from prisir_work import agent_browser_bridge as _ab
    def boom(*a, **kw):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(_ab, "ab_open", boom)
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/agent-browser/open"]["handler"]
    payload, status = handler({"url": "https://x"})
    assert status == 200
    assert payload["ok"] is False
    assert "RuntimeError" in payload["warnings"]


# ---------------------------------------------------------------------------
# 6. 跟 playwright namespace 不冲突
# ---------------------------------------------------------------------------

def test_endpoints_no_conflict_with_playwright():
    """agent-browser 跟 playwright 路径前缀不撞(命名空间分开)。"""
    from prisir_work import endpoints as _ep
    ab_paths = [p for p in _ep._REGISTRY if "agent-browser" in p]
    pw_paths = [p for p in _ep._REGISTRY if "playwright" in p]
    assert len(ab_paths) == 8
    assert len(pw_paths) == 8
    # 路径前缀互斥
    for p in ab_paths:
        assert "playwright" not in p
    for p in pw_paths:
        assert "agent-browser" not in p


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))