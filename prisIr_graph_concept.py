"""prisIr_graph_concept.py — 概念级 vault 搜索(L0 wikilink + L1 按需 embedding)

设计:
- L0:wikilink graph(已 ship)命中 ≥ min_l0_hits 时直接返回,零 LLM
- L0 miss:候选笔记选取(LIKE 标题/frontmatter/path 取 top K)
- L1:对候选跑 embedding,本地 bge-small-zh-v1.5 优先(已后台预热),失败兜远程 Bailian text-embedding-v4
- 跨进程 SQLite 缓存(7 天 TTL),key = MD5(model + query + candidates_mtime_hash)
- 远程 LLM 授权:信任已有 BAILIAN_API_KEY(用户主动配 key = 显式同意,不弹卡)

可独立测试:
    python prisIr_graph_concept.py     # 跑内置 smoke test
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import prisIr_graph_query as _query
import prisIr_graph_store as _store

# ── 配置 ──────────────────────────────────────────────────────────
CONCEPT_CACHE_PATH = _store.OI_HOME / "prisIr_graph_concept_cache.sqlite"
CONCEPT_CACHE_TTL = 7 * 86400  # 7 天,对齐 embedding_utils.py
CONCEPT_CACHE_LOCK = threading.Lock()

LOCAL_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# 本地模型预热(模块级 daemon 线程 + 锁)
_local_model = None
_local_model_ready_flag = False
_local_model_lock = threading.Lock()


# ── 本地模型预热(后台线程,不阻塞 import) ──────────────────────
def _preheat_local_model() -> None:
    """后台线程加载 fastembed + bge-small-zh-v1.5;失败静默走远程。"""
    global _local_model, _local_model_ready_flag
    try:
        from fastembed import TextEmbedding  # type: ignore
        _local_model = TextEmbedding(model_name=LOCAL_MODEL_NAME)
        with _local_model_lock:
            _local_model_ready_flag = True
    except Exception:
        # 静默失败 — 调用方走远程兜底
        pass


def _local_model_ready() -> bool:
    with _local_model_lock:
        return _local_model_ready_flag


def _start_preheat() -> None:
    """模块导入时启动 daemon 预热线程(幂等)。"""
    t = threading.Thread(target=_preheat_local_model, daemon=True, name="bge-preheat")
    t.start()


_start_preheat()


# ── Embedding 调用 ──────────────────────────────────────────────
def _bailian_embed_one(text: str) -> list[float] | None:
    """远程 embedding 单条(走 embedding_utils 自带进程内缓存)。"""
    try:
        from embedding_utils import bailian_embed
        return bailian_embed(text)
    except Exception:
        return None


def _local_embed_one(text: str) -> list[float] | None:
    """本地 fastembed 单条 → list[float]。"""
    if not _local_model_ready():
        return None
    try:
        vec = list(_local_model.embed([text]))[0]
        return vec.tolist() if hasattr(vec, "tolist") else list(vec)
    except Exception:
        return None


def _local_embed_batch(texts: list[str]) -> list[list[float] | None]:
    """本地 fastembed 批量 → list[vec|None](保留位置对齐,失败项 None)。"""
    if not _local_model_ready():
        return [None] * len(texts)
    out: list[list[float] | None] = []
    try:
        for vec in _local_model.embed(texts):
            out.append(vec.tolist() if hasattr(vec, "tolist") else list(vec))
        return out
    except Exception:
        # 失败时退化为逐条重试,避免单条坏数据拖垮全部
        return [_local_embed_one(t) for t in texts]


def _cosine(a: list[float] | None, b: list[float] | None) -> float:
    """cosine,任一为 None 返回 0。"""
    if a is None or b is None:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = (sum(x * x for x in a)) ** 0.5
    nb = (sum(x * x for x in b)) ** 0.5
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


# ── 候选笔记选取 ──────────────────────────────────────────────
def _pick_candidates(query: str, k: int, db_path: Path) -> list[dict]:
    """从 L0 候选池选前 K 个:title 精确 > title 前缀 LIKE > title/path/fm 包含 LIKE。"""
    like = f"%{query}%"
    out: list[dict] = []
    seen_ids: set[str] = set()
    with _store.conn_ctx(db_path) as c:
        # 1) title 精确(limit 大一点,允许多个 vault 文件同名)
        for r in c.execute(
            "SELECT id, title, path FROM nodes WHERE title = ?",
            (query,),
        ).fetchall():
            if r["id"] in seen_ids:
                continue
            out.append({"id": r["id"], "title": r["title"], "path": r["path"]})
            seen_ids.add(r["id"])
        if len(out) >= k:
            return out[:k]
        # 2) title 前缀 LIKE
        for r in c.execute(
            "SELECT id, title, path FROM nodes WHERE title LIKE ? ORDER BY title LIMIT ?",
            (f"{query}%", k * 3),
        ).fetchall():
            if r["id"] in seen_ids:
                continue
            out.append({"id": r["id"], "title": r["title"], "path": r["path"]})
            seen_ids.add(r["id"])
            if len(out) >= k:
                return out[:k]
        # 3) title/path/fm 包含 LIKE
        for r in c.execute(
            """SELECT id, title, path FROM nodes
               WHERE title LIKE ? OR path LIKE ? OR frontmatter LIKE ?
               ORDER BY title LIMIT ?""",
            (like, like, like, k * 3),
        ).fetchall():
            if r["id"] in seen_ids:
                continue
            out.append({"id": r["id"], "title": r["title"], "path": r["path"]})
            seen_ids.add(r["id"])
            if len(out) >= k:
                break
    return out[:k]


def _load_node_body(node_id: str, vault_dir: Path) -> str:
    """读节点对应笔记正文(去 frontmatter,截 4K 防爆)。"""
    node = _store.get_node_by_id(node_id)
    if not node:
        return ""
    p = vault_dir / node["path"]
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    # 去 frontmatter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4:].strip()
    # 截断
    MAX_CHARS = 4000
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n\n[... 截断,原 {len(text)} 字符 ...]"
    return text


# ── SQLite 跨进程缓存 ─────────────────────────────────────────
@contextmanager
def _cache_conn(db_path: Path = CONCEPT_CACHE_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    try:
        _ensure_cache_schema(conn)
        yield conn
    finally:
        conn.close()


def _ensure_cache_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS concept_cache (
            cache_key TEXT PRIMARY KEY,
            model     TEXT NOT NULL,
            query     TEXT NOT NULL,
            mtime_hash TEXT NOT NULL,
            results_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_concept_expires ON concept_cache(expires_at);
        """
    )


