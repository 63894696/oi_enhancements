#!/usr/bin/env python3
"""prisIr_graph.py — Obsidian vault wikilink 上下文图谱 CLI 入口

子命令:
  build      扫 vault 解析 wikilink 入库(增量)
  traverse   BFS N 跳展开 wikilink 关系
  search     按 title/path/frontmatter 模糊搜节点
  stats      图谱健康度

默认 vault 来自 mcp_prisiragent_server/vault_tools.py 的常量;
可用 --vault 覆盖。

可独立运行:
  python prisIr_graph.py build
  python prisIr_graph.py traverse "PrisirAI v2.3.0" --hops 2
  python prisIr_graph.py search "PrisirAI"
  python prisIr_graph.py stats
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 让 CLI 独立跑时也能找到 mcp_prisiragent_server 包里的 vault_tools
_REPO_ROOT = Path(__file__).resolve().parent
_MCP_DIR = _REPO_ROOT / "mcp_prisiragent_server"
for p in (str(_REPO_ROOT), str(_MCP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import prisIr_graph_build as _build
import prisIr_graph_concept as _concept
import prisIr_graph_import as _import
import prisIr_graph_query as _query
import prisIr_graph_store as _store


def _resolve_default_vault() -> Path:
    try:
        from vault_tools import VAULT_DIR as VAULT
        return VAULT
    except Exception:
        return Path(r"C:\Users\Administrator\Documents\ObsidianVault")


def cmd_build(args) -> int:
    vault = Path(args.vault) if args.vault else _resolve_default_vault()
    result = _build.build_vault(vault, rebuild=args.rebuild, debug=args.debug)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def cmd_traverse(args) -> int:
    result = _query.traverse(
        args.root,
        hops=args.hops,
        per_hop=args.per_hop,
        direction=args.direction,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def cmd_search(args) -> int:
    result = _query.search(args.query, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def cmd_stats(args) -> int:
    print(json.dumps({"ok": True, **_store.stats()}, ensure_ascii=False, indent=2))
    return 0


def cmd_neighbors(args) -> int:
    """N 跳邻居(kg_neighbors 风格)。"""
    # 先解析 root → node_id
    ids = _query._resolve_root(args.root)
    if not ids:
        print(json.dumps({"ok": False, "error": f"root '{args.root}' not found"},
                         ensure_ascii=False, indent=2))
        return 1
    if len(ids) > 1:
        print(json.dumps({
            "ok": False,
            "error": f"root '{args.root}' 解析为多个 node_id,请用更精确的标题",
            "candidates": ids[:5],
        }, ensure_ascii=False, indent=2))
        return 1
    result = _query.neighbors(ids[0], depth=args.depth)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_concept(args) -> int:
    """概念级搜索(B.v2):L0 wikilink 优先,L0 miss 时按需 LLM 抽 embedding。"""
    result = _concept.concept_search(
        query=args.query,
        limit=args.limit,
        model_preference=args.model_preference,
        candidate_k=args.candidate_k,
        min_l0_hits=args.min_l0_hits,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def cmd_import(args) -> int:
    """方向 C — 抓网页/微信公众号 → 落 vault/00-inbox/ → 入图。"""
    if args.kind == "web":
        result = _import.fetch_web(args.url)
    elif args.kind == "wechat":
        result = _import.fetch_wechat(args.url)
    else:
        print(json.dumps(
            {"ok": False, "error": f"未知 kind: {args.kind}(应 web/wechat)"},
            ensure_ascii=False, indent=2,
        ))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="prisIr_graph",
        description="Obsidian vault wikilink 上下文图谱(零 LLM,SQLite)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="扫 vault 解析 wikilink 入库")
    p_build.add_argument("--vault", help="vault 路径(默认 ~ Documents/ObsidianVault)")
    p_build.add_argument("--rebuild", action="store_true", help="强制全量重建")
    p_build.add_argument("--debug", action="store_true", help="返回错误样本")
    p_build.set_defaults(func=cmd_build)

    p_trv = sub.add_parser("traverse", help="BFS 展开 wikilink 关系")
    p_trv.add_argument("root", help="起点笔记标题或 ID")
    p_trv.add_argument("--hops", type=int, default=2, help="跳数 1-4(默认 2)")
    p_trv.add_argument("--per-hop", type=int, default=5, help="每跳上限(默认 5)")
    p_trv.add_argument("--direction", choices=["out", "in", "both"], default="both")
    p_trv.add_argument("--limit", type=int, default=20)
    p_trv.set_defaults(func=cmd_traverse)

    p_sch = sub.add_parser("search", help="模糊搜节点")
    p_sch.add_argument("query", help="搜索关键词")
    p_sch.add_argument("--limit", type=int, default=10)
    p_sch.set_defaults(func=cmd_search)

    p_st = sub.add_parser("stats", help="图谱健康度")
    p_st.set_defaults(func=cmd_stats)

    p_nb = sub.add_parser("neighbors", help="N 跳邻居(kg_neighbors 风格)")
    p_nb.add_argument("root", help="节点标题或 ID")
    p_nb.add_argument("--depth", type=int, default=1)
    p_nb.set_defaults(func=cmd_neighbors)

    p_con = sub.add_parser("concept", help="概念级搜索(B.v2) — L0 wikilink miss 时按需 LLM 抽 embedding")
    p_con.add_argument("query", help="概念关键词")
    p_con.add_argument("--limit", type=int, default=10)
    p_con.add_argument("--model-preference",
                       choices=["local", "remote", "auto"], default="auto",
                       help="local=本地 bge / remote=远程 Bailian / auto=本地优先")
    p_con.add_argument("--candidate-k", type=int, default=20,
                       help="候选笔记数(LLM 只对这 K 篇抽 embedding)")
    p_con.add_argument("--min-l0-hits", type=int, default=3,
                       help="L0 wikilink 命中阈值(默认 3)")
    p_con.set_defaults(func=cmd_concept)

    p_imp = sub.add_parser("import", help="方向 C — 抓网页/微信文章 → 落 vault/00-inbox/ → 入图")
    p_imp.add_argument("kind", choices=["web", "wechat"], help="来源类型")
    p_imp.add_argument("url", help="目标 URL")
    p_imp.set_defaults(func=cmd_import)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())