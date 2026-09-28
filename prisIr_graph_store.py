"""prisIr_graph_store.py — Obsidian vault wikilink 图谱的 SQLite 存储层

设计原则:
- 零外部依赖(仅 Python 标准库 sqlite3 + hashlib + json)
- 单文件 DB: ~/.oi/prisir_graph.db
- schema: nodes(笔记节点) + edges(wikilink 出边)
- 提供纯函数 CRUD,IO 留给 build/query 层
- 增量友好:mtime 字段驱动按文件级重抽

可独立测试:
    python prisIr_graph_store.py     # 跑内置 smoke test(建 / 写 / 读)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

# ── 配置 ──────────────────────────────────────────────────────────
OI_HOME = Path(os.environ.get("OI_HOME", Path.home() / ".oi"))
DB_PATH = OI_HOME / "prisIr_graph.db"

# ── Schema ────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id           TEXT PRIMARY KEY,        -- MD5(vault-relative-path)[:16]
    title        TEXT NOT NULL,
    path         TEXT NOT NULL UNIQUE,     -- POSIX 风格,vault-relative
    frontmatter  TEXT,                    -- JSON 序列化(已 fallback 到 dict)
    headings     TEXT,                    -- JSON 数组
    mtime        REAL NOT NULL,
    indexed_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_title ON nodes(title);
CREATE INDEX IF NOT EXISTS idx_nodes_path  ON nodes(path);

CREATE TABLE IF NOT EXISTS edges (
    src_id       TEXT NOT NULL,
    dst_title    TEXT NOT NULL,           -- wikilink 目标标题原文
    dst_id       TEXT,                    -- 命中节点时填;孤立链接 NULL
    kind         TEXT NOT NULL DEFAULT 'wikilink',
    line_no      INTEGER NOT NULL,
    section      TEXT,                    -- 所属 heading,空表示文件根
    FOREIGN KEY (src_id) REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_edges_src  ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst  ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
"""


def _node_id(vault_rel_path: str) -> str:
    """稳定 ID:MD5(vault-relative POSIX path)[:16]。

    用 POSIX 风格确保 Windows/Unix 行为一致。
    """
    norm = vault_rel_path.replace("\\", "/").lstrip("/")
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def _json_default(obj: Any) -> Any:
    """json.dumps default:datetime/date/Decimal/Path/set → str。"""
    iso = getattr(obj, "isoformat", None)
    if callable(iso):
        return iso()
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


# ── 连接管理 ──────────────────────────────────────────────────────
_lock = threading.Lock()


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """新建连接并启用必要 PRAGMA。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def conn_ctx(db_path: Path = DB_PATH):
    """线程安全的连接上下文。事务由调用方控制。"""
    with _lock:
        c = _connect(db_path)
        try:
            yield c
        finally:
            c.close()


def init_schema(db_path: Path = DB_PATH) -> None:
    """初始化 schema(idempotent)。"""
    with conn_ctx(db_path) as c:
        c.executescript(SCHEMA)


# ── Node CRUD ─────────────────────────────────────────────────────
def upsert_node(
    vault_rel_path: str,
    title: str,
    frontmatter: dict | None,
    headings: list[str],
    mtime: float,
    db_path: Path = DB_PATH,
) -> str:
    """插入或更新一个 node,返回 node_id。"""
    nid = _node_id(vault_rel_path)
    posix_path = vault_rel_path.replace("\\", "/").lstrip("/")
    fm_json = (
        json.dumps(frontmatter, ensure_ascii=False, default=_json_default)
        if frontmatter
        else None
    )
    hd_json = json.dumps(headings, ensure_ascii=False)
    now = time.time()
    with conn_ctx(db_path) as c:
        c.execute(
            """INSERT INTO nodes (id, title, path, frontmatter, headings, mtime, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   id = excluded.id,
                   title = excluded.title,
                   frontmatter = excluded.frontmatter,
                   headings = excluded.headings,
                   mtime = excluded.mtime,
                   indexed_at = excluded.indexed_at""",
            (nid, title, posix_path, fm_json, hd_json, mtime, now),
        )
    return nid


def get_node_by_path(vault_rel_path: str, db_path: Path = DB_PATH) -> dict | None:
    posix_path = vault_rel_path.replace("\\", "/").lstrip("/")
    with conn_ctx(db_path) as c:
        row = c.execute("SELECT * FROM nodes WHERE path = ?", (posix_path,)).fetchone()
    return _row_to_node(row) if row else None


def get_node_by_title(title: str, db_path: Path = DB_PATH) -> list[dict]:
    """标题同名时返回多条(允许 vault 重名文件)。"""
    with conn_ctx(db_path) as c:
        rows = c.execute("SELECT * FROM nodes WHERE title = ?", (title,)).fetchall()
    return [_row_to_node(r) for r in rows]


def get_node_by_id(nid: str, db_path: Path = DB_PATH) -> dict | None:
    with conn_ctx(db_path) as c:
        row = c.execute("SELECT * FROM nodes WHERE id = ?", (nid,)).fetchone()
    return _row_to_node(row) if row else None


def resolve_title(title: str, db_path: Path = DB_PATH) -> list[str]:
    """把用户给的标题解析为 node_id 列表(0/1/N 个)。"""
    return [n["id"] for n in get_node_by_title(title, db_path)]


def _row_to_node(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "path": row["path"],
        "frontmatter": json.loads(row["frontmatter"]) if row["frontmatter"] else None,
        "headings": json.loads(row["headings"]) if row["headings"] else [],
        "mtime": row["mtime"],
        "indexed_at": row["indexed_at"],
    }


# ── Edge CRUD ─────────────────────────────────────────────────────
def replace_edges_for_node(
    src_id: str,
    edges: Iterable[dict],
    db_path: Path = DB_PATH,
) -> int:
    """原子替换某 src 节点的全部边(先删后插),返回写入条数。

    edges 项: {dst_title, dst_id?, kind, line_no, section}
    """
    edge_list = list(edges)
    with conn_ctx(db_path) as c:
        c.execute("BEGIN")
        try:
            c.execute("DELETE FROM edges WHERE src_id = ?", (src_id,))
            for e in edge_list:
                c.execute(
                    """INSERT INTO edges (src_id, dst_title, dst_id, kind, line_no, section)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        src_id,
                        e["dst_title"],
                        e.get("dst_id"),
                        e.get("kind", "wikilink"),
                        e["line_no"],
                        e.get("section"),
                    ),
                )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return len(edge_list)