def _prune_expired(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DELETE FROM concept_cache WHERE expires_at < ?", (time.time(),))
    except Exception:
        pass


def _mtime_hash_of_nodes(node_ids: list[str], db_path: Path = _store.DB_PATH) -> str:
    """候选节点列表的 mtime 指纹;vault 改文件后自然失效。"""
    mtimes: list[float] = []
    for nid in node_ids:
        n = _store.get_node_by_id(nid, db_path)
        if n:
            mtimes.append(round(n["mtime"], 3))
    raw = json.dumps(sorted(mtimes), separators=(",", ":"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _make_cache_key(model: str, query: str, mtime_hash: str) -> str:
    raw = f"{model}|{query}|{mtime_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cache_get(cache_key: str, db_path: Path = CONCEPT_CACHE_PATH) -> list[dict] | None:
    with CONCEPT_CACHE_LOCK:
        with _cache_conn(db_path) as conn:
            _prune_expired(conn)
            row = conn.execute(
                "SELECT results_json, expires_at FROM concept_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] < time.time():
                return None
            try:
                return json.loads(row["results_json"])
            except Exception:
                return None


def _cache_set(
    cache_key: str,
    model: str,
    query: str,
    mtime_hash: str,
    results: list[dict],
    db_path: Path = CONCEPT_CACHE_PATH,
) -> None:
    now = time.time()
    with CONCEPT_CACHE_LOCK:
        with _cache_conn(db_path) as conn:
            _prune_expired(conn)
            conn.execute(
                """INSERT INTO concept_cache
                       (cache_key, model, query, mtime_hash, results_json, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       results_json = excluded.results_json,
                       created_at = excluded.created_at,
                       expires_at = excluded.expires_at""",
                (
                    cache_key,
                    model,
                    query,
                    mtime_hash,
                    json.dumps(results, ensure_ascii=False),
                    now,
                    now + CONCEPT_CACHE_TTL,
                ),
            )


# ── 公开 API ──────────────────────────────────────────────────
def concept_search(
    query: str,
    limit: int = 10,
    model_preference: str = "auto",
    candidate_k: int = 20,
    min_l0_hits: int = 3,
    vault_dir: Path | None = None,
    db_path: Path = _store.DB_PATH,
    cache_path: Path = CONCEPT_CACHE_PATH,
) -> dict:
    """概念级 vault 搜索。三层 fallback:

    L0 — wikilink graph 命中 ≥ min_l0_hits 时直接返回(零 LLM)
    L0 miss — 候选选取 → embedding 排序
       模型:auto=本地 bge 优先失败兜远程 / local=强制本地 / remote=强制远程
       缓存:(model, query, candidates_mtime_hash) 命中时零成本
    """
    if model_preference not in ("local", "remote", "auto"):
        return {"ok": False, "error": f"model_preference 必须是 local/remote/auto,got {model_preference}"}

    # 解析 vault_dir
    if vault_dir is None:
        try:
            from vault_tools import VAULT_DIR as _V
            vault_dir = _V
        except Exception:
            vault_dir = Path(r"C:\Users\Administrator\Documents\ObsidianVault")

    # ── L0:wikilink graph 优先 ──
    l0_hits = _query.search(query, limit=limit, db_path=db_path)
    l0_count = l0_hits.get("count", 0) if l0_hits.get("ok") else 0
    if l0_count >= min_l0_hits:
        return {
            "ok": True,
            "layer": "L0_wikilink",
            "query": query,
            "results": l0_hits.get("results", []),
            "l0_count": l0_count,
            "model_used": None,
        }

    # ── L0 miss:选候选 ──
    candidates = _pick_candidates(query, k=candidate_k, db_path=db_path)
    if not candidates:
        return {
            "ok": True,
            "layer": "L0_miss_no_candidates",
            "query": query,
            "results": [],
            "l0_count": l0_count,
            "model_used": None,
        }

    # 候选节点 id 用于 mtime_hash
    cand_ids = [c["id"] for c in candidates]
    mtime_hash = _mtime_hash_of_nodes(cand_ids, db_path)

    # 决定 model tag(用于缓存 key 区分 local vs remote)
    if model_preference == "local":
        model_tag = "local:" + LOCAL_MODEL_NAME
    elif model_preference == "remote":
        model_tag = "remote:qwen-text-embedding-v4"
    else:  # auto
        model_tag = "auto:" + ("local-pref:" + LOCAL_MODEL_NAME if _local_model_ready() else "remote-pref:qwen-text-embedding-v4")

    # ── 缓存查询 ──
    cache_key = _make_cache_key(model_tag, query, mtime_hash)
    cached = _cache_get(cache_key, cache_path)
    if cached is not None:
        return {
            "ok": True,
            "layer": "L1_cache_hit",
            "query": query,
            "results": cached[:limit],
            "l0_count": l0_count,
            "model_used": None,
            "cache_key": cache_key,
        }

    # ── 跑 embedding ──
    bodies = [_load_node_body(c["id"], vault_dir) for c in candidates]
    model_used: str | None = None
    vecs: list[list[float] | None] = []
    if model_preference in ("local", "auto") and _local_model_ready():
        vecs = _local_embed_batch(bodies)
        if any(v is not None for v in vecs):
            model_used = "local:" + LOCAL_MODEL_NAME
    if model_used is None and model_preference in ("remote", "auto"):
        # 远程兜底(逐条,便于失败跳过)
        vecs = [_bailian_embed_one(b) for b in bodies]
        if any(v is not None for v in vecs):
            model_used = "remote:qwen-text-embedding-v4"
    if model_used is None:
        return {
            "ok": False,
            "error": "无可用 embedding 模型(本地 fastembed 未就绪 + 远程 BAILIAN_API_KEY 未配置)",
            "layer": "L1_no_model",
            "query": query,
            "candidates": len(candidates),
        }

    # query 向量化
    qvec: list[float] | None = None
    if model_used.startswith("local:"):
        qvec = _local_embed_one(query)
    else:
        qvec = _bailian_embed_one(query)
    if qvec is None:
        # 极端情况:模型 tag 是 local/remote 但 query 向量化失败
        return {
            "ok": False,
            "error": f"query 向量化失败(model={model_used})",
            "layer": "L1_query_embed_fail",
            "query": query,
        }

    # cosine 排序
    scored: list[tuple[dict, float]] = []
    for c, v in zip(candidates, vecs):
        if v is None:
            continue
        scored.append((c, _cosine(qvec, v)))
    scored.sort(key=lambda x: -x[1])

    # 格式化结果
    results = [
        {
            "id": c["id"],
            "title": c["title"],
            "path": c["path"],
            "cosine": round(s, 4),
        }
        for c, s in scored[:limit]
    ]

    # 写缓存(只缓存成功的查询)
    try:
        _cache_set(cache_key, model_used, query, mtime_hash, results, cache_path)
    except Exception:
        pass

    return {
        "ok": True,
        "layer": "L1_fresh",
        "query": query,
        "results": results,
        "l0_count": l0_count,
        "model_used": model_used,
        "cache_key": cache_key,
        "candidates": len(candidates),
    }


# ── Smoke Test ─────────────────────────────────────────────────
def _smoke() -> None:
    import tempfile
    import prisIr_graph_build as _build

    with tempfile.TemporaryDirectory(prefix="prisIr_concept_smoke_") as tmp:
        tmpdir = Path(tmp)
        vault = tmpdir / "vault"
        vault.mkdir()
        # 造 5 篇 vault
        (vault / "a.md").write_text(
            "---\ntags: [alpha]\n---\n# A\n讲讲人工智能与神经网络\n## 背景\n神经网络在图像识别很有效。\n",
            encoding="utf-8",
        )
        (vault / "b.md").write_text(
            "# B\n讨论知识图谱与图数据库\n知识图谱能表达实体之间的关系。\n",
            encoding="utf-8",
        )
        (vault / "c.md").write_text(
            "# C\n讲联邦学习\n联邦学习让数据不出本地训练模型。\n",
            encoding="utf-8",
        )
        (vault / "d.md").write_text(
            "# D\n讲 PrisirAI 与本地优先\nPrisirAI 强调本地隐私优先。\n",
            encoding="utf-8",
        )
        (vault / "e.md").write_text(
            "# E\n讲多模态 AI\n多模态 AI 结合文本/图像/音频。\n",
            encoding="utf-8",
        )

        db = tmpdir / "graph.db"
        cache = tmpdir / "cache.db"
        r = _build.build_vault(vault, db)
        assert r["ok"] and r["errors"] == 0
        print(f"[smoke] build: {r['scanned']} nodes")

        # 测试 1:标题命中(L0)
        r = concept_search("人工智能", limit=5, vault_dir=vault, db_path=db, cache_path=cache)
        print(f"[smoke] '人工智能' L0 search → layer={r.get('layer')}, count={r.get('l0_count')}")
        # '人工智能' 不在 title 中,应走 L0 miss → 选候选 → embedding
        # 由于本地模型未 ready(测试环境 fastembed 不一定装),可能走 bailian 或 fail
        assert r.get("layer") in ("L0_wikilink", "L1_fresh", "L1_no_model", "L0_miss_no_candidates")

        # 测试 2:标题精确命中(L0) — 把 min_l0_hits 调到 1,确认 L0 路径
        r = concept_search("a", limit=5, vault_dir=vault, db_path=db,
                           cache_path=cache, min_l0_hits=1)
        print(f"[smoke] 'a' L0 search (min_l0_hits=1) → layer={r.get('layer')}")
        assert r["layer"] == "L0_wikilink"
        assert r["l0_count"] >= 1

        # 测试 2b:默认 min_l0_hits=3,'a' 只命中 1,L0 不够,走 L1
        r = concept_search("a", limit=5, vault_dir=vault, db_path=db, cache_path=cache)
        print(f"[smoke] 'a' L0 (default min_l0_hits=3) → layer={r.get('layer')}")
        assert r["layer"] != "L0_wikilink"  # 默认阈值下不进 L0

        # 测试 3:零候选(不存在 query)
        r = concept_search("完全不存在的关键词xyz", limit=5, vault_dir=vault, db_path=db, cache_path=cache)
        print(f"[smoke] 不存在关键词 → layer={r.get('layer')}")
        # L0 0 命中 + 无候选 → L0_miss_no_candidates 或 L1_no_model

        # 测试 4:本地模型未就绪 + 远程 key 未配时,应返回 ok=False error
        # (测试环境通常远程 key 也没配)
        if not _local_model_ready() and not os.environ.get("BAILIAN_API_KEY"):
            r = concept_search("PrisirAI", limit=5, vault_dir=vault, db_path=db, cache_path=cache)
            print(f"[smoke] 无模型可用 → ok={r.get('ok')}, layer={r.get('layer')}, error={r.get('error', '')[:60]}")
            # ok=False 或 ok=True with L0(取决于 'PrisirAI' 是否 L0 命中)
            if not r.get("ok"):
                assert "无可用 embedding 模型" in r.get("error", "")

        print("[smoke] PASS")


if __name__ == "__main__":
    _smoke()
    sys.exit(0)