# -*- coding: utf-8 -*-
"""tests/test_gh_bridge.py — P3j T21-C gh CLI 桥接测试。

7 个 mock case 覆盖:
  · gh_health 2 路(已装 + 未装)
  · repo_info 3 路(成功 + gh_failed + gh_timeout)
  · issue_get 1 路(成功)
  · search 1 路(成功,字段细分)

所有 subprocess 都 mock,不需要真 gh CLI。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _make_completed(stdout: str = "", stderr: str = "",
                    returncode: int = 0):
    """构造 subprocess.CompletedProcess-like 对象。"""
    class _CP:
        pass
    cp = _CP()
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


def _patch_run(monkeypatch, side_effect):
    """monkeypatch gh_bridge._run 到给定 side_effect(可以是函数列表)。"""
    if isinstance(side_effect, list):
        iter_func = iter(side_effect)
        def fn(*args, **kw):
            try:
                return next(iter_func)
            except StopIteration:
                return {"ok": False, "error": "no_more_responses"}
        monkeypatch.setattr("prisir_work.gh_bridge._run", fn)
    else:
        monkeypatch.setattr("prisir_work.gh_bridge._run", side_effect)


# ---------------------------------------------------------------------------
# 1. gh_health — 已装 + auth logged_in
# ---------------------------------------------------------------------------

def test_gh_health_installed(monkeypatch):
    """gh --version + gh auth status 都成功 → installed=True + auth=logged_in。"""
    from prisir_work import gh_bridge as _gh

    monkeypatch.setattr("prisir_work.gh_bridge.shutil.which",
                        lambda x: "C:/Program Files/GitHub CLI/gh.exe")
    def fake_run(args, *, timeout=30.0):
        if "--version" in args:
            return {"ok": True, "data": {"raw": "gh version 2.96.0 (2026-07-02)\n"}}
        if "auth" in args and "status" in args:
            return {"ok": True, "data": {}}  # 简化:auth 成功
        return {"ok": False, "error": "unexpected"}
    _patch_run(monkeypatch, fake_run)

    h = _gh.gh_health()
    assert h["ok"] is True
    assert h["installed"] is True
    assert h["auth_status"] == "logged_in"
    assert "2.96.0" in h["version"]


# ---------------------------------------------------------------------------
# 2. gh_health — 未装
# ---------------------------------------------------------------------------

def test_gh_health_not_installed(monkeypatch):
    """shutil.which 返 None → installed=False。"""
    from prisir_work import gh_bridge as _gh
    monkeypatch.setattr("prisir_work.gh_bridge.shutil.which", lambda x: None)
    h = _gh.gh_health()
    assert h["ok"] is True  # 探活永远 ok(让上层决定怎么处理)
    assert h["installed"] is False
    assert h["version"] == ""
    assert "未安装" in h["hint"]


# ---------------------------------------------------------------------------
# 3. repo_info — 成功
# ---------------------------------------------------------------------------

def test_repo_info_ok(monkeypatch):
    """gh api 返回 JSON → repo_info 解出 stars/forks。"""
    from prisir_work import gh_bridge as _gh
    payload = {
        "name": "anthropic-sdk-python",
        "description": "Test description",
        "stargazers_count": 3922,
        "forks_count": 800,
        "language": "Python",
        "default_branch": "main",
        "html_url": "https://github.com/anthropics/anthropic-sdk-python",
        "updated_at": "2026-09-26T00:00:00Z",
    }
    def fake_run(args, *, timeout=30.0):
        return {"ok": True, "data": payload}
    _patch_run(monkeypatch, fake_run)

    r = _gh.repo_info("anthropics", "anthropic-sdk-python")
    assert r["ok"] is True
    assert r["owner"] == "anthropics"
    assert r["name"] == "anthropic-sdk-python"
    # 注:gh --jq 已经把 stargazers_count 重命名为 stars(在 fake_run payload 里)
    # 因为我们 mock 没经过 --jq 处理,这里 payload 直接对应 repo 字段
    repo = r["repo"]
    assert repo["stargazers_count"] == 3922 or repo.get("stars") == 3922


# ---------------------------------------------------------------------------
# 4. repo_info — gh_failed(returncode 1)
# ---------------------------------------------------------------------------

def test_repo_info_failed(monkeypatch):
    """subprocess 返 returncode 1 → gh_failed。"""
    from prisir_work import gh_bridge as _gh
    def fake_run(args, *, timeout=30.0):
        return {"ok": False, "error": "gh_failed",
                "returncode": 1,
                "stderr": "404 Not Found"}
    _patch_run(monkeypatch, fake_run)
    r = _gh.repo_info("nonexistent", "notfound")
    assert r["ok"] is False
    assert r["error"] == "gh_failed"
    assert "404" in r["stderr"]


# ---------------------------------------------------------------------------
# 5. repo_info — TimeoutExpired
# ---------------------------------------------------------------------------

def test_repo_info_timeout(monkeypatch):
    """subprocess.TimeoutExpired → gh_timeout。"""
    from prisir_work import gh_bridge as _gh
    import subprocess as sp
    def fake_run(args, *, timeout=30.0):
        return {"ok": False, "error": "gh_timeout", "hint": "超时 30s"}
    _patch_run(monkeypatch, fake_run)
    r = _gh.repo_info("anthropics", "anthropic-sdk-python", timeout=10.0)
    assert r["ok"] is False
    assert r["error"] == "gh_timeout"


# ---------------------------------------------------------------------------
# 6. issue_get — 成功
# ---------------------------------------------------------------------------

def test_issue_get_ok(monkeypatch):
    """gh api 返 issue JSON → title/body/labels 解出。"""
    from prisir_work import gh_bridge as _gh
    payload = {
        "title": "Test issue",
        "body": "Issue body content here",
        "state": "open",
        "labels": [{"name": "bug"}, {"name": "P0"}],
        "user": {"login": "testuser"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-09-26T00:00:00Z",
        "comments": 5,
        "html_url": "https://github.com/x/y/issues/1",
        "pull_request": None,
    }
    def fake_run(args, *, timeout=30.0):
        return {"ok": True, "data": payload}
    _patch_run(monkeypatch, fake_run)
    r = _gh.issue_get("x", "y", 1)
    assert r["ok"] is True
    assert r["number"] == 1
    assert r["issue"]["title"] == "Test issue"
    assert r["issue"]["comments"] == 5


# ---------------------------------------------------------------------------
# 7. search — 成功(repos)
# ---------------------------------------------------------------------------

def test_search_repos_ok(monkeypatch):
    """gh search repos 返 3 个仓库 → 转 [{url, title, snippet}]。"""
    from prisir_work import gh_bridge as _gh
    payload = [
        {"name": "anthropic-sdk-python",
         "url": "https://github.com/anthropics/anthropic-sdk-python",
         "description": "Python SDK"},
        {"name": "litellm",
         "url": "https://github.com/BerriAI/litellm",
         "description": "Fastest litest AI Gateway"},
    ]
    def fake_run(args, *, timeout=30.0):
        return {"ok": True, "data": payload}
    _patch_run(monkeypatch, fake_run)
    r = _gh.search("anthropic-sdk-python", kind="repos", limit=10)
    assert len(r) == 2
    assert r[0]["title"] == "anthropic-sdk-python"
    assert "anthropics" in r[0]["url"]
    assert "Python SDK" in r[0]["snippet"]


# ---------------------------------------------------------------------------
# 8. search — issues, repo 字段是 dict
# ---------------------------------------------------------------------------

def test_search_issues_repo_dict(monkeypatch):
    """gh search issues 返 repository 是 dict{nameWithOwner: ...}。"""
    from prisir_work import gh_bridge as _gh
    payload = [
        {"title": "memory leak",
         "url": "https://github.com/microsoft/vscode/issues/200015",
         "number": 200015,
         "repository": {"nameWithOwner": "microsoft/vscode"},
         "state": "open"},
    ]
    def fake_run(args, *, timeout=30.0):
        return {"ok": True, "data": payload}
    _patch_run(monkeypatch, fake_run)
    r = _gh.search("memory leak", kind="issues", limit=10)
    assert len(r) == 1
    assert r[0]["title"] == "memory leak"
    assert "microsoft/vscode" in r[0]["snippet"]
    assert "#200015" in r[0]["snippet"]


# ---------------------------------------------------------------------------
# 9. search — 失败返空 list
# ---------------------------------------------------------------------------

def test_search_failed_returns_empty(monkeypatch):
    """gh search 失败 → 返 [](不抛)。"""
    from prisir_work import gh_bridge as _gh
    def fake_run(args, *, timeout=30.0):
        return {"ok": False, "error": "gh_failed"}
    _patch_run(monkeypatch, fake_run)
    r = _gh.search("xxx")
    assert r == []


# ---------------------------------------------------------------------------
# 10. pr_get — 成功(基本 smoke)
# ---------------------------------------------------------------------------

def test_pr_get_ok(monkeypatch):
    """gh api 返 PR JSON → merged/base/head 解出。"""
    from prisir_work import gh_bridge as _gh
    payload = {
        "title": "Add T21-C",
        "body": "...",
        "state": "closed",
        "user": {"login": "claude"},
        "head": {"ref": "feat/t21-c"},
        "base": {"ref": "master"},
        "merged": True,
        "mergeable": None,
        "created_at": "2026-09-26T00:00:00Z",
        "html_url": "https://github.com/x/y/pull/42",
        "additions": 100,
        "deletions": 50,
        "commits": 3,
        "changed_files": 7,
    }
    def fake_run(args, *, timeout=30.0):
        return {"ok": True, "data": payload}
    _patch_run(monkeypatch, fake_run)
    r = _gh.pr_get("x", "y", 42)
    assert r["ok"] is True
    assert r["pr"]["merged"] is True
    assert r["pr"]["additions"] == 100


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))