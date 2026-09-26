# -*- coding: utf-8 -*-
"""tests/test_gh_endpoints.py — P3j T21-C 端点 + gh_api_provider 测试。

4 case:
  · gh_api_provider repo URL 成功
  · gh_api_provider issue URL 成功
  · endpoints._web_gh_health 通过 mock gh_bridge.gh_health
  · endpoints._web_gh_search mock gh_bridge.search
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 1. gh_api_provider repo URL
# ---------------------------------------------------------------------------

def test_gh_api_provider_repo_ok(monkeypatch):
    """https://github.com/anthropics/anthropic-sdk-python → repo_info。"""
    from prisir_work import gh_api_provider as _ghp

    def fake_repo(owner, name, *, timeout=30.0):
        return {"ok": True, "owner": owner, "name": name,
                "repo": {"name": name, "description": "Test",
                         "stargazers_count": 3922, "forks_count": 800,
                         "language": "Python", "html_url": f"https://github.com/{owner}/{name}",
                         "updated_at": "2026-09-26"}}
    monkeypatch.setattr("prisir_work.gh_bridge.repo_info", fake_repo)

    r = _ghp.gh_api_provider("https://github.com/anthropics/anthropic-sdk-python")
    assert r["meta"]["ok"] is True
    assert r["meta"]["fetcher"] == "gh_api"
    assert "anthropic-sdk-python" in r["content"]
    assert "Stars" in r["content"]
    assert "3922" in r["content"]


# ---------------------------------------------------------------------------
# 2. gh_api_provider issue URL
# ---------------------------------------------------------------------------

def test_gh_api_provider_issue_ok(monkeypatch):
    """https://github.com/x/y/issues/1 → issue_get。"""
    from prisir_work import gh_api_provider as _ghp
    import prisir_work.gh_api_provider as _ghp_mod

    def fake_issue(owner, name, number, *, timeout=30.0):
        return {"ok": True, "owner": owner, "name": name, "number": number,
                "issue": {"title": "Test issue", "body": "Issue body",
                          "state": "open", "user": {"login": "tester"},
                          "comments": 3, "html_url": f"https://github.com/{owner}/{name}/issues/{number}",
                          "is_pull_request": None}}
    # gh_api_provider 内部 `from prisir_work import gh_bridge as _gh`
    # → monkeypatch gh_bridge.issue_get(模块级属性),gh_api_provider 走的是同一模块
    monkeypatch.setattr("prisir_work.gh_bridge.issue_get", fake_issue)

    r = _ghp.gh_api_provider("https://github.com/x/y/issues/1")
    assert r["meta"]["ok"] is True
    assert "Test issue" in r["content"]
    # content 用 markdown 加粗语法 **Kind**: Issue
    assert "**Kind**: Issue" in r["content"]
    assert "Issue body" in r["content"]
    assert r["meta"]["is_pr"] is False


# ---------------------------------------------------------------------------
# 3. gh_api_provider 非 github.com URL
# ---------------------------------------------------------------------------

def test_gh_api_provider_non_github_url():
    """非 github.com 域名 → error=non_github_url。"""
    from prisir_work import gh_api_provider as _ghp
    r = _ghp.gh_api_provider("https://gitlab.com/x/y")
    assert r["meta"]["ok"] is False
    assert r["meta"]["error"] == "non_github_url"


# ---------------------------------------------------------------------------
# 4. gh_api_provider unsupported URL
# ---------------------------------------------------------------------------

def test_gh_api_provider_unsupported_path():
    """github.com 但不是支持路径(3 段非 issues/pull)→ error=unsupported_github_url。

    注:`/owner/repo` 形式 2 段被 provider 当作仓库路径走 gh api(不会返 unsupported),
    因为 GitHub 仓库 URL 的标准形式就是 /owner/repo。
    想触发 unsupported_github_url,需要 3 段以上但不是 issues/pull/pull/N。
    """
    from prisir_work import gh_api_provider as _ghp
    # 3 段但中间不是 issues/pull / pull(N)
    r = _ghp.gh_api_provider("https://github.com/settings/profile/edit")
    assert r["meta"]["ok"] is False
    assert r["meta"]["error"] == "unsupported_github_url"
    supported = r["meta"]["supported"]
    if isinstance(supported, list):
        assert "/owner/repo" in supported
    else:
        assert "/owner/repo" in str(supported)


# ---------------------------------------------------------------------------
# 5. endpoint /web/gh/health
# ---------------------------------------------------------------------------

def test_web_gh_health_endpoint(monkeypatch):
    """endpoint /web/gh/health 通过 mock gh_bridge.gh_health。"""
    monkeypatch.setattr("prisir_work.gh_bridge.gh_health",
                        lambda: {"ok": True, "installed": True,
                                 "version": "gh version 2.96.0",
                                 "bin": "C:/Program Files/GitHub CLI/gh.exe",
                                 "auth_status": "logged_in"})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_gh_health({})
    assert status == 200
    assert body["ok"] is True
    assert body["installed"] is True
    assert body["auth_status"] == "logged_in"


# ---------------------------------------------------------------------------
# 6. endpoint /web/gh/repo
# ---------------------------------------------------------------------------