def list_orphaned_titles(db_path: Path = DB_PATH) -> list[str]:
    """返回所有 dst_id IS NULL 的 dst_title 去重列表(孤立链接)。"""
    with conn_ctx(db_path) as c:
        rows = c.execute(
            "SELECT DISTINCT dst_title FROM edges WHERE dst_id IS NULL ORDER BY dst_title"
        ).fetchall()
    return [r["dst_title"] for r in rows]


def resolve_orphans(db_path: Path = DB_PATH) -> int:
    """第二 pass:对孤立链接尝试按 title 命中已有 node,填上 dst_id。

    通常在 build 完一轮所有节点后调用。返回本次填上的边数。

    匹配策略(按优先级):
    1. title 精确 == dst_title
    2. title LIKE 'dst_title%' — Obsidian wikilink 经常省略日期后缀
    3. title LIKE '%dst_title%' — 包含匹配兜底
    """
    with conn_ctx(db_path) as c:
        c.execute("BEGIN")
        try:
            updated = 0
            seen_dst_titles = [
                r["dst_title"]
                for r in c.execute(
                    "SELECT DISTINCT dst_title FROM edges WHERE dst_id IS NULL"
                ).fetchall()
            ]
            for dst_title in seen_dst_titles:
                target_id = None
                # 1) 精确
                hits = c.execute(
                    "SELECT id FROM nodes WHERE title = ?", (dst_title,)
                ).fetchall()
                if hits:
                    target_id = hits[0]["id"]
                else:
                    # 2) 前缀匹配(wikilink 是标题前缀的情况)
                    hits = c.execute(
                        "SELECT id, title FROM nodes WHERE title LIKE ? ORDER BY LENGTH(title) LIMIT 1",
                        (dst_title + "%",),
                    ).fetchall()
                    if hits:
                        target_id = hits[0]["id"]
                    else:
                        # 3) 包含匹配兜底
                        hits = c.execute(
                            "SELECT id, title FROM nodes WHERE title LIKE ? ORDER BY LENGTH(title) LIMIT 1",
                            ("%" + dst_title + "%",),
                        ).fetchall()
                        if hits:
                            target_id = hits[0]["id"]
                if not target_id:
                    continue
                cur = c.execute(
                    "UPDATE edges SET dst_id = ? WHERE dst_title = ? AND dst_id IS NULL",
                    (target_id, dst_title),
                )
                updated += cur.rowcount or 0
            c.execute("COMMIT")
            return updated
        except Exception:
            c.execute("ROLLBACK")
            raise


