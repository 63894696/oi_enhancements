# -*- coding: utf-8 -*-
"""tests/test_agent_reach_bridge.py — P3j T20-A 子进程桥 mock 测试。

agent-reach 是外部 CLI,我们用 monkeypatch subprocess.run 模拟:
  · installed=False (FileNotFoundError)
  · installed=True + doctor 返 JSON
  · subprocess 返 non-zero (returncode 1)
  · subprocess.TimeoutExpired
  · read / search 成功 + 失败
  · platforms 静态目录
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, *, returncode: int = 0, stdout: str = "",
                 stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_subprocess(monkeypatch, *, side_effect):
    """monkeypatch subprocess.run with given side_effect function."""
    monkeypatch.setattr("subprocess.run", side_effect)


# ---------------------------------------------------------------------------
# 1. doctor — agent-reach 未装
# ---------------------------------------------------------------------------

def test_doctor_not_installed(monkeypatch):
    def _raise(*a, **kw):
        raise FileNotFoundError("agent-reach not found")
    _patch_subprocess(monkeypatch, side_effect=_raise)

    from prisir_work import agent_reach_bridge as arb
    r = arb.doctor()
    assert r["ok"] is True
    assert r["installed"] is False
    assert "未安装" in r["hint"] or "pip install" in r["hint"]
    assert r["platforms"] == []


# ---------------------------------------------------------------------------
# 2. doctor — installed + JSON 解析
# ---------------------------------------------------------------------------

def test_doctor_installed(monkeypatch):
    payload = json.dumps({
        "version": "0.1.0",
        "platforms": [
            {"id": "xhs", "status": "ok", "hint": ""},
            {"id": "github", "status": "warn", "hint": "需 playwright"},
        ],
    })
    monkeypatch.setattr("shutil.which", lambda x: "/fake/agent-reach")
    def _ok(*a, **kw):
        return _FakeProc(returncode=0, stdout=payload, stderr="")
    _patch_subprocess(monkeypatch, side_effect=_ok)

    from prisir_work import agent_reach_bridge as arb
    r = arb.doctor()
    assert r["installed"] is True
    assert r["version"] == "0.1.0"
    assert len(r["platforms"]) == 2


# ---------------------------------------------------------------------------
# 3. doctor — 子进程返非 0
# ---------------------------------------------------------------------------

def test_doctor_subprocess_failed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: "/fake/agent-reach")
    def _fail(*a, **kw):
        return _FakeProc(returncode=1, stdout="", stderr="boom")
    _patch_subprocess(monkeypatch, side_effect=_fail)

    from prisir_work import agent_reach_bridge as arb
    r = arb.doctor()
    assert r["installed"] is True  # bin 找到了
    assert r["platforms"] == []
    assert r.get("error") == "agent_reach_failed"


# ---------------------------------------------------------------------------
# 4. read — 成功
# ---------------------------------------------------------------------------

def test_read_ok(monkeypatch):
    payload = json.dumps({"content": "hello", "title": "测试",
                          "meta": {"lang": "zh"}})
    def _ok(*a, **kw):
        return _FakeProc(returncode=0, stdout=payload, stderr="")
    _patch_subprocess(monkeypatch, side_effect=_ok)

    from prisir_work import agent_reach_bridge as arb
    r = arb.read("bilibili-subtitle", "https://www.bilibili.com/video/BV1")
    assert r["ok"] is True
    assert r["content"] == "hello"
    assert r["title"] == "测试"


# ---------------------------------------------------------------------------
# 5. read — 超时
# ---------------------------------------------------------------------------

def test_read_timeout(monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="agent-reach", timeout=30.0)
    _patch_subprocess(monkeypatch, side_effect=_raise)

    from prisir_work import agent_reach_bridge as arb
    r = arb.read("xhs", "https://www.xiaohongshu.com/explore")
    assert r["ok"] is False
    assert r["error"] == "agent_reach_timeout"


# ---------------------------------------------------------------------------
# 6. search — 成功 + 结果截断
# ---------------------------------------------------------------------------

def test_search_results(monkeypatch):
    payload = json.dumps({"results": [
        {"url": "https://xhs.com/note/1", "title": "笔记1",
         "snippet": "..."},
        {"url": "https://xhs.com/note/2", "title": "笔记2",
         "snippet": "..."},
    ]})
    def _ok(*a, **kw):
        return _FakeProc(returncode=0, stdout=payload, stderr="")
    _patch_subprocess(monkeypatch, side_effect=_ok)

    from prisir_work import agent_reach_bridge as arb
    r = arb.search("xhs", "PrisirAI", limit=2)
    assert r["ok"] is True
    assert len(r["results"]) == 2
    assert r["sources"] == ["xhs", "xhs"]


def test_search_results_capped(monkeypatch):
    payload = json.dumps({"results": [
        {"url": f"https://xhs.com/n/{i}", "title": f"n{i}",
         "snippet": ""}
        for i in range(20)
    ]})
    def _ok(*a, **kw):
        return _FakeProc(returncode=0, stdout=payload, stderr="")
    _patch_subprocess(monkeypatch, side_effect=_ok)

    from prisir_work import agent_reach_bridge as arb
    r = arb.search("xhs", "q", limit=3)
    assert r["ok"] is True
    assert len(r["results"]) == 3, f"应截断到 limit=3,实际 {len(r['results'])}"


# ---------------------------------------------------------------------------
# 7. platforms — 静态 14 + P0 6
# ---------------------------------------------------------------------------

def test_platforms_static():
    from prisir_work import agent_reach_bridge as arb
    plats = arb.platforms()
    assert len(plats) == 14, f"应有 14 平台,实际 {len(plats)}"
    p0 = [p for p in plats if p["p0"]]
    assert len(p0) == 6, f"P0 应有 6 平台,实际 {len(p0)}"
    p0_ids = {p["id"] for p in p0}
    assert p0_ids == {"xhs", "bilibili-subtitle", "github", "v2ex",
                      "youtube-subtitle", "rss"}, \
        f"P0 平台 ID 不匹配:{p0_ids}"
    # default_on 应等于 p0
    for p in plats:
        if p["p0"]:
            assert p["default_on"] is True
        else:
            assert p["default_on"] is False


# ---------------------------------------------------------------------------
# 8. read — 缺参数
# ---------------------------------------------------------------------------

def test_read_missing_params():
    from prisir_work import agent_reach_bridge as arb
    r = arb.read("", "url")
    assert r["ok"] is False
    assert r["error"] == "missing_params"

    r2 = arb.read("xhs", "")
    assert r2["ok"] is False
    assert r2["error"] == "missing_params"


# ---------------------------------------------------------------------------
# 9. _detect_reach_intent
# ---------------------------------------------------------------------------

def test_detect_reach_intent():
    from prisir_work.research import _detect_reach_intent
    assert _detect_reach_intent("小红书怎么评价 PrisirAI") == ("xhs", "怎么评价 prisirai")
    assert _detect_reach_intent("B站 字幕") == ("bilibili-search", "字幕")
    assert _detect_reach_intent("V2EX 最近 AI agent 讨论") == ("v2ex", "最近 ai agent 讨论")
    assert _detect_reach_intent("完全无关的查询") is None
    assert _detect_reach_intent("") is None
    # 字幕 关键字(无 b站)
    plat, sub = _detect_reach_intent("给我这个视频字幕")
    assert plat in ("bilibili-subtitle", "youtube-subtitle"), \
        f"字幕应映射到字幕平台,实际 {plat}"


# ---------------------------------------------------------------------------
# 允许 python tests/test_agent_reach_bridge.py 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))