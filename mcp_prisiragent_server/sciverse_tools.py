"""sciverse_tools — 学术搜索 MCP 工具(基于 sciverse 0.9.0 Python SDK)

2026-07-23:用 opendatalab/Sciverse-Agent-Tools 现成 SDK,不再自己探 API。

依赖:
- pip install sciverse (已装 0.9.0)
- SCIVERSE_API_TOKEN env(已有)
- SCIVERSE_BASE_URL(默认 https://api.sciverse.space,可不设)

提供 4 个 MCP 工具(选了最有用的,不全 6 个):
- sciverse_semantic_search:自然语言语义检索
- sciverse_search_papers:结构化关键词搜索
- sciverse_list_paper_relations:取某篇论文引用/参考/相关论文
- sciverse_read_content:取原文字节切片(扩展上下文)
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

# 让 SCIVERSE_BASE_URL 在 env 有时覆盖默认
_BASE_URL_ENV = os.environ.get("SCIVERSE_BASE_URL", "")


def _err(stage: str, exc: Exception) -> str:
    return json.dumps(
        {"ok": False, "error": str(exc), "stage": stage},
        ensure_ascii=False,
    )


def _build_client():
    """构造 sciverse client,支持自定义 base url"""
    from sciverse import AgentToolsClient

    if _BASE_URL_ENV:
        # 如果 env 有自定义 URL,临时改客户端的 base_url
        # (sciverse SDK 0.9.0 的 client 不直接接 base_url,通过 env 变量 SCIVERSE_BASE_URL)
        os.environ["SCIVERSE_BASE_URL"] = _BASE_URL_ENV

    return AgentToolsClient()


# ── MCP 工具 ──
def sciverse_semantic_search_impl(query: str, mode: str = "balanced", limit: int = 5) -> str:
    """自然语言语义搜索论文

    mode: fast / balanced / quality
    """
    try:
        async def _do():
            async with _build_client() as c:
                return await c.semantic_search(query=query, mode=mode)

        r = asyncio.run(_do())
        hits = (r or {}).get("hits", []) or []
        compact = [
            {
                "title": h.get("title"),
                "authors": h.get("authors", [])[:3],
                "year": h.get("year"),
                "score": h.get("score"),
                "unique_id": h.get("unique_id") or h.get("id"),
                "abstract": (h.get("abstract") or "")[:300],
            }
            for h in hits[:limit]
        ]
        return json.dumps(
            {"ok": True, "query": query, "mode": mode, "total": len(hits), "hits": compact},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return _err("semantic_search", e)


def sciverse_search_papers_impl(query: str, limit: int = 10) -> str:
    """结构化搜索论文

    返回论文标题 + unique_id(用来后续读内容 / 取引用)
    """
    try:
        async def _do():
            async with _build_client() as c:
                return await c.search_papers(query=query, limit=limit)

        r = asyncio.run(_do())
        hits = (r or {}).get("hits", []) or (r or {}).get("results", []) or []
        compact = [
            {
                "title": h.get("title"),
                "authors": h.get("authors", [])[:3],
                "year": h.get("year"),
                "unique_id": h.get("unique_id") or h.get("id"),
                "abstract": (h.get("abstract") or "")[:300],
            }
            for h in hits[:limit]
        ]
        return json.dumps(
            {"ok": True, "query": query, "total": len(hits), "hits": compact},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return _err("search_papers", e)


def sciverse_list_paper_relations_impl(unique_id: str) -> str:
    """取某篇论文的引用 / 参考 / 相关工作

    unique_id:从 search_papers / semantic_search 结果里拿
    """
    try:
        async def _do():
            async with _build_client() as c:
                return await c.list_paper_relations(unique_id=unique_id)

        r = asyncio.run(_do())
        return json.dumps(r or {}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("list_paper_relations", e)


def sciverse_read_content_impl(unique_id: str, section: str = "") -> str:
    """读某篇论文的原文片段(section 可选)

    用途:扩展 RAG 上下文,把论文关键片段塞给 agent
    """
    try:
        async def _do():
            async with _build_client() as c:
                if section:
                    return await c.read_content(unique_id=unique_id, section=section)
                return await c.read_content(unique_id=unique_id)

        r = asyncio.run(_do())
        # r 可能很大,截断到 4KB
        text = json.dumps(r or {}, ensure_ascii=False)
        return text[:4000]
    except Exception as e:
        return _err("read_content", e)


# ── Dynamic Registry Exports ─────────────────────────
TOOL_DEFS = [
    {
        "name": "sciverse_semantic_search",
        "description": (
            "sciverse 自然语言语义搜索论文。"
            "输入:query (string), mode (fast/balanced/quality,默认 balanced), limit"
            "输出:title / authors / year / score / unique_id / abstract 前 300 字"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["fast", "balanced", "quality"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "sciverse_search_papers",
        "description": "sciverse 结构化搜索论文(query 关键词 + limit)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "sciverse_list_paper_relations",
        "description": "取论文的引用 / 参考 / 相关工作(给 unique_id)",
        "inputSchema": {
            "type": "object",
            "properties": {"unique_id": {"type": "string"}},
            "required": ["unique_id"],
        },
    },
    {
        "name": "sciverse_read_content",
        "description": "读论文原文字节切片(给 unique_id,可选 section)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "unique_id": {"type": "string"},
                "section": {"type": "string"},
            },
            "required": ["unique_id"],
        },
    },
]


HANDLERS = {
    "sciverse_semantic_search": sciverse_semantic_search_impl,
    "sciverse_search_papers": sciverse_search_papers_impl,
    "sciverse_list_paper_relations": sciverse_list_paper_relations_impl,
    "sciverse_read_content": sciverse_read_content_impl,
}


# ============================================================
# Standalone CLI
# ============================================================
def _cli():
    import argparse

    p = argparse.ArgumentParser(description="sciverse_tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_s = sub.add_parser("semantic", help="语义搜索")
    p_s.add_argument("query")
    p_s.add_argument("--mode", default="balanced")
    p_s.add_argument("--limit", type=int, default=5)

    p_q = sub.add_parser("search", help="结构化搜索")
    p_q.add_argument("query")
    p_q.add_argument("--limit", type=int, default=10)

    p_r = sub.add_parser("relations", help="论文引用关系")
    p_r.add_argument("unique_id")

    p_c = sub.add_parser("read", help="读论文内容")
    p_c.add_argument("unique_id")
    p_c.add_argument("--section", default="")

    args = p.parse_args()

    if args.cmd == "semantic":
        print(sciverse_semantic_search_impl(args.query, mode=args.mode, limit=args.limit))
    elif args.cmd == "search":
        print(sciverse_search_papers_impl(args.query, limit=args.limit))
    elif args.cmd == "relations":
        print(sciverse_list_paper_relations_impl(args.unique_id))
    elif args.cmd == "read":
        print(sciverse_read_content_impl(args.unique_id, section=args.section))


if __name__ == "__main__":
    _cli()