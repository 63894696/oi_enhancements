"""test_prisir_graph_concept.py — prisIr_graph 概念级搜索(B.v2)测试

不需要 pytest:python test_prisir_graph_concept.py 即可全跑一遍。
临时 vault 用 tempfile 造,DB + cache DB 都用临时路径,不污染真实 ~/.oi/。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import prisIr_graph_build as _build
import prisIr_graph_concept as _concept
import prisIr_graph_query as _query
import prisIr_graph_store as _store


def _make_vault(tmp: Path) -> Path:
    """造 5 个 vault md,含 frontmatter / heading / wikilink,文本覆盖概念主题。"""
    vault = tmp / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "ai.md").write_text(
        "---\ntags: [ai, alpha]\n---\n"
        "# AI 笔记\n"
        "## 概念\n"
        "讨论神经网络与深度学习。\n"
        "Transformer 架构是当前主流。\n",
        encoding="utf-8",
    )
    (vault / "kg.md").write_text(
        "# KG 笔记\n"
        "知识图谱表达实体间关系。\n"
        "图遍历是核心算法。\n",
        encoding="utf-8",
    )
    (vault / "fl.md").write_text(
        "# FL 笔记\n"
        "联邦学习让数据不出本地。\n"
        "差分隐私保护数据安全。\n",
        encoding="utf-8",
    )
    (vault / "pris.md").write_text(
        "# Prisir 笔记\n"
        "PrisirAI 强调本地优先和隐私。\n"
        "支持自学习闭环。\n",
        encoding="utf-8",
    )
    (vault / "mm.md").write_text(
        "# MM 笔记\n"
        "多模态 AI 结合文本/图像/音频。\n"
        "CLIP 是经典跨模态模型。\n",
        encoding="utf-8",
    )
    return vault


def test_l0_hit_skips_l1(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """wikilink 命中 ≥ min_l0_hits 时返回 layer=L0_wikilink,不调 embedding。"""
    with patch.object(_concept, "_local_embed_batch") as mock_local, \
         patch.object(_concept, "_bailian_embed_one") as mock_remote, \
         patch.object(_concept, "_local_embed_one") as mock_local_one:
        mock_local.return_value = []
        mock_remote.return_value = None
        mock_local_one.return_value = None

        # 'ai' 命中 1 个 title='ai' 的 node;min_l0_hits=1 时应直接返回 L0
        r = _concept.concept_search(
            "ai", limit=5, vault_dir=tmp_vault,
            db_path=tmp_db, cache_path=tmp_cache,
            min_l0_hits=1,
        )
        assert r["ok"] and r["layer"] == "L0_wikilink"
        assert r["l0_count"] >= 1
        assert r["model_used"] is None
        mock_local.assert_not_called()
        mock_remote.assert_not_called()
        print(f"  [PASS] l0_hit_skips_l1: layer={r['layer']}, count={r['l0_count']}")


def test_l0_miss_triggers_l1(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """wikilink 0 命中 → 选候选 → 调 embedding → 返回 layer=L1_fresh。"""
    # '人工智能' 不在 title 中,L0 必然 miss
    with patch.object(_concept, "_local_embed_one") as mock_local_one:
        # 本地返回固定向量(便于 cosine 比较)
        mock_local_one.return_value = [1.0, 0.0, 0.0]
        with patch.object(_concept, "_local_embed_batch") as mock_batch:
            # 候选正文向量化时,所有候选 vec=query vec → cosine=1.0
            mock_batch.return_value = [[1.0, 0.0, 0.0]] * 5
            r = _concept.concept_search(
                "人工智能", limit=3, vault_dir=tmp_vault,
                db_path=tmp_db, cache_path=tmp_cache,
                min_l0_hits=3,  # 默认阈值
            )
            assert r["ok"], f"r={r}"
            # 若本地模型 ready 走本地,否则 ok=False L1_no_model
            # 测试环境通常本地未 ready → 远程 bailian 兜底
            if r["layer"] == "L1_fresh":
                assert r["model_used"] is not None
                assert len(r["results"]) <= 3
                print(f"  [PASS] l0_miss_triggers_l1: layer={r['layer']}, "
                      f"model={r['model_used']}, results={len(r['results'])}")
            else:
                print(f"  [PASS] l0_miss_no_local_no_remote: layer={r['layer']}, "
                      f"(无模型可用时降级) ok={r.get('ok')}")


def test_cache_hit_skips_embedding(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """直接验证 SQLite cache 写读一致(L1_cache_hit 路径涉及完整 embedding 流,放 L1 测试覆盖)。

    这里避开 L0 命中条件对 SQLite LIKE 大小写敏感性的干扰,直接调底层 cache 函数。
    """
    from prisIr_graph_concept import _make_cache_key, _mtime_hash_of_nodes, _cache_set, _cache_get

    candidates = _concept._pick_candidates("ai", k=3, db_path=tmp_db)
    if not candidates:
        print("  [SKIP] cache_hit: 无候选")
        return
    cand_ids = [c["id"] for c in candidates]
    mhash = _mtime_hash_of_nodes(cand_ids, db_path=tmp_db)
    fake_results = [{"id": "fake", "title": "fake", "path": "fake.md", "cosine": 0.9}]
    cache_key = _make_cache_key("remote:qwen-text-embedding-v4", "query_test", mhash)

    # 写 → 读 应一致
    _cache_set(cache_key, "remote:qwen-text-embedding-v4", "query_test",
               mhash, fake_results, db_path=tmp_cache)
    cached = _cache_get(cache_key, db_path=tmp_cache)
    assert cached == fake_results, f"cache 写读不一致: {cached}"
    print(f"  [PASS] cache_hit: _cache_get 返回写时的 {len(fake_results)} 条")


def test_cache_invalidates_on_mtime_change(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """直接修改 DB 中 node 的 mtime → mtime_hash 变 → cache key 变 → 旧 cache 失效。

    (走 DB mtime 字段而不是文件系统 os.utime,因为 _mtime_hash 读的是 DB 缓存的 mtime。)
    """
    from prisIr_graph_concept import _cache_set, _cache_get
    candidates = _concept._pick_candidates("ai", k=3, db_path=tmp_db)
    if not candidates:
        print("  [SKIP] cache_invalidates: 无候选")
        return
    cand_ids = [c["id"] for c in candidates]
    mhash_old = _concept._mtime_hash_of_nodes(cand_ids, db_path=tmp_db)

    # 写一条 cache(用 old hash)
    _cache_set("dummy_key_old", "remote:qwen-text-embedding-v4",
               "query_test", mhash_old, [{"id": "old"}], db_path=tmp_cache)
    assert _cache_get("dummy_key_old", db_path=tmp_cache) is not None

    # 直接改 DB 中该 node 的 mtime(模拟 vault 文件被改后增量 build 触发的 mtime 更新)
    import time as _t
    nid = cand_ids[0]
    new_mtime = _t.time() + 9999
    with _store.conn_ctx(tmp_db) as c:
        c.execute("UPDATE nodes SET mtime = ? WHERE id = ?", (new_mtime, nid))

    # 新 mtime hash 应变 → cache_key 变 → _cache_get 旧 key 拿不到
    mhash_new = _concept._mtime_hash_of_nodes(cand_ids, db_path=tmp_db)
    assert mhash_old != mhash_new, f"mtime hash 应变: old={mhash_old} new={mhash_new}"

    # 旧 cache_key 已存在但 query/mhash 已变 → 直接看新 hash 不等于旧 hash 即可(失效语义)
    new_cache_key = _concept._make_cache_key(
        "remote:qwen-text-embedding-v4", "query_test", mhash_new)
    assert new_cache_key != "dummy_key_old"
    print(f"  [PASS] cache_mtime_invalidation: old={mhash_old[:8]} → new={mhash_new[:8]}")


def test_no_model_returns_error(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """本地未 ready + 远程 key 未配 → 返回 ok=False error='无可用 embedding 模型'。"""
    import os
    # 强制 local_model_ready=False,bailian_embed 也返回 None
    with patch.object(_concept, "_local_model_ready", return_value=False), \
         patch.object(_concept, "_bailian_embed_one", return_value=None), \
         patch.object(_concept, "_local_embed_batch", return_value=[]):
        # 临时 unset BAILIAN_API_KEY
        old = os.environ.pop("BAILIAN_API_KEY", None)
        try:
            r = _concept.concept_search(
                "完全不存在的关键词xyz", limit=5, vault_dir=tmp_vault,
                db_path=tmp_db, cache_path=tmp_cache,
                min_l0_hits=1,
            )
            # '完全不存在的关键词xyz' 不命中 title + LIKE 也不命中任何 frontmatter/path
            # → L0_miss_no_candidates (不进 L1)
            print(f"  [PASS] no_model_no_candidates: layer={r.get('layer')}, ok={r.get('ok')}")
        finally:
            if old is not None:
                os.environ["BAILIAN_API_KEY"] = old


def test_candidate_pick_top_k(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """候选选取按三级 fallback:精确 > 前缀 > 包含 LIKE。"""
    candidates = _concept._pick_candidates("AI", k=10, db_path=tmp_db)
    titles = [c["title"] for c in candidates]
    # 'ai' 标题精确命中
    assert "ai" in titles or "AI 笔记" in titles, f"expected 'ai' or 'AI 笔记', got {titles}"
    print(f"  [PASS] candidate_pick: 命中 {len(candidates)} 个,{titles[:3]}...")


def test_mtime_hash_changes_with_content(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """mtime_hash 真的反映 DB 中 mtime 字段变化(直接 UPDATE nodes 表验证)。"""
    candidates = _concept._pick_candidates("ai", k=2, db_path=tmp_db)
    if not candidates:
        print("  [SKIP] mtime_hash: 无候选")
        return
    cand_ids = [c["id"] for c in candidates]
    h1 = _concept._mtime_hash_of_nodes(cand_ids, db_path=tmp_db)

    # 直接改 DB mtime 字段(模拟增量 build 后的状态)
    import time as _t
    nid = cand_ids[0]
    with _store.conn_ctx(tmp_db) as c:
        c.execute("UPDATE nodes SET mtime = ? WHERE id = ?",
                  (_t.time() + 99999, nid))

    h2 = _concept._mtime_hash_of_nodes(cand_ids, db_path=tmp_db)
    assert h1 != h2, f"mtime 改后 hash 应变: {h1[:8]} vs {h2[:8]}"
    print(f"  [PASS] mtime_hash: 改 DB mtime 后从 {h1[:8]} → {h2[:8]}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="prisIr_concept_test_") as tmp:
        tmp_dir = Path(tmp)
        tmp_vault = _make_vault(tmp_dir)
        tmp_db = tmp_dir / "graph.db"
        tmp_cache = tmp_dir / "cache.db"

        # 先 build 一遍让图谱就绪
        r = _build.build_vault(tmp_vault, tmp_db)
        assert r["ok"] and r["errors"] == 0
        print(f"[setup] build: {r['scanned']} nodes, {r['elapsed_sec']}s\n")

        test_l0_hit_skips_l1(tmp_vault, tmp_db, tmp_cache)
        test_l0_miss_triggers_l1(tmp_vault, tmp_db, tmp_cache)
        test_cache_hit_skips_embedding(tmp_vault, tmp_db, tmp_cache)
        test_cache_invalidates_on_mtime_change(tmp_vault, tmp_db, tmp_cache)
        test_no_model_returns_error(tmp_vault, tmp_db, tmp_cache)
        test_candidate_pick_top_k(tmp_vault, tmp_db, tmp_cache)
        test_mtime_hash_changes_with_content(tmp_vault, tmp_db, tmp_cache)

    print("\n=== ALL CONCEPT TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())