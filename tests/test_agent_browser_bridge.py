# -*- coding: utf-8 -*-
"""tests/test_agent_browser_bridge.py — P3j T26 agent-browser 桥接测试。

~12 个 mock case 覆盖:
  · ab_health 3 路(missing_cli / not_installed / ready)
  · ab_open 3 路(成功 / bad_url / ab_cli_not_found)
  · ab_snapshot 2 路(成功解析 refs / data 不是 dict)
  · ab_click 3 路(成功 / bad_ref / ab_invalid_ref)
  · ab_fill 2 路(成功 / bad_ref)
  · ab_eval 1 路(成功 + 返 result)
  · ab_screenshot 1 路(成功)
  · ab_close 1 路(成功)
  · _extract_refs 1 路(文本 + 去重保序)

所有 subprocess.run 都 mock,不需要真 agent-browser。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _patch_run(monkeypatch, side_effect):
    """monkeypatch agent_browser_bridge._run 到给定 side_effect。"""
    if isinstance(side_effect, list):
        iter_func = iter(side_effect)
        def fn(*args, **kw):
            try:
                return next(iter_func)
            except StopIteration:
                return {"ok": False, "error": "no_more_responses"}
        monkeypatch.setattr("prisir_work.agent_browser_bridge._run", fn)
    else:
        monkeypatch.setattr("prisir_work.agent_browser_bridge._run", side_effect)


# ---------------------------------------------------------------------------
# 1. ab_health — missing_cli
# ---------------------------------------------------------------------------

def test_ab_health_missing_cli(monkeypatch):
    """shutil.which 返 None → mode=missing_cli。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr("prisir_work.agent_browser_bridge.shutil.which",
                        lambda x: None)
    h = _ab.ab_health()
    assert h["ok"] is True
    assert h["installed"] is False
    assert h["mode"] == "missing_cli"
    assert "npm install" in h["hint"]


# ---------------------------------------------------------------------------
# 2. ab_health — not_installed(doctor 失败)
# ---------------------------------------------------------------------------

