"""agent_reach_bridge.py — Agent-Reach 子进程桥(2026-09-26 ship,P3j T20-A)。

调 agent-reach CLI,统一异常处理 + fail-soft 返空。绝不 raise 给上层。

实际 CLI 形态(2026-09-26 探明,agent-reach 0.1.0):
  agent-reach list              → 列出已装 channel
  agent-reach list --all        → 列出所有可用 channel
  agent-reach install <channel> → 安装 channel
  agent-reach doctor            → 平台健康检查(明文)
  agent-reach doctor --json     → JSON 数组 [{channel, ok, auth, detail, installed}, ...]
  agent-reach get <channel> <query> [--limit N] [--json] → 读/搜统一入口
                                  返回 {channel, command, query, fetched_at, items: [...]}
  agent-reach skill             → 列出已注册 skill
  agent-reach cache --channel X → 清理缓存

公开 API:
  · doctor()      → {ok, installed, version, bin, channels: [{id, status, ...}]}
  · platforms()   → 静态 14 平台目录(包含未安装的占位)
  · read(channel, url, *, timeout) → {ok, content, title, meta}
  · search(channel, query, limit, timeout) → {ok, results: [...], sources}

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

# 14 平台静态目录(对齐 README 列出的 14 平台)。
# 当前 0.1.0 仅 rss + youtube ship 出来;其他 platform 列在 catalog 里作为
# 未来 channel 占位 — UI 仍渲染,但 🔬 测试 / search 会提示未安装。
PLATFORMS: list[dict[str, str]] = [
    {"id": "rss",                "title": "RSS 通用",     "category": "feed",
     "cli_id": "rss"},
    {"id": "youtube-subtitle",   "title": "YouTube 字幕",  "category": "global_video",
     "cli_id": "youtube"},
    {"id": "xhs",                "title": "小红书",        "category": "cn_social",
     "cli_id": "xhs"},
    {"id": "bilibili-subtitle",  "title": "B站字幕",       "category": "cn_video",
     "cli_id": "bilibili-subtitle"},
    {"id": "github",             "title": "GitHub",        "category": "dev",
     "cli_id": "github"},
    {"id": "v2ex",               "title": "V2EX",          "category": "cn_tech",
     "cli_id": "v2ex"},
    {"id": "bilibili-search",    "title": "B站搜索",       "category": "cn_video",
     "cli_id": "bilibili-search"},
    {"id": "weibo",              "title": "微博",          "category": "cn_social",
     "cli_id": "weibo"},
    {"id": "zhihu",              "title": "知乎",          "category": "cn_qa",
     "cli_id": "zhihu"},
    {"id": "exa",                "title": "Exa 搜索",      "category": "search",
     "cli_id": "exa"},
    {"id": "jina",               "title": "Jina Reader",   "category": "read",
     "cli_id": "jina"},
    {"id": "twitter",            "title": "Twitter/X",    "category": "global_social",
     "cli_id": "twitter"},
    {"id": "reddit",             "title": "Reddit",       "category": "global_social",
     "cli_id": "reddit"},
    {"id": "linkedin",           "title": "LinkedIn",     "category": "career",
     "cli_id": "linkedin"},
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
    cmd = [_REACH_BIN, *args]
    # 默认加 --json(若是 doctor / get)
    # 子命令若已带 --json 不重复加
    if "--json" not in args:
        cmd.append("--json")
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
        stderr = (proc.stderr or "").strip()
        # 推断更具体的 error 给 UI 提示
        err_kind = "agent_reach_failed"
        if "needs a feed URL" in stderr:
            err_kind = "reach_missing_query"
        elif "no channel named" in stderr or "Channel" in stderr and "not installed" in stderr:
            err_kind = "reach_channel_not_installed"
        elif "not found" in stderr.lower() or "no such" in stderr.lower():
            err_kind = "reach_unknown_platform"
        return {"ok": False, "error": err_kind,
                "returncode": proc.returncode,
                "stderr": stderr[-300:]}
    # parse stdout JSON
    out = (proc.stdout or "").strip()
    if not out:
        return {"ok": True, "data": {}}
    try:
        return {"ok": True, "data": json.loads(out)}
    except json.JSONDecodeError:
        # 非 JSON stdout — 包成 raw(给 markdown 输出兜底)
        return {"ok": True, "data": {"raw": out}}


def _get_version() -> str:
    """读 agent-reach version。失败 → ''。"""
    try:
        import importlib.metadata as _md
        return _md.version("agent-reach")
    except Exception:  # noqa: BLE001
        try:
            proc = subprocess.run(
                [_REACH_BIN, "--version"], capture_output=True, text=True,
                timeout=3.0, encoding="utf-8", errors="replace")
            return (proc.stdout or "").strip()
        except Exception:  # noqa: BLE001
            return ""


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def _resolve_cli_id(platform: str) -> tuple[str, dict[str, str]]:
    """把 PrisirAI 平台 id 翻译成 agent-reach CLI channel id。"""
    for p in PLATFORMS:
        if p["id"] == platform:
            return p["cli_id"], p
    return platform, {"id": platform, "title": platform, "cli_id": platform}


def doctor() -> dict[str, Any]:
    """检查 agent-reach 安装 + 已装 channel 状态。

    返回 shape:
      {
        "ok": True,
        "installed": True/False,
        "bin": "C:/Python/Scripts/agent-reach.exe" or "",
        "version": "0.1.0" or "",
        "channels": [{"id": "rss", "status": "ok"|"warn"|"err"|"unknown", "detail": "..."}],
        "hint": "..." (only when installed=False)
      }
    """
    bin_path = shutil.which(_REACH_BIN)
    if not bin_path:
        return {"ok": True, "installed": False, "version": "",
                "bin": "", "channels": [],
                "hint": f"{_REACH_BIN} 未安装 · pip install agent-reach"}
    version = _get_version()
    r = _run(["doctor"], timeout=15.0)
    channels: list[dict[str, Any]] = []
    if r.get("ok") and isinstance(r["data"], list):
        for ch in r["data"]:
            cid = ch.get("channel", "")
            ok = ch.get("ok", False)
            channels.append({
                "id": cid,
                "status": "ok" if ok else "warn",
                "auth": ch.get("auth", "unknown"),
                "detail": ch.get("detail", ""),
                "installed": ch.get("installed", False),
            })
    elif r.get("ok") and isinstance(r["data"], dict):
        # 兼容旧版本返 dict
        chs = r["data"].get("channels") or r["data"].get("platforms") or []
        for ch in chs:
            cid = ch.get("id") or ch.get("channel", "")
            ok = ch.get("ok") or ch.get("status") == "ok"
            channels.append({"id": cid, "status": "ok" if ok else "warn",
                             "auth": ch.get("auth", ""), "detail": ch.get("detail", ""),
                             "installed": ch.get("installed", True)})
    return {"ok": True, "installed": True,
            "bin": bin_path,
            "version": version,
            "channels": channels,
            "error": "" if r.get("ok") else r.get("error", "")}


def _ensure_cli_id(platform: str) -> str:
    cli_id, _ = _resolve_cli_id(platform)
    return cli_id


def read(platform: str, url: str, *, timeout: float = _REACH_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """读某 channel 的 url(query 字段)→ {content, title, meta}。

    CLI 形态:agent-reach get <channel> <url>

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
    cli_id = _ensure_cli_id(platform)
    r = _run(["get", cli_id, url], timeout=timeout)
    if not r.get("ok"):
        out = {"ok": False, "platform": platform, "url": url, **r}
        stderr = r.get("stderr", "")
        # 通道未安装 — 给明确安装提示
        if r.get("error") == "reach_channel_not_installed":
            out["hint"] = (f"{platform} channel 未安装 · agent-reach install {cli_id}")
        elif r.get("error") == "reach_missing_query":
            out["hint"] = (f"{platform} 需要 query(URL/关键词),不能为空")
        elif r.get("error") == "reach_unknown_platform":
            out["hint"] = (f"agent-reach 不支持 {platform} · 平台 ID 不在索引")
        elif not out.get("hint"):
            out["hint"] = stderr or r.get("error", "未知错误")
        return out
    data = r["data"]
    items = data.get("items", []) or []
    # 拼 content:把 items 的 text 拼成一段
    parts: list[str] = []
    for it in items:
        if it.get("title"):
            parts.append(f"## {it['title']}")
        if it.get("author") or it.get("published_at"):
            meta = " · ".join(filter(None, [it.get("author"), it.get("published_at")]))
            parts.append(f"({meta})")
        if it.get("url"):
            parts.append(f"URL: {it['url']}")
        if it.get("text"):
            parts.append(it["text"])
        parts.append("")
    content = "\n".join(parts).strip() or data.get("raw", "")
    title = items[0].get("title", "") if items else data.get("channel", platform)
    return {"ok": True, "platform": platform, "url": url,
            "content": content,
            "title": title,
            "meta": {"channel": data.get("channel", ""),
                     "command": data.get("command", ""),
                     "fetched_at": data.get("fetched_at", ""),
                     "cached": data.get("cached", False),
                     "item_count": len(items)}}


