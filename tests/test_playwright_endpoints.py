# -*- coding: utf-8 -*-
"""tests/test_playwright_endpoints.py — P3j T25 端点白名单 + capability 集成测试。

5 个 mock case 覆盖:
  · endpoints._REGISTRY 含 8 playwright 端点 + risk 正确(click/type=L1,余 L0)
  · capability 8 个全部注册 + 含中英 keywords
  · endpoint handler 真调底层函数(mock 掉 pw_bridge 7 fn)
  · 4 错误翻译(playwright_*)入 agent_main_chat_hook._TRANSLATIONS
  · endpoint 拿掉 / 上游报错时 handler 返 200 + warnings,不抛栈
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. endpoints._REGISTRY 含 8 playwright + risk 正确
# ---------------------------------------------------------------------------

def test_endpoints_registry_has_8_paths():
    """8 playwright 端点全部登记 + click/type=L1 + 6=L0。"""
    from prisir_work import endpoints as _ep
    expected = ("/web/playwright/health", "/web/playwright/navigate",
                "/web/playwright/snapshot", "/web/playwright/click",
                "/web/playwright/type", "/web/playwright/evaluate",
                "/web/playwright/screenshot", "/web/playwright/close")
    for p in expected:
        assert p in _ep._REGISTRY, f"端点 {p} 未注册"
        assert _ep._REGISTRY[p]["method"] == "POST"
        assert _ep._REGISTRY[p]["auth"] is True
    # click/type = L1
    assert _ep._REGISTRY["/web/playwright/click"]["risk"] == "L1"
    assert _ep._REGISTRY["/web/playwright/type"]["risk"] == "L1"
    # 其余 = L0
    for p in ("/web/playwright/health", "/web/playwright/navigate",
              "/web/playwright/snapshot", "/web/playwright/evaluate",
              "/web/playwright/screenshot", "/web/playwright/close"):
        assert _ep._REGISTRY[p]["risk"] == "L0", f"{p} 应为 L0"


# ---------------------------------------------------------------------------
# 2. capability 8 个注册 + keywords + click/type 标 L1 + confirm
# ---------------------------------------------------------------------------

def test_capability_8_registered_with_keywords():
    """8 capability 全部注册 + keywords 非空 + click/type 标 L1 + confirm。"""
    from prisir_work import capability as _cap
    caps = {c["id"]: c for c in _cap.list_capabilities()}
    expected = ("web.playwright.health", "web.playwright.navigate",
                "web.playwright.snapshot", "web.playwright.click",
                "web.playwright.type", "web.playwright.evaluate",
                "web.playwright.screenshot", "web.playwright.close")
    for cid in expected:
        assert cid in caps, f"capability {cid} 未注册"
        assert caps[cid]["keywords"], f"{cid} 缺 keywords"
    # click/type = L1 + confirm
    assert caps["web.playwright.click"]["risk"] == "L1"
    assert caps["web.playwright.click"]["confirm"]
    assert caps["web.playwright.type"]["risk"] == "L1"
    assert caps["web.playwright.type"]["confirm"]


# ---------------------------------------------------------------------------
# 3. endpoint handler 真调底层函数(mock 掉 pw_bridge 7 fn)
# ---------------------------------------------------------------------------

def test_endpoint_navigate_ok(monkeypatch):
    """mock pw_navigate → endpoint handler 走通 + 透传 ok/content。"""
    from prisir_work import playwright_bridge as _pw
    monkeypatch.setattr(_pw, "pw_navigate",
                        lambda url, *, timeout=30.0: {
                            "ok": True, "is_error": False,
                            "content": "Navigated to " + url,
                            "raw": {"x": 1}})
    from prisir_work import endpoints as _ep
    body = {"url": "https://example.com"}
    handler = _ep._REGISTRY["/web/playwright/navigate"]["handler"]
    payload, status = handler(body)
    assert status == 200
    assert payload["ok"] is True
    assert payload["url"] == "https://example.com"
    assert "Navigated" in payload["content"]


def test_endpoint_click_ok(monkeypatch):
    """mock pw_click → /web/playwright/click handler 透传。"""
    from prisir_work import playwright_bridge as _pw
    monkeypatch.setattr(_pw, "pw_click",
                        lambda element, ref, *, timeout=30.0: {
                            "ok": True, "content": "Clicked " + element,
                            "is_error": False})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/playwright/click"]["handler"]
    payload, status = handler({"element": "登录", "ref": "e5"})
    assert status == 200
    assert payload["element"] == "登录"
    assert payload["ref"] == "e5"
    assert "Clicked" in payload["content"]


def test_endpoint_evaluate_ok(monkeypatch):
    """mock pw_evaluate → /web/playwright/evaluate handler。"""
    from prisir_work import playwright_bridge as _pw
    monkeypatch.setattr(_pw, "pw_evaluate",
                        lambda function, *, timeout=30.0: {
                            "ok": True,
                            "content": "42",
                            "is_error": False})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/playwright/evaluate"]["handler"]
    payload, status = handler({"function": "() => 6*7"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["content"] == "42"


def test_endpoint_close_ok(monkeypatch):
    """mock pw_close → /web/playwright/close handler。"""
    from prisir_work import playwright_bridge as _pw
    monkeypatch.setattr(_pw, "pw_close",
                        lambda: {"ok": True, "message": "Browser closed"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/playwright/close"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["message"] == "Browser closed"


def test_endpoint_health_returns_mode(monkeypatch):
    """mock pw_health → /web/playwright/health handler。"""
    from prisir_work import playwright_bridge as _pw
    monkeypatch.setattr(_pw, "pw_health",
                        lambda: {"ok": True, "mode": "ready",
                                 "node": "/usr/bin/node"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/playwright/health"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["mode"] == "ready"


# ---------------------------------------------------------------------------
# 4. 4 错误翻译入 agent_main_chat_hook._TRANSLATIONS
# ---------------------------------------------------------------------------

def test_playwright_error_translations_registered():
    """4+ 条 playwright_ 错误翻译 + 命中后返中文 + link。"""
    from prisir_work import agent_main_chat_hook as _h
    keys = [t[0] for t in _h._TRANSLATIONS]
    required = ("playwright_missing_node", "playwright_npx_failed",
                "playwright_not_initialized", "playwright_call_timeout")
    missing = [k for k in required if k not in keys]
    assert not missing, f"_TRANSLATIONS 缺 playwright key: {missing}"
    # 命中测试
    r = _h.translate_exec_error(
        "playwright_missing_node: 找不到 npx")
    assert "Node.js" in r["zh"]
    assert r["link"] == "https://nodejs.org/"
    r2 = _h.translate_exec_error(
        "playwright_call_timeout: 30s")
    assert r2["link"] == "/extensions"


# ---------------------------------------------------------------------------
# 5. 上游报错时 handler 返 200 + warnings,不抛栈
# ---------------------------------------------------------------------------

def test_endpoint_handler_does_not_raise(monkeypatch):
    """pw_bridge 上游 raise → handler 吞异常,返 200 + warnings。"""
    from prisir_work import playwright_bridge as _pw
    def boom(*a, **kw):
        raise RuntimeError("simulated crash")
    monkeypatch.setattr(_pw, "pw_navigate", boom)
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/playwright/navigate"]["handler"]
    payload, status = handler({"url": "https://x"})
    assert status == 200
    assert payload["ok"] is False
    assert "RuntimeError" in payload["warnings"]


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))