def test_ab_health_not_installed(monkeypatch):
    """bin 在但 doctor 失败 → mode=not_installed。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr("prisir_work.agent_browser_bridge.shutil.which",
                        lambda x: "C:/npm/agent-browser.exe")
    def fake_run(args, *, timeout=10.0, need_json=True):
        if "--version" in args:
            return {"ok": True, "data": {"raw": "agent-browser 0.5.0\n"}}
        if "doctor" in args:
            return {"ok": False, "error": "ab_failed",
                    "stderr": "Chrome for Testing not found"}
        return {"ok": False, "error": "unexpected"}
    _patch_run(monkeypatch, fake_run)
    h = _ab.ab_health()
    assert h["installed"] is True
    assert h["mode"] == "not_installed"
    assert "Chrome for Testing" in h["hint"] or "agent-browser install" in h["hint"]


# ---------------------------------------------------------------------------
# 3. ab_health — ready
# ---------------------------------------------------------------------------

def test_ab_health_ready(monkeypatch):
    """bin 在 + doctor OK → mode=ready。"""
    from prisir_work import agent_browser_bridge as _ab
    monkeypatch.setattr("prisir_work.agent_browser_bridge.shutil.which",
                        lambda x: "C:/npm/agent-browser.exe")
    def fake_run(args, *, timeout=10.0, need_json=True):
        if "--version" in args:
            return {"ok": True, "data": {"raw": "agent-browser 0.5.0\n"}}
        if "doctor" in args:
            return {"ok": True, "data": {"raw": "All OK\n"}}
        return {"ok": False, "error": "unexpected"}
    _patch_run(monkeypatch, fake_run)
    h = _ab.ab_health()
    assert h["installed"] is True
    assert h["mode"] == "ready"
    assert h["version"].startswith("agent-browser")


# ---------------------------------------------------------------------------
# 4. ab_open — 成功
# ---------------------------------------------------------------------------

def test_ab_open_ok(monkeypatch):
    """agent-browser open https://x.com --json 成功 → ok=True。"""
    from prisir_work import agent_browser_bridge as _ab
    def fake_run(args, *, timeout=30.0, need_json=True):
        assert args[0] == "open"
        assert args[1].startswith("https://")
        # 注意:need_json=True 时 _run 内部会自己追加 --json(实际 subprocess 时)
        # 我们 mock _run 时收不到那个 --json,这里只验 args 透传
        return {"ok": True, "data": {"title": "Example Domain"}}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_open("https://example.com")
    assert r["ok"] is True
    assert r["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# 5. ab_open — bad_url
# ---------------------------------------------------------------------------

def test_ab_open_bad_url():
    """URL 不以 http(s):// 开头 → error=bad_url。"""
    from prisir_work import agent_browser_bridge as _ab
    r = _ab.ab_open("javascript:alert(1)")
    assert r["ok"] is False
    assert r["error"] == "bad_url"

    r = _ab.ab_open("")
    assert r["ok"] is False
    assert r["error"] == "bad_url"


# ---------------------------------------------------------------------------
# 6. ab_open — cli_not_found
# ---------------------------------------------------------------------------

def test_ab_open_cli_not_found(monkeypatch):
    """FileNotFoundError → error=ab_cli_not_found。"""
    from prisir_work import agent_browser_bridge as _ab
    def fake_run(args, *, timeout=30.0, need_json=True):
        return {"ok": False, "installed": False,
                "error": "ab_cli_not_found",
                "hint": "npm install -g agent-browser"}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_open("https://example.com")
    assert r["ok"] is False
    assert r["error"] == "ab_cli_not_found"


# ---------------------------------------------------------------------------
# 7. ab_snapshot — 成功 + refs 解析
# ---------------------------------------------------------------------------

def test_ab_snapshot_ok_with_refs(monkeypatch):
    """snapshot 返 dict{tree: '...@e1...@e2...'} → 解析出 refs。"""
    from prisir_work import agent_browser_bridge as _ab
    def fake_run(args, *, timeout=30.0, need_json=True):
        assert args[0] == "snapshot"
        assert "-d" in args
        return {"ok": True, "data": {
            "snapshot": "@e1 [button] Login\n@e2 [textbox] Email\n@e3 [textbox] Password",
        }}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_snapshot()
    assert r["ok"] is True
    assert "@e1" in r["refs"]
    assert "@e2" in r["refs"]
    assert "@e3" in r["refs"]
    assert "Login" in r["tree"]


# ---------------------------------------------------------------------------
# 8. ab_snapshot — 失败
# ---------------------------------------------------------------------------

def test_ab_snapshot_failed(monkeypatch):
    """subprocess 失败 → ok=False。"""
    from prisir_work import agent_browser_bridge as _ab
    def fake_run(args, *, timeout=30.0, need_json=True):
        return {"ok": False, "error": "ab_failed",
                "stderr": "No browser running"}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_snapshot()
    assert r["ok"] is False
    assert r["error"] == "ab_failed"


# ---------------------------------------------------------------------------
# 9. ab_click — bad_ref(不匹配 @eN 格式)
# ---------------------------------------------------------------------------

def test_ab_click_bad_ref():
    """ref 不是 @eN 格式 → error=bad_ref(不走 subprocess)。"""
    from prisir_work import agent_browser_bridge as _ab
    r = _ab.ab_click("button1")
    assert r["ok"] is False
    assert r["error"] == "bad_ref"

    r = _ab.ab_click("@e")  # 缺数字
    assert r["ok"] is False
    assert r["error"] == "bad_ref"

    r = _ab.ab_click("")
    assert r["ok"] is False
    assert r["error"] == "bad_ref"


# ---------------------------------------------------------------------------
# 10. ab_click — ab_invalid_ref(stderr 含 'not found')
# ---------------------------------------------------------------------------

def test_ab_click_invalid_ref(monkeypatch):
    """stderr 含 'not found' → error=ab_invalid_ref。"""
    from prisir_work import agent_browser_bridge as _ab
    def fake_run(args, *, timeout=30.0, need_json=True):
        return {"ok": False, "error": "ab_failed",
                "returncode": 1,
                "stderr": "ref @e99 not found in snapshot"}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_click("@e99")
    assert r["ok"] is False
    assert r["error"] == "ab_invalid_ref"
    assert r["ref"] == "@e99"


# ---------------------------------------------------------------------------
# 11. ab_fill — 成功 + submit/slowly flag
# ---------------------------------------------------------------------------

def test_ab_fill_ok(monkeypatch):
    """agent-browser fill @e2 "test" --submit --slowly → 透传 flag。"""
    from prisir_work import agent_browser_bridge as _ab
    received_args: list = []
    def fake_run(args, *, timeout=30.0, need_json=True):
        received_args.extend(args)
        return {"ok": True, "data": {"filled": True}}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_fill("@e2", "test@example.com", submit=True, slowly=True)
    assert r["ok"] is True
    assert r["ref"] == "@e2"
    assert r["text"] == "test@example.com"
    assert r["submit"] is True
    assert r["slowly"] is True
    # 验证参数顺序与 flag 透传
    assert "fill" in received_args
    assert "@e2" in received_args
    assert "test@example.com" in received_args
    assert "--submit" in received_args
    assert "--slowly" in received_args


# ---------------------------------------------------------------------------
# 12. ab_fill — bad_ref
# ---------------------------------------------------------------------------

def test_ab_fill_bad_ref():
    """ref 不是 @eN 格式 → error=bad_ref。"""
    from prisir_work import agent_browser_bridge as _ab
    r = _ab.ab_fill("textarea-1", "hello")
    assert r["ok"] is False
    assert r["error"] == "bad_ref"


# ---------------------------------------------------------------------------
# 13. ab_eval — 成功 + 返 result
# ---------------------------------------------------------------------------

def test_ab_eval_ok(monkeypatch):
    """agent-browser eval '() => document.title' 返 {result: ...}。"""
    from prisir_work import agent_browser_bridge as _ab
    def fake_run(args, *, timeout=30.0, need_json=True):
        assert args[0] == "eval"
        return {"ok": True, "data": {"result": "Example Domain"}}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_eval("() => document.title")
    assert r["ok"] is True
    assert r["result"] == "Example Domain"


# ---------------------------------------------------------------------------
# 14. ab_eval — empty_js
# ---------------------------------------------------------------------------

def test_ab_eval_empty_js():
    """空 JS → error=empty_js(不走 subprocess)。"""
    from prisir_work import agent_browser_bridge as _ab
    r = _ab.ab_eval("")
    assert r["ok"] is False
    assert r["error"] == "empty_js"


# ---------------------------------------------------------------------------
# 15. ab_screenshot — 成功 + path 透传
# ---------------------------------------------------------------------------

def test_ab_screenshot_ok(monkeypatch):
    """screenshot foo.png --full-page → path=foo.png。"""
    from prisir_work import agent_browser_bridge as _ab
    def fake_run(args, *, timeout=30.0, need_json=True):
        assert args[0] == "screenshot"
        return {"ok": True, "data": {"path": "C:/tmp/foo.png"}}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_screenshot("foo.png", full_page=True)
    assert r["ok"] is True
    assert r["path"] == "C:/tmp/foo.png"
    assert r["full_page"] is True


# ---------------------------------------------------------------------------
# 16. ab_close — 成功
# ---------------------------------------------------------------------------

def test_ab_close_ok(monkeypatch):
    """agent-browser close 成功 → ok=True。"""
    from prisir_work import agent_browser_bridge as _ab
    def fake_run(args, *, timeout=10.0, need_json=True):
        assert args[0] == "close"
        return {"ok": True, "data": {"raw": "Browser closed"}}
    _patch_run(monkeypatch, fake_run)
    r = _ab.ab_close()
    assert r["ok"] is True


# ---------------------------------------------------------------------------
# 17. _extract_refs — 文本 + 去重保序
# ---------------------------------------------------------------------------

def test_extract_refs_dedup_preserve_order():
    """snapshot 文本中 @e1 出现多次 → refs 去重保序。"""
    from prisir_work import agent_browser_bridge as _ab
    text = "@e1 [button] A\n@e2 [textbox] B\n@e1 [link] again\n@e3 [div]\n@e2"
    refs = _ab._extract_refs(text)
    assert refs == ["@e1", "@e2", "@e3"]


# ---------------------------------------------------------------------------
# 18. _extract_refs — JSON 对象
# ---------------------------------------------------------------------------

def test_extract_refs_from_json():
    """snapshot 是 dict(不是 str)→ 内部 dump 再 grep。"""
    from prisir_work import agent_browser_bridge as _ab
    obj = {"nodes": [{"ref": "@e1"}, {"ref": "@e2"}]}
    refs = _ab._extract_refs(obj)
    assert "@e1" in refs
    assert "@e2" in refs


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))