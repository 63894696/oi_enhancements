"""test_prisir_graph.py — prisIr_graph 单元 + 集成测试

不需要 pytest:python test_prisir_graph.py 即可全跑一遍。
临时 vault 用 tempfile 造,DB 也用临时路径,不污染真实 ~/.oi/prisir_graph.db。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import prisIr_graph_build as build
import prisIr_graph_query as query
import prisIr_graph_store as store


def _make_vault(tmp: Path) -> Path:
    vault = tmp / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "a.md").write_text(
        "---\ntags: [foo, alpha]\n---\n"
        "# A\n## S1\nsee [[b]] and [[c#H1]] and [[ghost]]\n",
        encoding="utf-8",
    )
    (vault / "b.md").write_text(
        "# B\nback to [[a]] and [[d|alias]]\n",
        encoding="utf-8",
    )
    (vault / "c.md").write_text(
        "# C\n## H1\nlink [[a]]\n",
        encoding="utf-8",
    )
    (vault / "d.md").write_text("# D\nno wikilink\n", encoding="utf-8")
    (vault / "nested").mkdir()
    (vault / "nested" / "e.md").write_text(
        "# E\nlink [[a]] and [[non-existent]]\n",
        encoding="utf-8",
    )
    return vault


def test_build_initial(tmp_vault: Path, tmp_db: Path):
    r = build.build_vault(tmp_vault, tmp_db, debug=True)
    assert r["ok"] and r["scanned"] == 5 and r["added"] == 5
    assert r["errors"] == 0
    # orphan 解析后,a→b, a→c 应 resolved;ghost 和 non-existent 仍孤立
    s = store.stats(tmp_db)
    assert s["nodes"] == 5
    assert s["edges"] == 8  # a→b, a→c, a→ghost; b→a, b→d; c→a; (c#H1 与 a→c 同 target 不增)
    assert s["orphaned_edges"] == 2  # ghost + non-existent
    print(f"  [PASS] build_initial: 5 nodes, {s['edges']} edges, {s['orphaned_edges']} orphans")


def test_build_incremental(tmp_vault: Path, tmp_db: Path):
    """增量:文件 mtime 未变,不应重抽。"""
    r = build.build_vault(tmp_vault, tmp_db)
    assert r["added"] == 0 and r["updated"] == 0
    print("  [PASS] build_incremental: 0 added / 0 updated")


def test_build_after_delete(tmp_vault: Path, tmp_db: Path):
    """删一个文件,DB 应同步删除该 node + 其边。"""
    (tmp_vault / "d.md").unlink()
    r = build.build_vault(tmp_vault, tmp_db)
    assert r["removed"] == 1
    s = store.stats(tmp_db)
    assert s["nodes"] == 4
    print(f"  [PASS] build_after_delete: removed=1, nodes={s['nodes']}")


def test_traverse_out_one_hop(tmp_vault: Path, tmp_db: Path):
    r = query.traverse("a", hops=1, per_hop=10, direction="out", db_path=tmp_db)
    assert r["ok"]
    # a 出向:b, c, ghost(b/c 应 resolved,ghost 仍孤立但边还在)
    titles = {e["dst_title"] for e in r["expanded"]}
    assert "b" in titles and "c" in titles
    # 跳 1 出向 unique 应包含 a, b, c
    assert r["unique_nodes_reached"] >= 3
    # 出向不应有入向
    assert all(e["direction"] == "out" for e in r["expanded"])
    # section 应被记录
    assert all(e["section"] for e in r["expanded"])
    print(f"  [PASS] traverse_out_1hop: {r['total_expanded']} edges, "
          f"unique={r['unique_nodes_reached']}")


def test_traverse_two_hops_includes_indirect(tmp_vault: Path, tmp_db: Path):
    r = query.traverse("a", hops=2, per_hop=10, direction="both", db_path=tmp_db)
    assert r["ok"]
    # 2 跳应到达 d(b→d 间接)
    from_ids = {e["from_id"] for e in r["expanded"]}
    a_id = store.get_node_by_path("a.md", tmp_db)["id"]
    b_id = store.get_node_by_path("b.md", tmp_db)["id"]
    d_id = store.get_node_by_path("d.md", tmp_db)["id"]
    assert b_id in from_ids  # b 在 1 跳被访问过,2 跳从 b 出发
    # d 应通过 b→d 出现在 expanded 的 dst 中
    dst_ids = {e["dst_id"] for e in r["expanded"] if e["dst_id"]}
    assert d_id in dst_ids, f"d 未在 2 跳内到达:dst_ids={dst_ids}"
    print(f"  [PASS] traverse_2hops: 跨跳到达 d,b_id={b_id[:8]}, d_id={d_id[:8]}")


def test_search_by_title(tmp_vault: Path, tmp_db: Path):
    r = query.search("a", limit=5, db_path=tmp_db)
    assert r["ok"] and r["count"] >= 1
    top = r["results"][0]
    assert top["title"] == "a"
    print(f"  [PASS] search_title: 命中 {r['count']} 条, top={top['title']}")


def test_search_by_frontmatter(tmp_vault: Path, tmp_db: Path):
    r = query.search("alpha", limit=5, db_path=tmp_db)
    assert r["ok"]
    paths = {hit["path"] for hit in r["results"]}
    assert any("a.md" in p for p in paths)
    print(f"  [PASS] search_frontmatter: alpha 命中 {r['count']} 条")


def test_isolated_link_has_null_dst_id(tmp_vault: Path, tmp_db: Path):
    """[[ghost]] 应 dst_id 为 None 但仍记录在边表里。"""
    r = query.traverse("a", hops=1, per_hop=10, direction="out", db_path=tmp_db)
    ghost_entries = [e for e in r["expanded"] if e["dst_title"] == "ghost"]
    assert ghost_entries and all(e["dst_id"] is None for e in ghost_entries)
    print(f"  [PASS] isolated_link: ghost dst_id is None ({len(ghost_entries)} entries)")


def test_unknown_root_returns_error(tmp_vault: Path, tmp_db: Path):
    r = query.traverse("nope-no-such-note", hops=1, per_hop=5, db_path=tmp_db)
    assert not r["ok"]
    assert "not found" in r.get("error", "")
    print("  [PASS] unknown_root: 正确报错")


def test_fuzzy_title_resolve(tmp_vault: Path, tmp_db: Path):
    """短 root 命中短标题,模糊 fallback。"""
    r = query.traverse("c", hops=1, per_hop=10, direction="both", db_path=tmp_db)
    assert r["ok"] and len(r["seeds"]) == 1
    print(f"  [PASS] fuzzy_resolve: 'c' → {r['seeds'][0]['title']}")


def test_datetime_in_frontmatter(tmp_db: Path):
    """frontmatter 含 datetime.date 字段时 round-trip 不应抛错。"""
    import datetime
    tmp = Path(tempfile.mkdtemp())
    vault = tmp / "vault"
    vault.mkdir()
    # PyYAML 默认会把未引号的 2026-01-01 解析成 datetime.date
    (vault / "dated.md").write_text(
        "---\ndate: 2026-01-01\ntitle: dated\n---\n# Dated\n", encoding="utf-8"
    )
    db = tmp / "dated.db"
    r = build.build_vault(vault, db)
    assert r["errors"] == 0
    node = store.get_node_by_path("dated.md", db)
    assert node and node["frontmatter"]["date"] == "2026-01-01"
    print("  [PASS] datetime_frontmatter: round-trip OK")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="prisIr_graph_test_") as tmp:
        tmp_dir = Path(tmp)
        # 大部分测试用同一组 vault+db;dated 测试单独
        for tag in ("", "_alt"):
            tmp_vault = _make_vault(tmp_dir / ("vault" + tag))
            tmp_db = tmp_dir / ("graph" + tag + ".db")
            print(f"[test] vault={tmp_vault}, db={tmp_db}")
            test_build_initial(tmp_vault, tmp_db)
            test_build_incremental(tmp_vault, tmp_db)
            test_build_after_delete(tmp_vault, tmp_db)
            # 删文件后测试需新建一份(避免污染)
            tmp_vault2 = _make_vault(tmp_dir / ("vault2" + tag))
            tmp_db2 = tmp_dir / ("graph2" + tag + ".db")
            build.build_vault(tmp_vault2, tmp_db2)
            test_traverse_out_one_hop(tmp_vault2, tmp_db2)
            test_traverse_two_hops_includes_indirect(tmp_vault2, tmp_db2)
            test_search_by_title(tmp_vault2, tmp_db2)
            test_search_by_frontmatter(tmp_vault2, tmp_db2)
            test_isolated_link_has_null_dst_id(tmp_vault2, tmp_db2)
            test_unknown_root_returns_error(tmp_vault2, tmp_db2)
            test_fuzzy_title_resolve(tmp_vault2, tmp_db2)
        # datetime 单测
        test_datetime_in_frontmatter(tmp_dir / "dated_tmp.db")

    print("\n=== ALL TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())