def delete_node_orphans(vault_rel_paths_to_keep: set[str], db_path: Path = DB_PATH) -> int:
    """vault 全扫完后,DB 里不在 keep 集合的节点 = 文件已删/改名,清掉。

    返回删除的节点数。
    """
    with conn_ctx(db_path) as c:
        c.execute("BEGIN")
        try:
            existing = {
                r["path"]: r["id"]
                for r in c.execute("SELECT path, id FROM nodes").fetchall()
            }
            keep_norm = {p.replace("\\", "/").lstrip("/") for p in vault_rel_paths_to_keep}
            to_remove_ids = [
                nid for path, nid in existing.items() if path not in keep_norm
            ]
            for nid in to_remove_ids:
                c.execute("DELETE FROM edges WHERE src_id = ?", (nid,))
                c.execute("DELETE FROM nodes WHERE id = ?", (nid,))
            c.execute("COMMIT")
            return len(to_remove_ids)
        except Exception:
            c.execute("ROLLBACK")
            raise


# ── 统计 ──────────────────────────────────────────────────────────
def stats(db_path: Path = DB_PATH) -> dict:
    size_mb = round(db_path.stat().st_size / 1024 / 1024, 2) if db_path.exists() else 0
    recent_cutoff = time.time() - 86400
    with conn_ctx(db_path) as c:
        n_nodes = c.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"]
        n_edges = c.execute("SELECT COUNT(*) AS n FROM edges").fetchone()["n"]
        n_orphans = c.execute(
            "SELECT COUNT(*) AS n FROM edges WHERE dst_id IS NULL"
        ).fetchone()["n"]
        out_row = c.execute(
            "SELECT src_id, COUNT(*) AS n FROM edges GROUP BY src_id ORDER BY n DESC LIMIT 1"
        ).fetchone()
        in_row = c.execute(
            "SELECT dst_id, COUNT(*) AS n FROM edges WHERE dst_id IS NOT NULL "
            "GROUP BY dst_id ORDER BY n DESC LIMIT 1"
        ).fetchone()
        avg_out = c.execute(
            "SELECT AVG(n) AS a FROM (SELECT COUNT(*) AS n FROM edges GROUP BY src_id)"
        ).fetchone()["a"]
        latest = c.execute("SELECT MAX(mtime) AS m FROM nodes").fetchone()["m"]
        recent = (
            c.execute(
                "SELECT COUNT(*) AS n FROM nodes WHERE mtime > ?", (recent_cutoff,)
            ).fetchone()["n"]
            if latest
            else 0
        )
    return {
        "db_path": str(db_path),
        "db_size_mb": size_mb,
        "nodes": n_nodes,
        "edges": n_edges,
        "orphaned_edges": n_orphans,
        "max_out_degree": out_row["n"] if out_row else 0,
        "max_out_degree_node": out_row["src_id"] if out_row else None,
        "max_in_degree": in_row["n"] if in_row else 0,
        "max_in_degree_node": in_row["dst_id"] if in_row else None,
        "avg_out_degree": round(avg_out, 2) if avg_out else 0,
        "latest_node_mtime": latest,
        "node_count_mtime_recent": recent,
    }


# ── Smoke Test ────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    tmp_db = Path(os.environ.get("PRISIR_GRAPH_TEST_DB", OI_HOME / "prisIr_graph_smoke.db"))
    if tmp_db.exists():
        tmp_db.unlink()
    print(f"[smoke] db = {tmp_db}")

    init_schema(tmp_db)
    nid_a = upsert_node(
        "notes/a.md", "a",
        frontmatter={"tags": ["foo"]},
        headings=["# A", "## A.1"],
        mtime=100.0,
        db_path=tmp_db,
    )
    nid_b = upsert_node(
        "notes/b.md", "b",
        frontmatter=None,
        headings=["# B"],
        mtime=200.0,
        db_path=tmp_db,
    )
    print(f"[smoke] upserted nid_a={nid_a} nid_b={nid_b}")

    # edges from a → b + a → isolated
    n = replace_edges_for_node(
        nid_a,
        [
            {"dst_title": "b", "line_no": 5, "section": "# A"},
            {"dst_title": "ghost", "line_no": 7, "section": "## A.1"},
        ],
        db_path=tmp_db,
    )
    print(f"[smoke] wrote {n} edges")

    # resolve orphans (b exists, ghost doesn't)
    filled = resolve_orphans(tmp_db)
    print(f"[smoke] orphan resolved: {filled}")

    a_node = get_node_by_path("notes/a.md", tmp_db)
    assert a_node and a_node["title"] == "a"
    print(f"[smoke] get_node_by_path OK: {a_node['path']}")

    titles = resolve_title("a", tmp_db)
    assert titles == [nid_a]
    print(f"[smoke] resolve_title('a') OK")

    s = stats(tmp_db)
    print(f"[smoke] stats: {json.dumps(s, ensure_ascii=False, indent=2)}")
    assert s["nodes"] == 2 and s["edges"] == 2 and s["orphaned_edges"] == 1

    print("[smoke] PASS")
    sys.exit(0)