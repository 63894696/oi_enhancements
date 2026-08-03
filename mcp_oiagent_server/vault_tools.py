#!/usr/bin/env python3
"""vault_tools.py — OIagent MCP 工具:Obsidian vault 搜索 + 读取

集成位置: /c/Users/Administrator/oi_enhancements/mcp_oiagent_server/server.py
已在 server.py 的 dynamic_registry 中自动注册。

工具列表:
- vault_search: 在 Obsidian vault 中搜索(走 Everything + AnyTXT 双引擎)
- vault_read: 读取 vault 中指定笔记的完整内容
- vault_list: 列出 vault 目录结构

依赖:
- Everything HTTP API: http://127.0.0.1:8765/
- AnyTXT JSON-RPC: http://127.0.0.1:9920
- Obsidian vault: C:/Users/Administrator/Documents/ObsidianVault

v0.1 (2026-08-02): 初始版本 — vault_search + vault_read + vault_list
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ── 配置 ──────────────────────────────────────────────────────────
VAULT_DIR = Path(r"C:\Users\Administrator\Documents\ObsidianVault")
EVERYTHING_URL = "http://127.0.0.1:8765/"
ANYTXT_URL = "http://127.0.0.1:9920"
TIMEOUT = 10


# ── Tool 定义(供 dynamic_registry 自动发现) ──────────────────────
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "vault_search",
        "description": "在 Obsidian vault 中搜索笔记。支持关键词搜索(+可选路径过滤/扩展名过滤),返回匹配笔记的 path/title/snippet。双引擎:Everything(文件名)+AnyTXT(正文)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "path": {"type": "string", "description": "可选:限定搜索路径(相对于 vault 根)"},
                "ext": {"type": "string", "description": "可选:文件扩展名过滤,如 .md"},
                "limit": {"type": "integer", "description": "返回数量上限(默认 10)", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "vault_read",
        "description": "读取 Obsidian vault 中指定笔记的完整内容。按文件名或相对路径定位。返回 frontmatter + 正文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "笔记路径(相对于 vault 根,如 sessions/2026-08-02-xxx.md 或直接文件名)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "vault_list",
        "description": "列出 Obsidian vault 的目录结构。返回指定路径下的子目录和文件列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要列出的路径(相对于 vault 根,默认 '' 表示根)"},
                "depth": {"type": "integer", "description": "递归深度(默认 1)", "default": 1},
            },
            "required": [],
        },
    },
]


# ── 实现 ──────────────────────────────────────────────────────────


def _err(stage: str, exc: Exception) -> str:
    return json.dumps(
        {"ok": False, "error": str(exc), "stage": stage},
        ensure_ascii=False,
    )


def _everything_search(query: str, path: str | None = None, ext: str | None = None, limit: int = 10) -> list[dict]:
    """调用 Everything HTTP API 搜索文件名。"""
    q = {"search": query, "json": "1", "count": str(limit), "path_column": "1"}
    # 限定到 vault 路径
    vault_path = str(VAULT_DIR)
    if path:
        q["search"] = f"{query} path:{vault_path}\\{path}"
    else:
        q["search"] = f"{query} path:{vault_path}"
    if ext:
        q["search"] += f" ext:{ext}"
    url = EVERYTHING_URL + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "oiagent-vault/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            results = []
            for item in data.get("results", [])[:limit]:
                p = item.get("path", "")
                # 只返回 vault 内的文件
                if not p.startswith(vault_path):
                    continue
                results.append({
                    "path": p,
                    "name": item.get("name", ""),
                    "size": item.get("size", 0),
                })
            return results
    except Exception as e:
        return [{"_error": f"Everything search failed: {e}"}]


def _anytxt_search(query: str, limit: int = 10) -> list[dict]:
    """调用 AnyTXT JSON-RPC 搜索正文。"""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "GetResult",
        "params": {"keyword": query, "maxResults": limit},
        "id": 1,
    }).encode("utf-8")
    req = urllib.request.Request(
        ANYTXT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            results = []
            for item in data.get("result", {}).get("items", [])[:limit]:
                results.append({
                    "path": item.get("path", ""),
                    "title": item.get("title", ""),
                    "snippet": item.get("snippet", "")[:200],
                })
            return results
    except Exception as e:
        return [{"_error": f"AnyTXT search failed: {e}"}]


def _dedup_merge(everything_results: list[dict], anytxt_results: list[dict], limit: int) -> list[dict]:
    """合并两个引擎结果,按 path 去重,优先保留 AnyTXT 的 snippet。"""
    seen: dict[str, dict] = {}
    for r in anytxt_results:
        if "_error" not in r:
            seen[r["path"]] = r
    for r in everything_results:
        if "_error" not in r:
            p = r["path"]
            if p not in seen:
                seen[p] = r
            # 如果 Everything 结果没有 snippet,用 AnyTXT 补充
    return list(seen.values())[:limit]


def vault_search_impl(query: str, path: str | None = None, ext: str | None = None, limit: int = 10) -> str:
    """在 Obsidian vault 中搜索笔记。

    策略:
    1. 先用 Everything 搜文件名(快,毫秒级)
    2. 再用 AnyTXT 搜正文(准,有 snippet)
    3. 合并去重,返回最优结果
    """
    try:
        # 限定搜索范围到 vault
        search_path = str(VAULT_DIR) if not path else str(VAULT_DIR / path)

        # 并行搜索
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as exe:
            f_ever = exe.submit(_everything_search, query, path, ext, limit * 2)
            f_anytxt = exe.submit(_anytxt_search, query, limit * 2)
            ever_results = f_ever.result()
            anytxt_results = f_anytxt.result()

        merged = _dedup_merge(ever_results, anytxt_results, limit)

        # 格式化输出
        output = []
        for r in merged:
            if "_error" in r:
                continue
            name = Path(r["path"]).name
            output.append(f"- **{name}**\n  路径: `{r['path']}`")
            if "snippet" in r and r["snippet"]:
                output.append(f"  摘要: {r['snippet'][:100]}...")

        return json.dumps({
            "ok": True,
            "query": query,
            "count": len(output),
            "results": output,
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return _err("vault_search", e)


def vault_read_impl(path: str) -> str:
    """读取 vault 中指定笔记的完整内容。"""
    try:
        # 解析路径
        if path.startswith("/"):
            full_path = Path(path)
        else:
            full_path = VAULT_DIR / path.replace("/", "\\")

        if not full_path.exists():
            # 尝试模糊匹配
            matches = list(VAULT_DIR.rglob(full_path.name))
            if matches:
                full_path = matches[0]
            else:
                return json.dumps({"ok": False, "error": f"文件不存在: {path}"}, ensure_ascii=False)

        content = full_path.read_text(encoding="utf-8", errors="replace")
        return json.dumps({
            "ok": True,
            "path": str(full_path),
            "size": len(content),
            "content": content[:50000],  # 限制 50KB
        }, ensure_ascii=False)

    except Exception as e:
        return _err("vault_read", e)


def vault_list_impl(path: str = "", depth: int = 1) -> str:
    """列出 vault 目录结构。"""
    try:
        base = VAULT_DIR / path.replace("/", "\\") if path else VAULT_DIR
        if not base.exists():
            return json.dumps({"ok": False, "error": f"目录不存在: {path}"}, ensure_ascii=False)

        result = {"ok": True, "path": str(base), "entries": []}
        _list_dir(base, result["entries"], max_depth=depth)
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return _err("vault_list", e)


def _list_dir(dir_path: Path, entries: list, max_depth: int = 1, current_depth: int = 0):
    """递归列出目录。"""
    if current_depth >= max_depth:
        return
    try:
        for child in sorted(dir_path.iterdir()):
            if child.is_dir():
                entries.append({"type": "dir", "name": child.name, "path": str(child.relative_to(VAULT_DIR))})
                if max_depth > 1:
                    _list_dir(child, entries, max_depth, current_depth + 1)
            elif child.suffix == ".md":
                entries.append({
                    "type": "file",
                    "name": child.name,
                    "path": str(child.relative_to(VAULT_DIR)),
                    "size": child.stat().st_size,
                })
    except PermissionError:
        pass


# ── Handler 映射(供 dynamic_registry) ────────────────────────────
HANDLERS: dict[str, callable] = {
    "vault_search": vault_search_impl,
    "vault_read": vault_read_impl,
    "vault_list": vault_list_impl,
}


# ── CLI 入口(独立测试) ───────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("tool", choices=list(HANDLERS.keys()))
    ap.add_argument("args", nargs="*", help="JSON string or positional args")
    args = ap.parse_args()

    handler = HANDLERS[args.tool]
    if args.args:
        try:
            inp = json.loads(args.args[0])
        except json.JSONDecodeError:
            inp = {"query": " ".join(args.args)} if args.tool == "vault_search" else {"path": " ".join(args.args)}
    else:
        inp = {}

    print(handler(**inp))
