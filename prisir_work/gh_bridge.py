# -*- coding: utf-8 -*-
"""gh_bridge.py — GitHub CLI 直接调(2026-09-26 ship, P3j T21-C)。

复现 gh CLI 的核心能力(repo / issue / PR / search),绕 Playwright/cookies,
让 PrisirAI 主对话可以:
  · 读 GitHub 仓库元数据(`gh api repos/owner/name`)
  · 读 GitHub issue / PR(`gh api repos/.../issues/N`)
  · 搜 GitHub 仓库 / issue / PR / 代码(`gh search ...`)

设计:
  · 每个 action 一个 subprocess(`gh --json` 输出)
  · 失败一律返 `{ok: False, error: "gh_xxx"}` 不 raise
  · 默认 timeout 30s;gh 没装也走 fail-soft 路径
  · gh 已装但未登录时 public 端点(repo/issue/search)仍可用,
    private repo 才需 `gh auth login`

公开 API(5 个 fn):
  · gh_health()                  → {ok, installed, version, bin}
  · repo_info(owner, name)       → {ok, repo: {name, stars, ...}}
  · issue_get(owner, name, n)    → {ok, issue: {title, body, ...}}
  · pr_get(owner, name, n)       → {ok, pr: {title, body, ...}}
  · search(query, kind, limit)   → list[{url, title, snippet}]
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

GH_BIN = os.environ.get("GH_BIN", "gh")
GH_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# 内部:跑 gh 子命令
# ---------------------------------------------------------------------------

def _run(args: list[str], *, timeout: float = GH_TIMEOUT) -> dict[str, Any]:
    """调 gh 子命令,统一异常处理。

    返回值形态:
      成功 + JSON stdout → {ok: True, data: <parsed_dict_or_list>}
      成功 + 非 JSON    → {ok: True, data: {"raw": stdout}}
      失败              → {ok: False, error: "gh_xxx", ...}
    """
    cmd = [GH_BIN, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "installed": False,
                "error": "gh_not_installed",
                "hint": "gh CLI 未安装(https://cli.github.com)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "gh_timeout",
                "hint": f"超时 {timeout}s"}
    except Exception as e:
        return {"ok": False, "error": f"gh_{type(e).__name__}",
                "detail": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": "gh_failed",
                "returncode": proc.returncode,
                "stderr": proc.stderr[-300:]}
    # 解析 stdout JSON
    try:
        return {"ok": True, "data": json.loads(proc.stdout)}
    except json.JSONDecodeError:
        return {"ok": True, "data": {"raw": proc.stdout}}


# ---------------------------------------------------------------------------
# 公开 API 1: gh_health(版本探活)
# ---------------------------------------------------------------------------

def gh_health() -> dict[str, Any]:
    """检查 gh CLI 是否装 + 版本号。无需 gh auth 登录。"""
    bin_path = shutil.which(GH_BIN)
    if not bin_path:
        return {"ok": True, "installed": False, "version": "",
                "bin": "", "auth_status": "n/a",
                "hint": "gh CLI 未安装(https://cli.github.com)"}
    r = _run(["--version"], timeout=5.0)
    ver = ""
    if r.get("ok"):
        data = r.get("data")
        if isinstance(data, dict) and data.get("raw"):
            ver = data["raw"].strip()
        elif isinstance(data, str):
            ver = data.strip()
    # 探测 auth 状态(不影响主流程,只是给 UI 看)
    auth = _run(["auth", "status"], timeout=5.0)
    auth_status = "logged_in" if auth.get("ok") else "not_logged_in"
    return {"ok": True, "installed": True, "version": ver,
            "bin": bin_path, "auth_status": auth_status}


# ---------------------------------------------------------------------------
# 公开 API 2: repo_info(仓库元数据)
# ---------------------------------------------------------------------------

def repo_info(owner: str, name: str, *,
              timeout: float = GH_TIMEOUT) -> dict[str, Any]:
    """读 GitHub 仓库元数据。无需 token(public repo)。

    返回:{ok, owner, name, repo: {name, description, stars, forks, ...}}
    失败返:{ok: False, error: "gh_xxx", ...}
    """
    if not owner or not name:
        return {"ok": False, "error": "missing_owner_or_name"}
    # gh api + --jq 取必要字段(减小 stdout)
    r = _run(["api", f"repos/{owner}/{name}",
              "--jq",
              "{name: .name, description: .description, "
              "stars: .stargazers_count, forks: .forks_count, "
              "language: .language, default_branch: .default_branch, "
              "html_url: .html_url, updated_at: .updated_at, "
              "topics: .topics, license: .license.spdx_id}"],
             timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "owner": owner, "name": name, **r}
    repo = r["data"] if isinstance(r["data"], dict) else {}
    return {"ok": True, "owner": owner, "name": name, "repo": repo}


# ---------------------------------------------------------------------------
# 公开 API 3: issue_get
# ---------------------------------------------------------------------------

def issue_get(owner: str, name: str, number: int,
              *, timeout: float = GH_TIMEOUT) -> dict[str, Any]:
    """读 GitHub issue。无需 token(public)。"""
    if not owner or not name or not number:
        return {"ok": False, "error": "missing_fields"}
    r = _run(["api", f"repos/{owner}/{name}/issues/{int(number)}",
              "--jq",
              "{title: .title, body: .body, state: .state, "
              "labels: [.labels[].name], user: .user.login, "
              "created_at: .created_at, updated_at: .updated_at, "
              "comments: .comments, html_url: .html_url, "
              "is_pull_request: .pull_request}"],
             timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "owner": owner, "name": name,
                "number": int(number), **r}
    issue = r["data"] if isinstance(r["data"], dict) else {}
    return {"ok": True, "owner": owner, "name": name,
            "number": int(number), "issue": issue}


# ---------------------------------------------------------------------------
# 公开 API 4: pr_get
# ---------------------------------------------------------------------------

def pr_get(owner: str, name: str, number: int,
           *, timeout: float = GH_TIMEOUT) -> dict[str, Any]:
    """读 GitHub PR。无需 token(public)。"""
    if not owner or not name or not number:
        return {"ok": False, "error": "missing_fields"}
    r = _run(["api", f"repos/{owner}/{name}/pulls/{int(number)}",
              "--jq",
              "{title: .title, body: .body, state: .state, "
              "user: .user.login, head: .head.ref, base: .base.ref, "
              "merged: .merged, mergeable: .mergeable, "
              "created_at: .created_at, html_url: .html_url, "
              "additions: .additions, deletions: .deletions, "
              "commits: .commits, changed_files: .changed_files}"],
             timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "owner": owner, "name": name,
                "number": int(number), **r}
    pr = r["data"] if isinstance(r["data"], dict) else {}
    return {"ok": True, "owner": owner, "name": name,
            "number": int(number), "pr": pr}


# ---------------------------------------------------------------------------
# 公开 API 5: search
# ---------------------------------------------------------------------------

def search(query: str, kind: str = "repos",
           limit: int = 10, *,
           timeout: float = GH_TIMEOUT) -> list[dict[str, Any]]:
    """GitHub 搜索。kind ∈ {repos, issues, prs, code}。

    返回 list[{url, title, snippet}](空 list = 失败/无结果)。
    """
    if not query or not query.strip():
        return []
    if kind not in ("repos", "issues", "prs", "code"):
        kind = "repos"
    safe_limit = min(max(int(limit), 1), 30)
    # 不同 search kind 支持的 --json 字段不同(gh 2.96 实测):
    #   repos: 没有 title, 用 name + description
    #   issues / prs: 有 title + number + repository + url
    #   code: 字段更少, 只 url + repository + path
    fields_by_kind = {
        "repos":   "name,url,description,language,stargazersCount",
        "issues":  "title,url,number,repository,state",
        "prs":     "title,url,number,repository,state",
        "code":    "name,path,repository,url",
    }
    fields = fields_by_kind.get(kind, fields_by_kind["repos"])
    r = _run(["search", kind, query,
              "--limit", str(safe_limit),
              "--json", fields],
             timeout=timeout)
    if not r.get("ok"):
        log.warning("gh search failed: %s", r.get("error"))
        return []
    items = r["data"]
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items[:safe_limit]:
        if kind == "repos":
            url = it.get("url", "") or \
                  f"https://github.com/{it.get('name', '')}"
            out.append({
                "url": url,
                "title": it.get("name", ""),
                "snippet": it.get("description", "") or "",
            })
        elif kind == "code":
            repo = it.get("repository", {})
            repo_name = repo.get("nameWithOwner", "") if isinstance(repo, dict) else ""
            out.append({
                "url": it.get("url", ""),
                "title": it.get("path") or it.get("name", ""),
                "snippet": f"code in {repo_name}",
            })
        else:  # issues / prs
            repo = it.get("repository", {})
            repo_name = repo.get("nameWithOwner", "") if isinstance(repo, dict) else ""
            out.append({
                "url": it.get("url", ""),
                "title": it.get("title", ""),
                "snippet": f"{kind} #{it.get('number', '?')} in {repo_name}",
            })
    return out