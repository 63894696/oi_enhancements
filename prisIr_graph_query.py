"""prisIr_graph_query.py — 图谱查询层(traverse / search / 边查询)

设计:
- traverse 用 SQL 反复自连接,不用 NetworkX(零依赖)
- 支持 direction: out / in / both
- N-hop BFS,每跳按 per_hop 截断
- 入参 root 可以是 node_id 或 title(模糊回退到 search)

可独立测试:
    python prisIr_graph_query.py     # 跑内置 smoke test
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import prisIr_graph_store as store


# ── 内部辅助 ──────────────────────────────────────────────────────
def _resolve_root(root: str, db_path: Path = store.DB_PATH) -> list[str]:
    """把 root 解析成 node_id 列表。优先级:id 命中 > title 精确 > title 模糊。"""
    # 1) 先当 id 查
    node = store.get_node_by_id(root, db_path)
    if node:
        return [node["id"]]
    # 2) title 精确
    ids = store.resolve_title(root, db_path)
    if ids:
        return ids
    # 3) title 模糊(LIKE)
    like = f"%{root}%"
    with store.conn_ctx(db_path) as c:
        rows = c.execute(
            "SELECT id FROM nodes WHERE title LIKE ? ORDER BY title LIMIT 10",
            (like,),
        ).fetchall()
    return [r["id"] for r in rows]


def _edges_out(src_id: str, db_path: Path = store.DB_PATH) -> list[dict]:
    with store.conn_ctx(db_path) as c:
        rows = c.execute(
            """SELECT e.dst_title, e.dst_id, e.kind, e.line_no, e.section,
                      n.title AS dst_title_full, n.path AS dst_path
               FROM edges e
               LEFT JOIN nodes n ON e.dst_id = n.id
               WHERE e.src_id = ?
               ORDER BY e.line_no""",
            (src_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _edges_in(dst_id: str, db_path: Path = store.DB_PATH) -> list[dict]:
    """入边:谁链接了 dst_id。"""
    with store.conn_ctx(db_path) as c:
        rows = c.execute(
            """SELECT e.src_id, e.kind, e.line_no, e.section,
                      n.title AS src_title, n.path AS src_path
               FROM edges e
               JOIN nodes n ON e.src_id = n.id
               WHERE e.dst_id = ?
               ORDER BY e.line_no""",
            (dst_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 公开 API ──────────────────────────────────────────────────────
def traverse(
    root: str,
    hops: int = 2,
    per_hop: int = 5,
    direction: str = "both",
    limit: int = 20,
    db_path: Path = store.DB_PATH,
) -> dict:
    """BFS 遍历图谱,返回 {seeds, expanded}。

    - root: 起点标题或 node_id(模糊回退)
    - hops: 1..4(>4 拒入,防爆)
    - per_hop: 每跳最多返回的邻居数
    - direction: "out" / "in" / "both"
    - limit: 总返回上限
    """
    if hops < 1 or hops > 4:
        return {"ok": False, "error": "hops must be in 1..4"}
    if direction not in ("out", "in", "both"):
        return {"ok": False, "error": "direction must be out/in/both"}

    seed_ids = _resolve_root(root, db_path)
    if not seed_ids:
        return {"ok": False, "error": f"root '{root}' not found", "suggestions": []}

    # 收集每跳的展开
    expanded: list[dict] = []
    visited_ids = set(seed_ids)
    frontier_ids = list(seed_ids)
    seed_titles = {
        sid: store.get_node_by_id(sid, db_path)["title"] for sid in seed_ids
    }

    for hop in range(1, hops + 1):
        next_frontier: list[str] = []
        per_hop_count = 0
        for nid in frontier_ids:
            if per_hop_count >= per_hop:
                break
            from_node = store.get_node_by_id(nid, db_path)
            if not from_node:
                continue
            per_hop_count += 1
            # 出向
            if direction in ("out", "both"):
                for e in _edges_out(nid, db_path):
                    entry = {
                        "hop": hop,
                        "direction": "out",
                        "from_id": nid,
                        "from_title": from_node["title"],
                        "from_path": from_node["path"],
                        "dst_title": e["dst_title"],
                        "dst_id": e["dst_id"],
                        "dst_path": e["dst_path"],
                        "kind": e["kind"],
                        "line_no": e["line_no"],
                        "section": e["section"],
                        "resolved": e["dst_id"] is not None,
                    }
                    expanded.append(entry)
                    if e["dst_id"] and e["dst_id"] not in visited_ids:
                        next_frontier.append(e["dst_id"])
                        visited_ids.add(e["dst_id"])
            # 入向
            if direction in ("in", "both"):
                for e in _edges_in(nid, db_path):
                    entry = {
                        "hop": hop,
                        "direction": "in",
                        "from_id": e["src_id"],
                        "from_title": e["src_title"],
                        "from_path": e["src_path"],
                        "dst_id": nid,
                        "dst_title": from_node["title"],
                        "dst_path": from_node["path"],
                        "kind": e["kind"],
                        "line_no": e["line_no"],
                        "section": e["section"],
                        "resolved": True,
                    }
                    expanded.append(entry)
                    if e["src_id"] not in visited_ids:
                        next_frontier.append(e["src_id"])
                        visited_ids.add(e["src_id"])
            if len(expanded) >= limit:
                break
        frontier_ids = next_frontier
        if not frontier_ids or len(expanded) >= limit:
            break

    return {
        "ok": True,
        "root": root,
        "seeds": [{"id": sid, "title": seed_titles[sid]} for sid in seed_ids],
        "expanded": expanded[:limit],
        "total_expanded": len(expanded[:limit]),
        "unique_nodes_reached": len(visited_ids),
        "hops": hops,
        "per_hop": per_hop,
        "direction": direction,
    }


def search(
    query: str,
    limit: int = 10,
    db_path: Path = store.DB_PATH,
) -> dict:
    """按 title / frontmatter / path 模糊搜索节点。"""
    like = f"%{query}%"
    with store.conn_ctx(db_path) as c:
        rows = c.execute(
            """SELECT id, title, path, frontmatter
               FROM nodes
               WHERE title LIKE ? OR path LIKE ? OR frontmatter LIKE ?
               ORDER BY
                   CASE WHEN title = ? THEN 0
                        WHEN title LIKE ? THEN 1
                        ELSE 2 END,
                   title
               LIMIT ?""",
            (like, like, like, query, f"{query}%", limit),
        ).fetchall()
    return {
        "ok": True,
        "query": query,
        "count": len(rows),
        "results": [
            {
                "id": r["id"],
                "title": r["title"],
                "path": r["path"],
                "frontmatter": json.loads(r["frontmatter"]) if r["frontmatter"] else None,
            }
            for r in rows
        ],
    }


def neighbors(
    node_id: str,
    depth: int = 1,
    db_path: Path = store.DB_PATH,
) -> dict:
    """N 跳邻居(给 kg_neighbors 那种用法),返回出/入边各 depth 跳。"""
    node = store.get_node_by_id(node_id, db_path)
    if not node:
        return {"ok": False, "error": f"node '{node_id}' not found"}

    visited = {node_id}
    all_neighbors: list[dict] = []
    frontier = {node_id}
    for d in range(depth):
        next_f: set[str] = set()
        for nid in frontier:
            for e in _edges_out(nid, db_path):
                all_neighbors.append({
                    "direction": "out",
                    "from": nid,
                    "to": e["dst_id"] or e["dst_title"],
                    "relation": e["kind"],
                    "depth": d + 1,
                })
                if e["dst_id"] and e["dst_id"] not in visited:
                    next_f.add(e["dst_id"])
                    visited.add(e["dst_id"])
            for e in _edges_in(nid, db_path):
                all_neighbors.append({
                    "direction": "in",
                    "from": e["src_id"],
                    "to": nid,
                    "relation": e["kind"],
                    "depth": d + 1,
                })
                if e["src_id"] not in visited:
                    next_f.add(e["src_id"])
                    visited.add(e["src_id"])
        frontier = next_f
    return {
        "ok": True,
        "node": node_id,
        "title": node["title"],
        "depth": depth,
        "total": len(all_neighbors),
        "neighbors": all_neighbors[:100],
    }


# ── Smoke Test ────────────────────────────────────────────────────
def _smoke():
    import tempfile
    import prisIr_graph_build as build

    with tempfile.TemporaryDirectory(prefix="prisIr_graph_q_smoke_") as tmp:
        tmp_vault = Path(tmp) / "vault"
        tmp_vault.mkdir()
        (tmp_vault / "a.md").write_text(
            "# A\nsee [[b]]\nsee [[c]]\n", encoding="utf-8"
        )
        (tmp_vault / "b.md").write_text(
            "# B\nback to [[a]]\nref [[d]]\n", encoding="utf-8"
        )
        (tmp_vault / "c.md").write_text(
            "# C\nlink [[a]]\n", encoding="utf-8"
        )
        (tmp_vault / "d.md").write_text(
            "# D\n", encoding="utf-8"
        )
        tmp_db = Path(tmp) / "q.db"
        build.build_vault(tmp_vault, tmp_db)

        # search
        r = search("a", limit=5, db_path=tmp_db)
        assert r["ok"] and r["count"] >= 1
        print(f"[smoke] search('a') → {r['count']} hits, top: {r['results'][0]['title']}")

        # traverse out 1 hop
        r = traverse("a", hops=1, per_hop=10, direction="out", db_path=tmp_db)
        assert r["ok"]
        print(f"[smoke] traverse(a, out 1hop) → {r['total_expanded']} edges, "
              f"unique={r['unique_nodes_reached']}")
        assert r["total_expanded"] == 2  # a→b, a→c
        assert r["unique_nodes_reached"] == 3  # a, b, c

        # traverse both 2 hop
        r = traverse("a", hops=2, per_hop=10, direction="both", db_path=tmp_db)
        assert r["ok"]
        print(f"[smoke] traverse(a, both 2hop) → {r['total_expanded']} edges, "
              f"unique={r['unique_nodes_reached']}")
        # 1 hop 出向:b,c; 2 hop 出向:b→d; 入向:来自 b,c
        assert r["total_expanded"] >= 4
        assert r["unique_nodes_reached"] == 4  # a,b,c,d

        # 找不到
        r = traverse("nope", hops=1, per_hop=5, db_path=tmp_db)
        assert not r["ok"]
        print(f"[smoke] traverse(nope) → {r.get('error')}")

        # neighbors
        a_node = store.get_node_by_path("a.md", tmp_db)
        r = neighbors(a_node["id"], depth=2, db_path=tmp_db)
        assert r["ok"]
        print(f"[smoke] neighbors(a, 2hop) → total={r['total']}")
        assert r["total"] >= 4

        print("[smoke] PASS")


if __name__ == "__main__":
    _smoke()
    sys.exit(0)