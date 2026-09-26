# -*- coding: utf-8 -*-
"""tests/test_screenshot_bridge.py — P3j T28 screenshot-mcp 桥接测试。

~20 个 mock case 覆盖:
  · ss_health 4 路(missing_cli / missing_node / no_backend / ready)
  · ss_capture 4 路(成功 / bad_mode / ss_invalid_area / cli_not_found)
  · ss_list 2 路(默认目录 + 自定义 dir)
  · ss_read 2 路(PNG 尺寸 / 文件不存在)
  · ss_active_backend 1 路(纯本地)
  · ss_install_hint 1 路(三平台分支)
  · _parse_saved_path 1 路
  · area 格式校验 1 路(负数 / 宽高 <= 0)
  · filename 路径穿越 1 路
  · 后端探测 1 路(win powershell)

所有 subprocess.run 都 mock,不需要真 screenshot-mcp。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _patch_run(monkeypatch, side_effect):
    """monkeypatch screenshot_bridge._run 到给定 side_effect。"""
    if isinstance(side_effect, list):
        iter_func = iter(side_effect)
        def fn(*args, **kw):
            try:
                return next(iter_func)
            except StopIteration:
                return {"ok": False, "error": "no_more_responses"}
        monkeypatch.setattr("prisir_work.screenshot_bridge._run", fn)
    else:
        monkeypatch.setattr("prisir_work.screenshot_bridge._run", side_effect)


# ---------------------------------------------------------------------------
# 1. ss_health — missing_cli
# ---------------------------------------------------------------------------

def test_ss_health_missing_cli(monkeypatch):
    """shutil.which('screenshot-mcp') 返 None → mode=missing_cli。"""
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr("prisir_work.screenshot_bridge.shutil.which",
                        lambda x: None)
    h = _ss.ss_health()
    assert h["ok"] is True
    assert h["installed"] is False
    assert h["mode"] == "missing_cli"
    assert "npm install" in h["hint"]
    assert h["backend"] == ""


# ---------------------------------------------------------------------------
# 2. ss_health — missing_node(Node < 18)
# ---------------------------------------------------------------------------

def test_ss_health_missing_node(monkeypatch):
    """bin 在 + Node 16 → mode=missing_node。"""
    from prisir_work import screenshot_bridge as _ss

    def fake_which(name):
        if name == "screenshot-mcp":
            return "/usr/local/bin/screenshot-mcp"
        if name == "node":
            return "/usr/bin/node"
        return None

    monkeypatch.setattr("prisir_work.screenshot_bridge.shutil.which",
                        fake_which)

    fake_result = type("R", (), {
        "returncode": 0, "stdout": "v16.20.2\n", "stderr": ""
    })()
    monkeypatch.setattr(
        "prisir_work.screenshot_bridge.subprocess.run",
        lambda *a, **kw: fake_result,
    )
    h = _ss.ss_health()
    assert h["mode"] == "missing_node"
    assert "Node.js 版本" in h["hint"]


# ---------------------------------------------------------------------------
# 3. ss_health — no_backend(Win 上 powershell 缺失)
# ---------------------------------------------------------------------------

def test_ss_health_no_backend_win(monkeypatch):
    """bin 在 + Node OK + Win 上无 powershell → mode=no_backend。"""
    from prisir_work import screenshot_bridge as _ss

    def fake_which(name):
        if name == "screenshot-mcp":
            return "/usr/local/bin/screenshot-mcp"
        if name == "node":
            return "/usr/bin/node"
        return None  # powershell 缺失

    monkeypatch.setattr("prisir_work.screenshot_bridge.shutil.which",
                        fake_which)
    monkeypatch.setattr("prisir_work.screenshot_bridge._detect_platform",
                        lambda: "windows")
    fake_result = type("R", (), {
        "returncode": 0, "stdout": "v18.19.0\n", "stderr": ""
    })()
    monkeypatch.setattr(
        "prisir_work.screenshot_bridge.subprocess.run",
        lambda *a, **kw: fake_result,
    )
    h = _ss.ss_health()
    assert h["mode"] == "no_backend"
    assert h["platform"] == "windows"


# ---------------------------------------------------------------------------
# 4. ss_health — ready
# ---------------------------------------------------------------------------

def test_ss_health_ready(monkeypatch):
    """bin 在 + Node OK + Win powershell 都在 → mode=ready。"""
    from prisir_work import screenshot_bridge as _ss

    def fake_which(name):
        return "/some/path/bin" if name in (
            "screenshot-mcp", "node", "powershell") else None

    monkeypatch.setattr("prisir_work.screenshot_bridge.shutil.which",
                        fake_which)
    monkeypatch.setattr("prisir_work.screenshot_bridge._detect_platform",
                        lambda: "windows")

    fake_node = type("R", (), {
        "returncode": 0, "stdout": "v18.19.0\n", "stderr": ""
    })()
    calls = {"n": 0}

    def fake_subprocess_run(*a, **kw):
        calls["n"] += 1
        # 第 1 次:node --version;第 2 次:_run --version(screenshot-mcp)
        if calls["n"] == 1:
            return fake_node
        return type("R", (), {
            "returncode": 0, "stdout": "screenshot-mcp 1.2.3\n", "stderr": ""
        })()

    monkeypatch.setattr(
        "prisir_work.screenshot_bridge.subprocess.run",
        fake_subprocess_run,
    )
    h = _ss.ss_health()
    assert h["mode"] == "ready"
    assert h["installed"] is True
    assert h["backend"] == "powershell"


# ---------------------------------------------------------------------------
# 5. ss_capture — 成功
# ---------------------------------------------------------------------------

def test_ss_capture_ok(monkeypatch):
    """fullscreen + stdout 含 'Saved to:' → 抽 path。"""
    from prisir_work import screenshot_bridge as _ss
    _patch_run(monkeypatch, lambda args, **kw: {
        "ok": True, "stdout": "Capturing...\nSaved to: /tmp/cap.png\n",
        "stderr": "", "returncode": 0,
    })
    r = _ss.ss_capture("fullscreen")
    assert r["ok"] is True
    assert r["path"] == "/tmp/cap.png"
    assert r["mode"] == "fullscreen"


# ---------------------------------------------------------------------------
# 6. ss_capture — bad_mode
# ---------------------------------------------------------------------------

def test_ss_capture_bad_mode(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    r = _ss.ss_capture("weird-mode")
    assert r["ok"] is False
    assert r["error"] == "ss_bad_mode"
    assert r["got"] == "weird-mode"


# ---------------------------------------------------------------------------
# 7. ss_capture — area 缺失
# ---------------------------------------------------------------------------

def test_ss_capture_area_missing(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    r = _ss.ss_capture("area", area="")
    assert r["ok"] is False
    assert r["error"] == "ss_invalid_area"


# ---------------------------------------------------------------------------
# 8. ss_capture — area 格式错(负数)
# ---------------------------------------------------------------------------

def test_ss_capture_area_negative(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    r = _ss.ss_capture("area", area="-1,2,3,4")
    assert r["ok"] is False
    assert r["error"] == "ss_invalid_area"


# ---------------------------------------------------------------------------
# 9. ss_capture — area 宽高 0
# ---------------------------------------------------------------------------

def test_ss_capture_area_zero_dim(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    r = _ss.ss_capture("area", area="0,0,0,100")
    assert r["ok"] is False
    assert r["error"] == "ss_invalid_area"


# ---------------------------------------------------------------------------
# 10. ss_capture — filename 路径穿越
# ---------------------------------------------------------------------------

def test_ss_capture_filename_traversal(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    r = _ss.ss_capture("fullscreen", filename="../etc/passwd")
    assert r["ok"] is False
    assert r["error"] == "ss_bad_filename"


# ---------------------------------------------------------------------------
# 11. ss_capture — cli_not_found
# ---------------------------------------------------------------------------

def test_ss_capture_cli_not_found(monkeypatch):
    """_run 返 ss_cli_not_found → ss_capture 也透传。"""
    from prisir_work import screenshot_bridge as _ss
    _patch_run(monkeypatch, lambda args, **kw: {
        "ok": False, "installed": False,
        "error": "ss_cli_not_found", "hint": "...",
    })
    r = _ss.ss_capture("fullscreen")
    assert r["ok"] is False
    assert r["error"] == "ss_cli_not_found"


# ---------------------------------------------------------------------------
# 12. ss_capture — area 走 subprocess 拿参数
# ---------------------------------------------------------------------------

def test_ss_capture_with_area(monkeypatch):
    """area 校验通过 + 成功 → _run 收到 --area 100,200,300,400。"""
    from prisir_work import screenshot_bridge as _ss
    captured = {}

    def fake_run(args, **kw):
        captured["args"] = args
        return {"ok": True, "stdout": "Saved to: /tmp/x.png\n",
                "stderr": "", "returncode": 0}

    _patch_run(monkeypatch, fake_run)
    r = _ss.ss_capture("area", area="100,200,300,400",
                       filename="cap.png")
    assert r["ok"] is True
    assert "capture" in captured["args"]
    assert "--mode" in captured["args"]
    assert "area" in captured["args"]
    assert "--area" in captured["args"]
    assert "100,200,300,400" in captured["args"]
    assert "--filename" in captured["args"]


# ---------------------------------------------------------------------------
# 13. ss_list — 目录不存在
# ---------------------------------------------------------------------------

def test_ss_list_dir_not_exist(monkeypatch, tmp_path):
    from prisir_work import screenshot_bridge as _ss
    r = _ss.ss_list(output_dir=str(tmp_path / "nope"))
    assert r["ok"] is True
    assert r["entries"] == []
    assert "dir_not_exist" in r["warnings"]


# ---------------------------------------------------------------------------
# 14. ss_list — 列已有 PNG(按 mtime desc)
# ---------------------------------------------------------------------------

def test_ss_list_with_files(monkeypatch, tmp_path):
    from prisir_work import screenshot_bridge as _ss
    (tmp_path / "old.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
    (tmp_path / "new.png").write_bytes(b"\x89PNG" + b"\x00" * 200)
    import time
    time.sleep(0.05)
    (tmp_path / "new.png").write_bytes(b"\x89PNG" + b"\x00" * 200)
    r = _ss.ss_list(output_dir=str(tmp_path), limit=10)
    assert r["ok"] is True
    assert r["total"] >= 2
    # new.png 应该在前面
    names = [e["name"] for e in r["entries"]]
    assert names[0] == "new.png"


# ---------------------------------------------------------------------------
# 15. ss_read — 空路径
# ---------------------------------------------------------------------------

def test_ss_read_empty_path():
    from prisir_work import screenshot_bridge as _ss
    r = _ss.ss_read("")
    assert r["ok"] is False
    assert r["error"] == "ss_empty_path"


# ---------------------------------------------------------------------------
# 16. ss_read — 文件不存在
# ---------------------------------------------------------------------------

def test_ss_read_not_found():
    from prisir_work import screenshot_bridge as _ss
    r = _ss.ss_read("/tmp/__no_such_file__abc123__.png")
    assert r["ok"] is False
    assert r["error"] == "ss_not_found"


# ---------------------------------------------------------------------------
# 17. ss_read — PNG 尺寸解析
# ---------------------------------------------------------------------------

def test_ss_read_png_dimensions(tmp_path):
    """手工写 PNG 头(width=320, height=240)→ ss_read 抽尺寸。"""
    from prisir_work import screenshot_bridge as _ss
    import struct as _s
    fp = tmp_path / "test.png"
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = b"IHDR" + _s.pack(">IIBBBBB", 320, 240, 8, 2, 0, 0, 0)
    fp.write_bytes(sig + _s.pack(">I", 13) + ihdr + b"\x00" * 16)
    r = _ss.ss_read(str(fp))
    assert r["ok"] is True
    assert r["width"] == 320
    assert r["height"] == 240
    assert r["exists"] is True


# ---------------------------------------------------------------------------
# 18. ss_active_backend — Win
# ---------------------------------------------------------------------------

def test_ss_active_backend_windows(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr("prisir_work.screenshot_bridge._detect_platform",
                        lambda: "windows")
    monkeypatch.setattr("prisir_work.screenshot_bridge.shutil.which",
                        lambda x: "/x" if x == "powershell" else None)
    r = _ss.ss_active_backend()
    assert r["ok"] is True
    assert r["platform"] == "windows"
    assert r["backend"] == "powershell"
    assert r["available"] is True


# ---------------------------------------------------------------------------
# 19. ss_install_hint — Linux 已装
# ---------------------------------------------------------------------------

def test_ss_install_hint_linux_ready(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr("prisir_work.screenshot_bridge._detect_platform",
                        lambda: "linux")
    monkeypatch.setattr("prisir_work.screenshot_bridge.shutil.which",
                        lambda x: "/usr/bin/grim" if x == "grim" else None)
    r = _ss.ss_install_hint()
    assert r["ok"] is True
    assert r["backend"] == "grim"
    assert "grim" in r["hint"]


# ---------------------------------------------------------------------------
# 20. ss_install_hint — Linux 没后端
# ---------------------------------------------------------------------------

def test_ss_install_hint_linux_no_backend(monkeypatch):
    from prisir_work import screenshot_bridge as _ss
    monkeypatch.setattr("prisir_work.screenshot_bridge._detect_platform",
                        lambda: "linux")
    monkeypatch.setattr("prisir_work.screenshot_bridge.shutil.which",
                        lambda x: None)
    r = _ss.ss_install_hint()
    assert r["ok"] is True
    assert r["backend"] == "未检测到"
    assert "grim" in r["hint"]
    assert "scrot" in r["hint"]


# ---------------------------------------------------------------------------
# 21. _parse_saved_path
# ---------------------------------------------------------------------------

def test_parse_saved_path_basic():
    from prisir_work import screenshot_bridge as _ss
    assert _ss._parse_saved_path("Saved to: /tmp/cap.png") == "/tmp/cap.png"
    assert _ss._parse_saved_path("Saved   to:cap.png") == "cap.png"
    assert _ss._parse_saved_path("nothing here") is None