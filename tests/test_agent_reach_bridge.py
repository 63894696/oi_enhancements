# -*- coding: utf-8 -*-
"""tests/test_agent_reach_bridge.py — P3j T20-A 子进程桥 mock 测试(适配真实 CLI)。

agent-reach 实际 CLI(0.1.0):agent-reach get <channel> <query> --json
                              agent-reach doctor --json(返 list)
                              agent-reach list / install <channel>

我们 mock subprocess.run + importlib.metadata.version 模拟:
  · installed=False (FileNotFoundError / shutil.which None)
  · installed=True + doctor 返 JSON list
  · subprocess 返 non-zero (returncode 1) → 推断具体 error_kind
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


def _patch_version(monkeypatch, version: str = "0.1.0"):
    """mock importlib.metadata.version('agent-reach') return value."""
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "version",
                        lambda name: version if name == "agent-reach"
                        else (_ for _ in ()).throw(
                            importlib.metadata.PackageNotFoundError(name)))


# ---------------------------------------------------------------------------
# 1. doctor — agent-reach 未装
# ---------------------------------------------------------------------------

def test_doctor_not_installed(monkeypatch):
    # shutil.which returns None → bin 找不到
    monkeypatch.setattr("shutil.which", lambda x: None)
    from prisir_work import agent_reach_bridge as arb
    r = arb.doctor()
    assert r["ok"] is True
    assert r["installed"] is False
    assert "未安装" in r["hint"] or "pip install" in r["hint"]
    assert r["channels"] == []


# ---------------------------------------------------------------------------
# 2. doctor — installed + JSON 解析(list 形态,0.1.0 实际行为)
# ---------------------------------------------------------------------------

def test_doctor_installed(monkeypatch):
    # agent-reach 0.1.0 实际 doctor --json 返 list
    payload = json.dumps([
        {"channel": "rss",     "ok": True,  "auth": "none",
         "detail": "feedparser", "installed": True},
        {"channel": "youtube", "ok": True,  "auth": "none",
         "detail": "yt-dlp",     "installed": True},
    ])
    monkeypatch.setattr("shutil.which", lambda x: "/fake/agent-reach")
    _patch_version(monkeypatch, "0.1.0")
    def _ok(*a, **kw):
        return _FakeProc(returncode=0, stdout=payload, stderr="")
    _patch_subprocess(monkeypatch, side_effect=_ok)

    from prisir_work import agent_reach_bridge as arb
    r = arb.doctor()
    assert r["installed"] is True
    assert r["version"] == "0.1.0"
    assert len(r["channels"]) == 2
    assert r["channels"][0]["id"] == "rss"
    assert r["channels"][0]["status"] == "ok"


# ---------------------------------------------------------------------------
# 3. doctor — 子进程返非 0
# ---------------------------------------------------------------------------

def test_doctor_subprocess_failed(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: "/fake/agent-reach")
    _patch_version(monkeypatch, "0.1.0")
    def _fail(*a, **kw):
        return _FakeProc(returncode=1, stdout="", stderr="boom")
    _patch_subprocess(monkeypatch, side_effect=_fail)

    from prisir_work import agent_reach_bridge as arb
    r = arb.doctor()
    assert r["installed"] is True
    assert r["channels"] == []
    assert r.get("error") == "agent_reach_failed"


# ---------------------------------------------------------------------------
# 4. read — 成功(新 CLI:get <channel> <url> --json,返 {items: [...]})
# ---------------------------------------------------------------------------

def test_read_ok(monkeypatch):
    payload = json.dumps({
        "channel": "rss",
        "command": "feed",
        "query": "https://hnrss.org/frontpage",
        "fetched_at": "2026-09-26T00:00:00+00:00",
        "items": [
            {"title": "测试标题", "url": "https://test.com/1",
             "author": "alice", "published_at": "2026-09-26",
             "text": "hello world", "engagement": {}},
        ],
    })
    monkeypatch.setattr("shutil.which", lambda x: "/fake/agent-reach")
    def _ok(*a, **kw):
        return _FakeProc(returncode=0, stdout=payload, stderr="")
    _patch_subprocess(monkeypatch, side_effect=_ok)

    from prisir_work import agent_reach_bridge as arb
    r = arb.read("rss", "https://hnrss.org/frontpage")
    assert r["ok"] is True
    assert "hello world" in r["content"]
    assert r["title"] == "测试标题"
    assert r["meta"]["item_count"] == 1
    assert r["meta"]["channel"] == "rss"


# ---------------------------------------------------------------------------
# 5. read — 超时
# ---------------------------------------------------------------------------

def test_read_timeout(monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="agent-reach", timeout=30.0)
    _patch_subprocess(monkeypatch, side_effect=_raise)

    from prisir_work import agent_reach_bridge as arb
    r = arb.read("rss", "https://hnrss.org/frontpage")
    assert r["ok"] is False
    assert r["error"] == "agent_reach_timeout"


# ---------------------------------------------------------------------------
# 6. read — channel 未装(stderr 推断)
# ---------------------------------------------------------------------------

def test_read_channel_not_installed(monkeypatch):
    def _fail(*a, **kw):
        return _FakeProc(returncode=1, stdout="",
                          stderr="agent-reach: no channel named 'xhs' in the index")
    _patch_subprocess(monkeypatch, side_effect=_fail)

    from prisir_work import agent_reach_bridge as arb
    r = arb.read("xhs", "https://test")
    assert r["ok"] is False
    assert r["error"] == "reach_channel_not_installed"
    assert "channel 未安装" in r["hint"]
    assert "agent-reach install xhs" in r["hint"]


# ---------------------------------------------------------------------------
# 7. search — 成功(新 CLI:get <channel> <query> --limit N --json)
# ---------------------------------------------------------------------------

def test_search_results(monkeypatch):
    payload = json.dumps({
        "channel": "rss",
        "items": [
            {"url": f"https://x.com/n/{i}", "title": f"n{i}",
             "text": f"snippet {i}", "author": "u", "published_at": ""}
            for i in range(2)
        ],
    })
    def _ok(*a, **kw):
        return _FakeProc(returncode=0, stdout=payload, stderr="")
    _patch_subprocess(monkeypatch, side_effect=_ok)

    from prisir_work import agent_reach_bridge as arb
    r = arb.search("rss", "https://test.com/feed", limit=2)
    assert r["ok"] is True
    assert len(r["results"]) == 2
    assert r["sources"] == ["rss", "rss"]


def test_search_results_capped(monkeypatch):
    payload = json.dumps({
        "channel": "rss",
        "items": [
            {"url": f"https://x.com/n/{i}", "title": f"n{i}",
             "text": "", "author": "", "published_at": ""}
            for i in range(20)
        ],
    })
    def _ok(*a, **kw):
        return _FakeProc(returncode=0, stdout=payload, stderr="")
    _patch_subprocess(monkeypatch, side_effect=_ok)

    from prisir_work import agent_reach_bridge as arb
    r = arb.search("rss", "https://test.com/feed", limit=3)
    assert r["ok"] is True
    assert len(r["results"]) == 3, f"应截断到 limit=3,实际 {len(r['results'])}"


# ---------------------------------------------------------------------------
# 8. search — 缺 query(rss 专属)
# ---------------------------------------------------------------------------

def test_search_rss_missing_query(monkeypatch):
    def _fail(*a, **kw):
        return _FakeProc(returncode=1, stdout="",
                          stderr="agent-reach: rss: rss needs a feed URL")
    _patch_subprocess(monkeypatch, side_effect=_fail)

    from prisir_work import agent_reach_bridge as arb
    r = arb.search("rss", "")
    assert r["ok"] is False
    assert r["error"] == "reach_missing_query"


# ---------------------------------------------------------------------------
# 9. platforms — 静态 14 + P0 6
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
    for p in plats:
        if p["p0"]:
            assert p["default_on"] is True
        else:
            assert p["default_on"] is False


# ---------------------------------------------------------------------------
# 10. read — 缺参数
# ---------------------------------------------------------------------------

def test_read_missing_params():
    from prisir_work import agent_reach_bridge as arb
    r = arb.read("", "url")
    assert r["ok"] is False
    assert r["error"] == "missing_params"

    r2 = arb.read("rss", "")
    assert r2["ok"] is False
    assert r2["error"] == "missing_params"


# ---------------------------------------------------------------------------
# 11. _detect_reach_intent
# ---------------------------------------------------------------------------

def test_detect_reach_intent():
    from prisir_work.research import _detect_reach_intent
    assert _detect_reach_intent("小红书怎么评价 PrisirAI") == ("xhs", "怎么评价 prisirai")
    assert _detect_reach_intent("B站 字幕") == ("bilibili-search", "字幕")
    assert _detect_reach_intent("V2EX 最近 AI agent 讨论") == ("v2ex", "最近 ai agent 讨论")
    assert _detect_reach_intent("完全无关的查询") is None
    assert _detect_reach_intent("") is None
    plat, sub = _detect_reach_intent("给我这个视频字幕")
    assert plat in ("bilibili-subtitle", "youtube-subtitle"), \
        f"字幕应映射到字幕平台,实际 {plat}"


# ---------------------------------------------------------------------------
# 允许 python tests/test_agent_reach_bridge.py 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))