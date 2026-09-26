# -*- coding: utf-8 -*-
"""gh_api_provider.py — github.com URL → gh api 直查(2026-09-26 ship,P3j T21-C)。

避免 jina / urllib 抓 github.com HTML(经常 200 但内容粗糙 + 403 风控),
直接走 gh api 拿结构化数据。

支持的 URL 形式:
  · https://github.com/{owner}/{repo}            → repo_info(stars/forks/desc)
  · https://github.com/{owner}/{repo}/issues/{N} → issue_get(title/body/labels)
  · https://github.com/{owner}/{repo}/pull/{N}   → issue_get (gh api 同一端点)
  · 其他 github.com 路径                          → error=unsupported_github_url

公开 API:
  · gh_api_provider(url, options) → {content, meta}
    — 作为 fetcher 给 web_fetch.fetch() 用
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def gh_api_provider(url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """github.com URL → 结构化 markdown(走 gh api)。"""
    options = options or {}
    timeout = float(options.get("timeout", 30.0))

    if not url or not isinstance(url, str):
        return {"content": "", "meta": {"fetcher": "gh_api", "ok": False,
                                        "error": "bad_url"}}

    parsed = urlparse(url)
    if parsed.hostname not in ("github.com", "www.github.com"):
        return {"content": "", "meta": {"fetcher": "gh_api", "ok": False,
                                        "error": "non_github_url"}}

    parts = [p for p in parsed.path.split("/") if p]

    # /owner/repo
    if len(parts) == 2:
        try:
            from prisir_work import gh_bridge as _gh
            r = _gh.repo_info(parts[0], parts[1], timeout=timeout)
            if r.get("ok"):
                repo = r.get("repo", {}) or {}
                content = (f"# {repo.get('name','')}\n"
                           f"_{repo.get('description','')}_\n\n"
                           f"**Stars**: {repo.get('stargazers_count', 0) or repo.get('stars', 0)}  "
                           f"**Forks**: {repo.get('forks_count', 0) or repo.get('forks', 0)}  "
                           f"**Language**: {repo.get('language','')}\n\n"
                           f"**URL**: {repo.get('html_url', url)}\n"
                           f"**Updated**: {repo.get('updated_at','')}")
                return {"content": content,
                        "meta": {"fetcher": "gh_api", "ok": True,
                                 "repo": repo,
                                 "owner": parts[0], "name": parts[1]}}
            return {"content": "", "meta": {"fetcher": "gh_api",
                                            "ok": False,
                                            "owner": parts[0], "name": parts[1],
                                            "error": r.get("error", "gh_failed"),
                                            "detail": r.get("stderr", "")}}
        except Exception as e:  # noqa: BLE001
            return {"content": "", "meta": {"fetcher": "gh_api", "ok": False,
                                            "error": type(e).__name__,
                                            "owner": parts[0], "name": parts[1]}}

    # /owner/repo/issues/N 或 /owner/repo/pull/N
    if len(parts) == 4 and parts[2] in ("issues", "pull"):
        try:
            from prisir_work import gh_bridge as _gh
            number = int(parts[3])
            r = _gh.issue_get(parts[0], parts[1], number, timeout=timeout)
            if r.get("ok"):
                obj = r.get("issue", {}) or {}
                is_pr = obj.get("is_pull_request") not in (None, False)
                kind = "PR" if is_pr else "Issue"
                content = (f"# {obj.get('title','')}\n\n"
                           f"**Kind**: {kind}  **State**: {obj.get('state','')}\n"
                           f"**User**: {(obj.get('user') or {}).get('login','') if isinstance(obj.get('user'), dict) else obj.get('user','')}\n"
                           f"**URL**: {obj.get('html_url', url)}\n"
                           f"**Comments**: {obj.get('comments', 0)}\n\n"
                           f"{(obj.get('body','') or '')[:5000]}")
                return {"content": content,
                        "meta": {"fetcher": "gh_api", "ok": True,
                                 "issue": obj,
                                 "owner": parts[0], "name": parts[1],
                                 "number": number,
                                 "is_pr": is_pr}}
            return {"content": "", "meta": {"fetcher": "gh_api", "ok": False,
                                            "error": r.get("error", "gh_failed"),
                                            "owner": parts[0], "name": parts[1],
                                            "number": number}}
        except (ValueError, Exception) as e:  # noqa: BLE001
            return {"content": "", "meta": {"fetcher": "gh_api", "ok": False,
                                            "error": type(e).__name__}}

    return {"content": "", "meta": {"fetcher": "gh_api", "ok": False,
                                    "error": "unsupported_github_url",
                                    "supported": [
                                        "/owner/repo",
                                        "/owner/repo/issues/N",
                                        "/owner/repo/pull/N",
                                    ]}}