def test_web_gh_repo_endpoint(monkeypatch):
    """endpoint /web/gh/repo 调 gh_bridge.repo_info。"""
    monkeypatch.setattr("prisir_work.gh_bridge.repo_info",
                        lambda o, n, *, timeout=30.0: {"ok": True, "owner": o, "name": n,
                                                      "repo": {"stargazers_count": 100}})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_gh_repo({"owner": "x", "name": "y"})
    assert status == 200
    assert body["ok"] is True
    assert body["owner"] == "x"


# ---------------------------------------------------------------------------
# 7. endpoint /web/gh/repo 缺字段
# ---------------------------------------------------------------------------

def test_web_gh_repo_missing_fields():
    """缺 owner / name → error=missing_owner_or_name。"""
    from prisir_work import endpoints as _ep
    body, status = _ep._web_gh_repo({"owner": "", "name": ""})
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "missing_owner_or_name"


# ---------------------------------------------------------------------------
# 8. endpoint /web/gh/issue
# ---------------------------------------------------------------------------

def test_web_gh_issue_endpoint(monkeypatch):
    """endpoint /web/gh/issue 调 gh_bridge.issue_get。"""
    monkeypatch.setattr("prisir_work.gh_bridge.issue_get",
                        lambda o, n, num, *, timeout=30.0:
                        {"ok": True, "owner": o, "name": n, "number": num,
                         "issue": {"title": "Test", "body": "B", "state": "open",
                                   "user": {"login": "u"}, "comments": 1,
                                   "html_url": f"https://github.com/{o}/{n}/issues/{num}",
                                   "is_pull_request": None}})
    from prisir_work import endpoints as _ep
    body, status = _ep._web_gh_issue({"owner": "x", "name": "y", "number": 42})
    assert status == 200
    assert body["ok"] is True
    assert body["issue"]["title"] == "Test"
    assert body["number"] == 42


# ---------------------------------------------------------------------------
# 9. endpoint /web/gh/search
# ---------------------------------------------------------------------------

def test_web_gh_search_endpoint(monkeypatch):
    """endpoint /web/gh/search 调 gh_bridge.search。"""
    monkeypatch.setattr("prisir_work.gh_bridge.search",
                        lambda q, kind="repos", limit=10, *, timeout=30.0:
                        [{"url": "https://github.com/a/b", "title": "b",
                          "snippet": "desc"}])
    from prisir_work import endpoints as _ep
    body, status = _ep._web_gh_search({"query": "x", "kind": "repos", "limit": 5})
    assert status == 200
    assert body["ok"] is True
    assert body["kind"] == "repos"
    assert body["results"][0]["title"] == "b"
    assert body["sources"] == ["gh_search"]


# ---------------------------------------------------------------------------
# 10. endpoint /web/gh/search empty query
# ---------------------------------------------------------------------------

def test_web_gh_search_empty():
    """空 query → error=empty_query。"""
    from prisir_work import endpoints as _ep
    body, status = _ep._web_gh_search({"query": ""})
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "empty_query"


# ---------------------------------------------------------------------------
# 11. capability 注册 4 个 web.gh.*
# ---------------------------------------------------------------------------

def test_capability_registered():
    """web.gh.{health,repo,issue,search} 4 个 capability 已注册。"""
    from prisir_work import capability as _cap
    all_caps = _cap.list_capabilities()
    cap_ids = {c["id"] for c in all_caps}
    assert "web.gh.health" in cap_ids
    assert "web.gh.repo" in cap_ids
    assert "web.gh.issue" in cap_ids
    assert "web.gh.search" in cap_ids


# ---------------------------------------------------------------------------
# 12. web_fetch gh_api fetcher 已注册
# ---------------------------------------------------------------------------

def test_fetcher_registered():
    """web_fetch 的 _FETCHERS 含 gh_api(触发 fetch() 让 lazy register 执行)。"""
    from prisir_work import web_fetch as _wf
    # 触发 lazy register:web_fetch 在 _FETCHERS 为空时自动注册默认 5 个 fetcher
    # 用一个 invalid URL 触发一次 fetch(),内部会先 register 再 fail
    try:
        _wf.fetch("about:blank", options={"no_cache": True, "timeout": 0.1})
    except Exception:
        pass
    fetcher_names = list(_wf._FETCHERS.keys())
    assert "gh_api" in fetcher_names, \
        f"gh_api 不在 _FETCHERS,当前: {fetcher_names}"
    # 同样确认 jina / feedparser / ytdlp_meta 也注册了
    for expected in ("jina", "feedparser", "ytdlp_meta", "gh_api",
                     "http_urllib", "a11y", "browser_use_cli"):
        assert expected in fetcher_names, \
            f"{expected} missing from _FETCHERS,当前: {fetcher_names}"


# ---------------------------------------------------------------------------
# 13. web_search gh_search provider 已注册
# ---------------------------------------------------------------------------

def test_search_provider_registered():
    """web_search 的 _PROVIDERS 含 gh_search(若 gh CLI 已装)。"""
    import shutil as _shutil
    if not _shutil.which("gh"):
        return  # 没装 gh,跳过
    from prisir_work import web_search as _ws
    provider_names = list(_ws._PROVIDERS.keys())
    assert "gh_search" in provider_names, \
        f"gh_search 不在 _PROVIDERS,当前: {provider_names}"


# ---------------------------------------------------------------------------
# 直接跑
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))