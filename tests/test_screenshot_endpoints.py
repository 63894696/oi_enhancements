# -*- coding: utf-8 -*-
"""tests/test_screenshot_endpoints.py — P3j T28 端点白名单 + capability 集成测试。

~12 个 mock case 覆盖:
  · endpoints._REGISTRY 含 6 screenshot 端点 + 全部 risk=L0
  · capability 6 个全部注册 + 含中英 keywords
  · endpoint handler 真调底层函数(mock 掉 ss_bridge 6 fn)
  · 9 错误翻译(ss_*)入 agent_main_chat_hook._TRANSLATIONS
  · endpoint handler 异常时返 200 + warnings,不抛栈
  · namespace 不冲突(截图能力 vs agent-browser 能力)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. endpoints._REGISTRY 含 6 screenshot 端点 + 全部 risk=L0
# ---------------------------------------------------------------------------

def test_endpoints_registry_has_6_paths():
    """6 screenshot 端点全部登记 + 全部 L0(截图本质只读)。"""
    from prisir_work import endpoints as _ep
    expected = ("/web/screenshot/health", "/web/screenshot/capture",
                "/web/screenshot/list", "/web/screenshot/read",
                "/web/screenshot/active_backend",
                "/web/screenshot/install_hint")
    for p in expected:
        assert p in _ep._REGISTRY, f"端点 {p} 未注册"
        assert _ep._REGISTRY[p]["method"] == "POST"
        assert _ep._REGISTRY[p]["auth"] is True
        assert _ep._REGISTRY[p]["risk"] == "L0", f"{p} 应为 L0"


# ---------------------------------------------------------------------------
# 2. capability 6 个注册 + keywords
# ---------------------------------------------------------------------------

def test_capability_6_registered_with_keywords():
    """6 capability 全部注册 + keywords 非空 + L0。"""
    from prisir_work import capability as _cap
    caps = {c["id"]: c for c in _cap.list_capabilities()}
    expected = ("web.screenshot.health", "web.screenshot.capture",
                "web.screenshot.list", "web.screenshot.read",
                "web.screenshot.active_backend",
                "web.screenshot.install_hint")
    for cid in expected:
        assert cid in caps, f"capability {cid} 未注册"
        assert caps[cid]["keywords"], f"{cid} 缺 keywords"
        assert caps[cid]["risk"] == "L0", f"{cid} 应为 L0"
        assert caps[cid]["endpoint"].startswith("/web/screenshot/")


# ---------------------------------------------------------------------------
# 3. endpoint handler 真调底层函数(mock 掉 ss_bridge 6 fn)
# ---------------------------------------------------------------------------

def test_endpoint_capture_ok(monkeypatch):
    """mock ss_capture → /web/screenshot/capture handler 透传 path + ok。"""
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr(_ss, "ss_capture",
                        lambda mode, *, area="", filename="",
                               output_dir="", timeout=30.0: {
                            "ok": True, "mode": mode, "path": "/tmp/x.png"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/capture"]["handler"]
    payload, status = handler({"mode": "fullscreen",
                                "filename": "shot.png"})
    assert status == 200
    assert payload["ok"] is True
    assert payload["mode"] == "fullscreen"
    assert payload["path"] == "/tmp/x.png"


def test_endpoint_capture_bad_mode(monkeypatch):
    """ss_capture 返 ss_bad_mode → handler 透传 ok=False。"""
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr(_ss, "ss_capture",
                        lambda *a, **kw: {"ok": False,
                                          "error": "ss_bad_mode"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/capture"]["handler"]
    payload, status = handler({"mode": "bad"})
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "ss_bad_mode"


def test_endpoint_health_ok(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr(_ss, "ss_health",
                        lambda *, timeout=10.0: {"ok": True,
                                                  "mode": "ready",
                                                  "version": "1.2.3"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/health"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["mode"] == "ready"
    assert payload["version"] == "1.2.3"


def test_endpoint_list_ok(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr(_ss, "ss_list",
                        lambda *, limit=20, output_dir="": {
                            "ok": True, "entries": [
                                {"name": "a.png", "size": 1024}],
                            "total": 1})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/list"]["handler"]
    payload, status = handler({"limit": 5})
    assert status == 200
    assert payload["total"] == 1


def test_endpoint_read_empty_path():
    """空 path → ss_empty_path。"""
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/read"]["handler"]
    payload, status = handler({"path": ""})
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "ss_empty_path"


def test_endpoint_read_ok(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr(_ss, "ss_read",
                        lambda path: {"ok": True, "path": path,
                                      "width": 320, "height": 240})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/read"]["handler"]
    payload, status = handler({"path": "/tmp/x.png"})
    assert status == 200
    assert payload["width"] == 320


def test_endpoint_active_backend(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr(_ss, "ss_active_backend",
                        lambda: {"ok": True, "platform": "linux",
                                 "backend": "grim", "available": True})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/active_backend"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["backend"] == "grim"


def test_endpoint_install_hint(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr(_ss, "ss_install_hint",
                        lambda: {"ok": True, "hint": "...",
                                 "command": "npm install -g screenshot-mcp"})
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/install_hint"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert "npm install" in payload["command"]


# ---------------------------------------------------------------------------
# 4. 9 错误翻译(ss_*)入 agent_main_chat_hook._TRANSLATIONS
# ---------------------------------------------------------------------------

def test_error_translations_ss_keys_present():
    """9 个 ss_* key 全部在 _TRANSLATIONS。"""
    from prisir_work.agent_main_chat_hook import _TRANSLATIONS
    keys = {k for k, _, _ in _TRANSLATIONS}
    expected = ("ss_cli_not_found", "ss_timeout", "ss_no_backend",
                "ss_invalid_area", "ss_failed", "ss_bad_mode",
                "ss_bad_filename", "ss_not_found", "ss_node_too_old")
    for k in expected:
        assert k in keys, f"错误翻译 {k} 未注册"


def test_error_translations_ss_zh_filled():
    """ss_* 翻译 zh 都非空;hint 允许空(输入校验类,如 ss_invalid_area
    / ss_bad_mode 已是 self-explanatory,无 hint 必要)。"""
    from prisir_work.agent_main_chat_hook import _TRANSLATIONS
    for k, zh, hint in _TRANSLATIONS:
        if k.startswith("ss_"):
            assert zh, f"{k} 缺 zh"
            # hint 可空(用户输入校验类)— 但 zh 必须自带足够上下文
            if not hint:
                assert ("看" in zh or "必须" in zh or "格式" in zh
                        or "不能" in zh), (
                    f"{k} hint 为空时 zh 必须自带足够上下文")


# ---------------------------------------------------------------------------
# 5. endpoint handler 异常时返 200 + 不抛栈
# ---------------------------------------------------------------------------

def test_endpoint_capture_handler_crash(monkeypatch):
    """ss_capture raise → handler 返 200 + ok=False + warnings。"""
    from prisir_work import screenshot_bridge as _ss

    def boom(*a, **kw):
        raise RuntimeError("crash")
    monkeypatch.setattr(_ss, "ss_capture", boom)
    from prisir_work import endpoints as _ep
    handler = _ep._REGISTRY["/web/screenshot/capture"]["handler"]
    payload, status = handler({})
    assert status == 200
    assert payload["ok"] is False
    assert payload["error"] == "RuntimeError"


# ---------------------------------------------------------------------------
# 6. namespace 不冲突(截图能力 vs agent-browser 能力)
# ---------------------------------------------------------------------------

def test_namespace_no_conflict_with_agent_browser():
    """web.screenshot.* 跟 web.agent-browser.* 不撞 id。"""
    from prisir_work import capability as _cap
    caps = {c["id"] for c in _cap.list_capabilities()}
    ss_caps = {c for c in caps if c.startswith("web.screenshot.")}
    ab_caps = {c for c in caps if c.startswith("web.agent-browser.")}
    assert len(ss_caps) == 6
    assert len(ab_caps) == 8
    assert ss_caps.isdisjoint(ab_caps)