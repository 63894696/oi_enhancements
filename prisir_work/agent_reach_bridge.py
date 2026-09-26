"""agent_reach_bridge.py — Agent-Reach 子进程桥(2026-09-26 ship,P3j T20-A)。

调 agent-reach CLI,统一异常处理 + fail-soft 返空。绝不 raise 给上层。

设计:
  · 每个动作一个子进程(< 30s timeout,P0 平台都给 30s)
  · JSON 输出(agent-reach 0.x 支持 --json)
  · stdout 非 JSON → 自动包成 raw 字段
  · 失败一律返 {ok: False, error: "agent_reach_xxx", hint: "..."}

公开 API:
  · doctor()         → {ok, installed, version, bin, platforms: [{id, status, hint}]}
  · platforms()      → 14 个 platform 列表(同 doctor 但不带 installed)
  · read(platform, url, *, timeout=30)  → {ok, content, title, meta}
  · search(platform, query, limit=10, timeout=30) → {ok, results: [...], sources}

为什么用子进程桥(而不直接 import agent_reach 模块):
  · agent-reach 是独立 CLI,version 演变更快(0.x → 1.x 可能有 breaking change)
  · 子进程隔离 — agent-reach 内部异常 / 死锁不会拖垮主进程
  · 复用我们已有的 web_search.register_provider / web_fetch.register_fetcher 模式
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_REACH_BIN = os.environ.get("AGENT_REACH_BIN", "agent-reach")
_REACH_TIMEOUT_DEFAULT = 30.0

# P0 6 平台默认开(用户拍板 2026-09-26):
# 小红书 / B站字幕 / GitHub / V2EX / YouTube字幕 / RSS
P0_PLATFORMS: set[str] = {
    "xhs", "bilibili-subtitle", "github", "v2ex",
    "youtube-subtitle", "rss",
}

# 14 平台静态目录(对齐 agent-reach 0.x 的 platforms 子命令)。
# 每个: id / 中文 title / category(分组)
PLATFORMS: list[dict[str, str]] = [
    {"id": "xhs",               "title": "小红书",      "category": "cn_social"},
    {"id": "bilibili-subtitle", "title": "B站字幕",     "category": "cn_video"},
    {"id": "github",            "title": "GitHub",      "category": "dev"},
    {"id": "v2ex",              "title": "V2EX",        "category": "cn_tech"},
    {"id": "youtube-subtitle",  "title": "YouTube 字幕", "category": "global_video"},
    {"id": "rss",               "title": "RSS 通用",    "category": "feed"},
    {"id": "bilibili-search",   "title": "B站搜索",     "category": "cn_video"},
    {"id": "weibo",             "title": "微博",        "category": "cn_social"},
    {"id": "zhihu",             "title": "知乎",        "category": "cn_qa"},
    {"id": "exa",               "title": "Exa 搜索",    "category": "search"},
    {"id": "jina",              "title": "Jina Reader", "category": "read"},
    {"id": "twitter",           "title": "Twitter/X",   "category": "global_social"},
    {"id": "reddit",            "title": "Reddit",      "category": "global_social"},
    {"id": "linkedin",          "title": "LinkedIn",    "category": "career"},
]

# 默认启用集合(落地在 data_dir/agent_reach_enabled.json — 留给后续 UI toggle)
DEFAULT_ENABLED: set[str] = P0_PLATFORMS.copy()


# ---------------------------------------------------------------------------
# 子进程执行核心
# ---------------------------------------------------------------------------

def _run(args: list[str], *, timeout: float = _REACH_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """调 agent-reach 子命令,统一异常处理。

    返回:
      · 成功: {"ok": True, "data": <parsed JSON or {raw: stdout}>}
      · 失败: {"ok": False, "error": "...", "hint": "..."}

    永远不 raise;让上层 fail-soft 处理。
    """
    cmd = [_REACH_BIN, *args, "--json"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return {"ok": False, "installed": False,
                "error": "agent_reach_not_installed",
                "hint": f"pip install agent-reach (找不到 {_REACH_BIN})"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "agent_reach_timeout",
                "hint": f"超时 {timeout}s,可降低 timeout 或重试"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"agent_reach_{type(e).__name__}",
                "detail": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "error": "agent_reach_failed",
                "returncode": proc.returncode,
                "stderr": proc.stderr[-300:] if proc.stderr else ""}
    # parse stdout JSON
    out = (proc.stdout or "").strip()
    if not out:
        return {"ok": True, "data": {}}
    try:
        return {"ok": True, "data": json.loads(out)}
    except json.JSONDecodeError:
        # 非 JSON stdout — 包成 raw(给 markdown 输出兜底)
        return {"ok": True, "data": {"raw": out}}


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def doctor() -> dict[str, Any]:
    """检查 agent-reach 安装 + 14 平台状态。

    返回 shape:
      {
        "ok": True,
        "installed": True/False,
        "bin": "C:/Python/Scripts/agent-reach.exe" or "",
        "version": "0.1.0" or "",
        "platforms": [{"id": ..., "status": "ok"|"warn"|"err", "hint": "..."}],
        "hint": "..." (only when installed=False)
      }
    """
    bin_path = shutil.which(_REACH_BIN)
    if not bin_path:
        return {"ok": True, "installed": False, "version": "",
                "bin": "", "platforms": [],
                "hint": f"{_REACH_BIN} 未安装 · pip install agent-reach"}
    r = _run(["doctor"], timeout=15.0)
    if not r.get("ok"):
        return {"ok": True, "installed": True,
                "version": "", "bin": bin_path,
                "platforms": [], "error": r.get("error", ""),
                "hint": r.get("hint") or r.get("detail") or r.get("error", "")}
    return {"ok": True, "installed": True,
            "bin": bin_path,
            "version": str(r["data"].get("version", "")),
            "platforms": r["data"].get("platforms", []) or []}


def read(platform: str, url: str, *, timeout: float = _REACH_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """读某平台 URL → {content, title, meta}。

    返回 shape(成功):
      {"ok": True, "platform": ..., "url": ..., "content": "<字幕/正文>",
       "title": "...", "meta": {...}}
    失败: {"ok": False, "platform": ..., "url": ..., "error": "...", "hint": "..."}
    """
    platform = (platform or "").strip()
    url = (url or "").strip()
    if not platform or not url:
        return {"ok": False, "error": "missing_params",
                "hint": "platform 和 url 都必填"}
    r = _run(["read", platform, url], timeout=timeout)
    if not r.get("ok"):
        out = {"ok": False, "platform": platform, "url": url, **r}
        # 兼容两种 error:agent_reach_not_installed 时上面已给 hint
        if not out.get("hint"):
            out["hint"] = r.get("hint") or r.get("detail") or r.get("error", "未知错误")
        return out
    data = r["data"]
    return {"ok": True, "platform": platform, "url": url,
            "content": str(data.get("content", "")),
            "title": str(data.get("title", "")),
            "meta": data.get("meta", {}) or {}}


def search(platform: str, query: str, limit: int = 10,
           *, timeout: float = _REACH_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """搜某平台关键词 → [{url, title, snippet}]。

    返回 shape(成功):
      {"ok": True, "platform": ..., "query": ..., "results": [...], "sources": [platform]}
    """
    platform = (platform or "").strip()
    query = (query or "").strip()
    if not platform or not query:
        return {"ok": False, "error": "missing_params",
                "hint": "platform 和 query 都必填"}
    r = _run(["search", platform, query, "--limit", str(max(1, min(limit, 50)))],
             timeout=timeout)
    if not r.get("ok"):
        out = {"ok": False, "platform": platform, "query": query, **r}
        if not out.get("hint"):
            out["hint"] = r.get("hint") or r.get("detail") or r.get("error", "未知错误")
        return out
    items = r["data"].get("results", []) or []
    return {"ok": True, "platform": platform, "query": query,
            "results": items[:max(1, min(limit, 50))],
            "sources": [platform] * len(items[:max(1, min(limit, 50))])}


def platforms() -> list[dict[str, Any]]:
    """14 平台目录(静态,与 agent-reach 安装状态无关)。

    返回 shape:
      [{"id": ..., "title": ..., "category": ..., "default_on": bool, "p0": bool}]
    """
    return [{"id": p["id"], "title": p["title"],
             "category": p["category"],
             "default_on": p["id"] in DEFAULT_ENABLED,
             "p0": p["id"] in P0_PLATFORMS}
            for p in PLATFORMS]


# ---------------------------------------------------------------------------
# CLI 自检(开发/调试用)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json
    import sys as _sys
    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "doctor"
    if cmd == "doctor":
        print(_json.dumps(doctor(), ensure_ascii=False, indent=2))
    elif cmd == "platforms":
        print(_json.dumps(platforms(), ensure_ascii=False, indent=2))
    elif cmd == "read":
        plat = _sys.argv[2] if len(_sys.argv) > 2 else ""
        url = _sys.argv[3] if len(_sys.argv) > 3 else ""
        print(_json.dumps(read(plat, url), ensure_ascii=False, indent=2))
    elif cmd == "search":
        plat = _sys.argv[2] if len(_sys.argv) > 2 else ""
        q = _sys.argv[3] if len(_sys.argv) > 3 else ""
        print(_json.dumps(search(plat, q, limit=5), ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd} (use doctor/platforms/read/search)")