def search(platform: str, query: str, limit: int = 10,
           *, timeout: float = _REACH_TIMEOUT_DEFAULT) -> dict[str, Any]:
    """搜某 channel 关键词 → [{url, title, snippet}]。

    CLI 形态:agent-reach get <channel> <query> --limit N
    部分 channel(如 rss)query 是 feed URL,空 query 时拉最新条目。

    返回 shape(成功):
      {"ok": True, "platform": ..., "query": ..., "results": [...], "sources": [platform]}
    """
    platform = (platform or "").strip()
    query = (query or "").strip()
    if not platform:
        return {"ok": False, "error": "missing_params",
                "hint": "platform 必填"}
    cli_id = _ensure_cli_id(platform)
    # query 可空(rss 不需要关键词)— 传空字符串让 CLI 用 default
    cap = max(1, min(limit, 50))
    args = ["get", cli_id, query, "--limit", str(cap)]
    r = _run(args, timeout=timeout)
    if not r.get("ok"):
        out = {"ok": False, "platform": platform, "query": query, **r}
        stderr = r.get("stderr", "")
        if r.get("error") == "reach_channel_not_installed":
            out["hint"] = (f"{platform} channel 未安装 · agent-reach install {cli_id}")
        elif r.get("error") == "reach_missing_query":
            out["hint"] = (f"{platform} 需要 query(URL/关键词),不能为空")
        elif r.get("error") == "reach_unknown_platform":
            out["hint"] = (f"agent-reach 不支持 {platform} · 平台 ID 不在索引")
        elif not out.get("hint"):
            out["hint"] = stderr or r.get("error", "未知错误")
        return out
    data = r["data"]
    items = data.get("items", []) or []
    results: list[dict[str, Any]] = []
    for it in items[:cap]:
        results.append({
            "url": it.get("url", ""),
            "title": it.get("title", ""),
            "snippet": (it.get("text", "") or "")[:300],
            "author": it.get("author", ""),
            "published_at": it.get("published_at", ""),
        })
    return {"ok": True, "platform": platform, "query": query,
            "results": results,
            "sources": [platform] * len(results)}


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