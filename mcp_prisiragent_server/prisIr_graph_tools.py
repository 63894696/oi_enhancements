"""prisIr_graph_tools.py — Prisiragent MCP 工具:Obsidian vault wikilink 上下文图谱

集成位置: mcp_prisiragent_server/server.py 通过 dynamic_registry 自动注册
(本文件不在 v0.* 前缀,不被 dynamic_registry.py 的 skip 规则排除)

工具列表(共 7 个):
- prisIr_graph_traverse:    给定笔记标题/ID,BFS N 跳展开 wikilink 关系
- prisIr_graph_search:      按标题/path/frontmatter 模糊搜节点(给 traverse 找起点)
- prisIr_graph_stats:       图谱健康度 — 节点/边/孤立数/mtime
- prisIr_graph_build:       触发增量建图(Agent 默认不调,运维兜底)
- prisIr_graph_concept_search(B.v2):概念级搜索 — L0 wikilink miss 时按需 LLM 抽 embedding
- prisIr_graph_import_web(C):抓网页 → 落 vault/00-inbox/web/ → 入图
- prisIr_graph_import_wechat(C):抓微信公众号文章 → 落 vault/00-inbox/wechat/ → 入图

借鉴 V7 Go 的「预先建图,traverse 不重新发现」哲学;零 LLM(默认),按需 LLM(B.v2 概念级)。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 让 standalone 跑(不经过 server.py)时也能找到仓库根模块
_OI_ROOT = Path(__file__).resolve().parent.parent
if str(_OI_ROOT) not in sys.path:
    sys.path.insert(0, str(_OI_ROOT))

import prisIr_graph_build as _build
import prisIr_graph_concept as _concept
import prisIr_graph_import as _import
import prisIr_graph_query as _query
import prisIr_graph_store as _store
from pathlib import Path as _Path

# 复用 vault_tools 的 VAULT_DIR(避免两份硬编码)
try:
    from vault_tools import VAULT_DIR as _DEFAULT_VAULT
except Exception:
    _DEFAULT_VAULT = _Path(r"C:\Users\Administrator\Documents\ObsidianVault")


# ── Tool 定义(供 dynamic_registry 自动发现) ──────────────────────
TOOL_DEFS: list[dict] = [
    {
        "name": "prisIr_graph_traverse",
        "description": (
            "Obsidian vault wikilink 上下文图谱遍历 — 给定笔记标题或 ID,"
            "BFS N 跳展开 wikilink 关系,返回邻接实体列表"
            "(title/path/dst_id/direction/hop/line_no/section)。"
            "零 LLM 抽取,本地 SQLite < 100ms。"
            "借鉴 V7 Go「预先建图,traverse 不重新发现」哲学。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "起点笔记标题或 ID(必填)"},
                "hops": {"type": "integer", "default": 2, "description": "BFS 跳数(1-4)"},
                "per_hop": {"type": "integer", "default": 5, "description": "每跳最多返回节点数"},
                "direction": {
                    "type": "string",
                    "enum": ["out", "in", "both"],
                    "default": "both",
                    "description": "出向(out) / 入向(in) / 双向(both)",
                },
                "limit": {"type": "integer", "default": 20, "description": "总返回上限"},
            },
            "required": ["root"],
        },
    },
    {
        "name": "prisIr_graph_search",
        "description": (
            "在 vault 节点上按标题/path/frontmatter 模糊搜索,"
            "返回候选起点(配合 prisIr_graph_traverse 用)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "default": 10, "description": "返回上限"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "prisIr_graph_stats",
        "description": "图谱健康度 — 节点数/边数/孤立链接数/最大出入度/DB 大小/最新 mtime",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "prisIr_graph_build",
        "description": (
            "触发增量建图(扫 vault 解析 wikilink 入库),"
            "返回本次新增/更新/删除/孤立解析数。"
            "Agent 默认不应调用,留作运维兜底。"
            "默认 vault 来自 vault_tools.VAULT_DIR。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault": {
                    "type": "string",
                    "description": f"可选:vault 路径,默认 {_DEFAULT_VAULT}",
                },
                "rebuild": {
                    "type": "boolean",
                    "default": False,
                    "description": "强制全量重建(忽略 mtime 缓存)",
                },
                "debug": {
                    "type": "boolean",
                    "default": False,
                    "description": "返回错误样本",
                },
            },
            "required": [],
        },
    },
    {
        "name": "prisIr_graph_concept_search",
        "description": (
            "概念级 vault 搜索(B.v2 ship)。"
            "L0 wikilink graph 命中 ≥ min_l0_hits(默认 3)时零成本直接返回;"
            "L0 miss 时按需 LLM 抽 embedding 补齐概念级召回 — "
            "本地 bge-small-zh-v1.5 优先(后台预热,失败兜远程),"
            "远程 Bailian text-embedding-v4 兜底(需 BAILIAN_API_KEY)。"
            "跨进程 SQLite 缓存 7 天 TTL,key = (model, query, candidates_mtime_hash)。"
            "返回 layer 字段:L0_wikilink / L0_miss_no_candidates / L1_cache_hit / L1_fresh / L1_no_model。"
            "Agent 应在 wikilink graph 召回不足时调用本工具。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "概念关键词(必填)"},
                "limit": {"type": "integer", "default": 10, "description": "返回上限"},
                "model_preference": {
                    "type": "string",
                    "enum": ["local", "remote", "auto"],
                    "default": "auto",
                    "description": "local=强制本地 bge / remote=强制远程 Bailian / auto=本地优先失败兜远程",
                },
                "candidate_k": {
                    "type": "integer",
                    "default": 20,
                    "description": "候选笔记数,LLM 只对这 K 篇抽 embedding",
                },
                "min_l0_hits": {
                    "type": "integer",
                    "default": 3,
                    "description": "L0 命中数 ≥ 此阈值时直接返回 wikilink 结果",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "prisIr_graph_import_web",
        "description": (
            "抓网页 → 落 vault/00-inbox/web/<slug>.md → 自动入 wikilink 图谱(触发增量 build)。"
            "返回 {source, path, title, body_chars, build_result}。"
            "Agent 在用户希望把网页内容纳入 vault 引用体系时调用本工具。"
            "不做反向链接解析(留口子);不做去重(用户后续手动整理)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "网页 URL(必填,http/https)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "prisIr_graph_import_wechat",
        "description": (
            "抓微信公众号文章(mp.weixin.qq.com)→ 落 vault/00-inbox/wechat/<slug>.md → 入图。"
            "使用手机微信 UA(MicroMessenger/8.0.44)绕过风控(2026-08-14 已验证)。"
            "返回 {source, path, title, author, body_chars, build_result}。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "公众号文章 URL(必填,mp.weixin.qq.com)"},
            },
            "required": ["url"],
        },
    },
]


# ── Handler 实现 ──────────────────────────────────────────────────
def _err(stage: str, exc: Exception) -> str:
    return json.dumps(
        {"ok": False, "stage": stage, "error": str(exc)},
        ensure_ascii=False,
    )


def prisIr_graph_traverse_impl(
    root: str,
    hops: int = 2,
    per_hop: int = 5,
    direction: str = "both",
    limit: int = 20,
) -> str:
    try:
        result = _query.traverse(root, hops=hops, per_hop=per_hop,
                                 direction=direction, limit=limit)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("prisIr_graph_traverse", e)


def prisIr_graph_search_impl(query: str, limit: int = 10) -> str:
    try:
        result = _query.search(query, limit=limit)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("prisIr_graph_search", e)


def prisIr_graph_stats_impl() -> str:
    try:
        result = _store.stats()
        return json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("prisIr_graph_stats", e)


def prisIr_graph_build_impl(
    vault: str | None = None,
    rebuild: bool = False,
    debug: bool = False,
) -> str:
    try:
        vault_path = Path(vault) if vault else _DEFAULT_VAULT
        if not vault_path.exists():
            return json.dumps(
                {"ok": False, "error": f"vault 不存在: {vault_path}"},
                ensure_ascii=False,
            )
        result = _build.build_vault(vault_path, rebuild=rebuild, debug=debug)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("prisIr_graph_build", e)


def prisIr_graph_concept_search_impl(
    query: str,
    limit: int = 10,
    model_preference: str = "auto",
    candidate_k: int = 20,
    min_l0_hits: int = 3,
) -> str:
    try:
        result = _concept.concept_search(
            query=query,
            limit=limit,
            model_preference=model_preference,
            candidate_k=candidate_k,
            min_l0_hits=min_l0_hits,
            vault_dir=_DEFAULT_VAULT,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("prisIr_graph_concept_search", e)


def prisIr_graph_import_web_impl(url: str) -> str:
    """抓网页 → 落 vault/00-inbox/web/ → 入图。"""
    try:
        if not url or not url.startswith(("http://", "https://")):
            return json.dumps(
                {"ok": False, "error": "url 必须是 http/https"},
                ensure_ascii=False,
            )
        result = _import.fetch_web(url, vault_dir=_DEFAULT_VAULT)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("prisIr_graph_import_web", e)


def prisIr_graph_import_wechat_impl(url: str) -> str:
    """抓公众号文章 → 落 vault/00-inbox/wechat/ → 入图。"""
    try:
        if not url or not url.startswith(("http://", "https://")):
            return json.dumps(
                {"ok": False, "error": "url 必须是 http/https"},
                ensure_ascii=False,
            )
        result = _import.fetch_wechat(url, vault_dir=_DEFAULT_VAULT)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return _err("prisIr_graph_import_wechat", e)


# ── Handler 映射(供 dynamic_registry) ───────────────────────────
HANDLERS: dict[str, callable] = {
    "prisIr_graph_traverse": prisIr_graph_traverse_impl,
    "prisIr_graph_search": prisIr_graph_search_impl,
    "prisIr_graph_stats": prisIr_graph_stats_impl,
    "prisIr_graph_build": prisIr_graph_build_impl,
    "prisIr_graph_concept_search": prisIr_graph_concept_search_impl,
    "prisIr_graph_import_web": prisIr_graph_import_web_impl,
    "prisIr_graph_import_wechat": prisIr_graph_import_wechat_impl,
}


# ── CLI 入口(独立调试) ───────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("tool", choices=list(HANDLERS.keys()))
    ap.add_argument("--root", help="traverse 起点")
    ap.add_argument("--query", help="search 关键词")
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--per-hop", type=int, default=5)
    ap.add_argument("--direction", default="both",
                    choices=["out", "in", "both"])
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--vault", help="build vault 路径")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    h = HANDLERS[args.tool]
    if args.tool == "prisIr_graph_traverse":
        print(h(args.root, hops=args.hops, per_hop=args.per_hop,
                direction=args.direction, limit=args.limit))
    elif args.tool == "prisIr_graph_search":
        print(h(args.query, limit=args.limit))
    elif args.tool == "prisIr_graph_build":
        print(h(vault=args.vault, rebuild=args.rebuild, debug=args.debug))
    else:
        print(h())