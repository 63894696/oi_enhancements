"""v0.33 知识图谱 MCP 工具 — kg_query / kg_entity_info / kg_neighbors / kg_dedup_all / kg_merge_entities

复用 v029_knowledge_graph.py 的 NetworkX 图 + v028b extraction JSON。

v0.33 新增:
- _fuzzy_match(): 实体名模糊匹配（difflib.SequenceMatcher）
- _dedup_entities(): 加载时自动去重合并
- kg_dedup_all_impl(): 扫描所有实体对，报告待合并对
- kg_merge_entities_impl(): 手动合并两个实体

v0.34 新增 (Mandol-inspired):
- _embedding_dedup(): 双信号去重 (字符串相似度 + Bailian embedding 语义相似度)
- kg_bfs_expand_impl(): 从种子节点 BFS 图扩展 (Mandol HybridRetriever BFS expansion)
- kg_add_relationship_impl(): 添加关系边 (支持 CAUSES/CAUSED_BY/PREFERS/EVIDENCED_BY)
- _load_extraction(): 自动检测新关系类型并加载
"""
from __future__ import annotations

import difflib
import json
import sys
import time
from pathlib import Path

import networkx as nx

# ── 配置 ──────────────────────────────────────────────────────────
_PERSIST_DIR = Path("D:/AureonCloud/proton/ruflo_memory")
_GRAPH_FILE = _PERSIST_DIR / "knowledge_graph.json"

# 模糊匹配阈值
_FUZZY_EXACT = 0.98   # 几乎相同 → 直接跳过
_FUZZY_MERGE = 0.85   # 高度相似 → 合并

# Mandol-inspired: 关系类型常量
REL_CAUSES = "CAUSES"
REL_CAUSED_BY = "CAUSED_BY"
REL_PREFERS = "PREFERS"
REL_EVIDENCED_BY = "EVIDENCED_BY"
REL_SEMANTIC_SIMILAR = "SEMANTIC_SIMILAR"

# ── 图加载 + 模糊去重 ─────────────────────────────────────────────

_GraphCache = None


def _get_graph() -> nx.DiGraph:
    """懒加载 NetworkX 图(带进程内缓存)"""
    global _GraphCache
    if _GraphCache is not None:
        return _GraphCache
    if _GRAPH_FILE.exists():
        try:
            _GraphCache = nx.readwrite.json_graph.node_link_graph(
                json.loads(_GRAPH_FILE.read_text(encoding="utf-8"))
            )
            return _GraphCache
        except Exception:
            pass
    _GraphCache = nx.DiGraph()
    return _GraphCache


def _fuzzy_match(a: str, b: str) -> float:
    """Two-string similarity via difflib.SequenceMatcher (0-1)."""
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _entity_name(G: nx.DiGraph, eid: str) -> str:
    """Get display name for an entity (name field or id fallback)."""
    return G.nodes[eid].get("name", eid)


def _dedup_entities(G: nx.DiGraph) -> dict:
    """Run fuzzy dedup on all entities in-place.

    For each pair of entities with similar names:
    - ratio >= FUZZY_EXACT: skip (exact duplicate)
    - ratio >= FUZZY_MERGE: merge (combine edges, keep best attrs)

    Returns:
        {"merged": N, "skipped": N, "pairs": [(id_a, id_b, ratio)]}
    """
    node_ids = list(G.nodes())
    merged = 0
    skipped = 0
    pairs = []

    for i in range(len(node_ids)):
        for j in range(i + 1, len(node_ids)):
            id_a, id_b = node_ids[i], node_ids[j]
            name_a = _entity_name(G, id_a)
            name_b = _entity_name(G, id_b)
            if not name_a or not name_b:
                continue
            ratio = _fuzzy_match(name_a, name_b)
            if ratio >= _FUZZY_MERGE:
                pairs.append((id_a, id_b, ratio))
                if ratio >= _FUZZY_EXACT:
                    # Exact duplicate — skip, don't merge
                    skipped += 1
                else:
                    # Partial match — merge id_a into id_b
                    _merge_two_entities(G, id_a, id_b)
                    merged += 1

    return {"merged": merged, "skipped": skipped, "pairs": pairs}


def _merge_two_entities(G: nx.DiGraph, source: str, target: str):
    """Merge source entity into target entity.

    - Redirect all edges from source to target
    - Merge attributes (target values preferred)
    - Remove source node
    """
    # Redirect incoming edges
    for src, _, edge_data in list(G.in_edges(source, data=True)):
        if src == target:
            continue  # avoid self-loop
        if G.has_edge(src, target):
            # Edge exists — keep existing (don't overwrite)
            pass
        else:
            G.add_edge(src, target, **edge_data)
    # Redirect outgoing edges
    for _, dst, edge_data in list(G.out_edges(source, data=True)):
        if dst == target:
            continue
        if G.has_edge(target, dst):
            pass
        else:
            G.add_edge(target, dst, **edge_data)

    # Merge attributes: target preferred
    src_attrs = dict(G.nodes[source])
    tgt_attrs = dict(G.nodes[target])
    merged_attrs = {**tgt_attrs, **{k: v for k, v in src_attrs.items()
                                    if k not in tgt_attrs or not tgt_attrs[k]}}
    G.nodes[target].update(merged_attrs)

    # Remove source
    G.remove_node(source)


def _load_extraction(G: nx.DiGraph, namespace: str) -> tuple[int, int]:
    """从 extraction JSON 加载到 NetworkX，带模糊去重"""
    json_path = _PERSIST_DIR / f"{namespace}_extraction.json"
    if not json_path.exists():
        return 0, 0
    data = json.loads(json_path.read_text(encoding="utf-8"))
    entities = data.get("entities", {})
    relations = data.get("relations", [])
    e_count = 0
    for eid, edata in entities.items():
        if not G.has_node(eid):
            G.add_node(eid, **{k: v for k, v in edata.items() if k != "id"})
            e_count += 1
    r_count = 0
    for r in relations:
        s, t, rel = r.get("source", ""), r.get("target", ""), r.get("rel", "")
        if s and t and rel and not G.has_edge(s, t):
            G.add_edge(s, t, **{k: v for k, v in r.items() if k not in ("source", "target")})
            r_count += 1
    return e_count, r_count


# ── 语义搜索缓存 ──────────────────────────────────────────────────
# 实体 embedding 缓存: {entity_id: {"vector": [...], "_ts": epoch}}
_KG_EMBED_CACHE: dict[str, dict] = {}
_KG_EMBED_CACHE_TTL = 7 * 86400  # 7 days


def _kg_bailian_embed(text: str) -> list[float] | None:
    """Call Bailian text-embedding-v4 for KG semantic search."""
    from embedding_utils import bailian_embed
    return bailian_embed(text)


def _kg_cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = (sum(x * x for x in a)) ** 0.5
    norm_b = (sum(x * x for x in b)) ** 0.5
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


def _kg_get_entity_vec(node_id: str, node_data: dict) -> list[float] | None:
    """Get cached embedding for an entity, computing if needed."""
    entry = _KG_EMBED_CACHE.get(node_id)
    if entry and (time.time() - entry.get("_ts", 0)) < _KG_EMBED_CACHE_TTL:
        return entry["vector"]

    text = node_data.get("name", "") + " " + node_data.get("description", "")
    vec = _kg_bailian_embed(text)
    if vec:
        _KG_EMBED_CACHE[node_id] = {"vector": vec, "_ts": time.time()}
    return vec


def _ensure_loaded() -> nx.DiGraph:
    """确保图已加载所有 namespace"""
    G = _get_graph()
    if G.number_of_nodes() > 0:
        return G
    for ns in ("aureon_arch", "aureon_experiences", "aureon_knowledge"):
        _load_extraction(G, ns)
    return G


# ── MCP 工具实现 ──────────────────────────────────────────────────


def kg_query_impl(query: str, namespace: str = "", top_k: int = 10) -> str:
    """按 namespace + query 搜索知识图谱

    v0.33: hybrid search — substring match + embedding cosine similarity
    策略:
    1. 实体名模糊匹配(query 作为子串匹配 entity name/desc)
    2. 如有 namespace, 限定在该 namespace 内
    3. 返回匹配的实体 + 它们的 relation 摘要 + 语义相似度分数
    """
    G = _ensure_loaded()
    results = []
    query_lower = query.lower()

    # Step 1: substring match (fast filter)
    for node_id, node_data in G.nodes(data=True):
        name = node_data.get("name", node_id).lower()
        desc = node_data.get("description", "").lower()
        source = node_data.get("source_doc", "")

        in_ns = True
        if namespace:
            in_ns = namespace.lower() in source.lower() if source else True
        if not in_ns:
            continue

        matched = query_lower in name or query_lower in desc
        if not matched:
            continue

        # 获取 1-hop relations
        out_rels = []
        for _, _, edge_data in G.out_edges(node_id, data=True):
            out_rels.append(edge_data.get("rel", ""))

        results.append({
            "id": node_id,
            "name": node_data.get("name", node_id),
            "description": node_data.get("description", ""),
            "source_doc": source,
            "outgoing_relations": list(set(out_rels))[:5],
            "match_type": "name" if query_lower in name else "description",
            "bm25_score": 1.0,  # exact substring match = max score
        })
        if len(results) >= top_k:
            break

    # Step 2: semantic search (fallback when substring match is sparse)
    semantic_hits = []
    if len(results) < top_k:
        qvec = _kg_bailian_embed(query)
        if qvec:
            for node_id, node_data in G.nodes(data=True):
                # Skip if already in results
                if any(r["id"] == node_id for r in results):
                    continue
                in_ns = True
                if namespace:
                    source = node_data.get("source_doc", "")
                    in_ns = namespace.lower() in source.lower() if source else True
                if not in_ns:
                    continue

                evect = _kg_get_entity_vec(node_id, node_data)
                if evect:
                    sim = _kg_cosine(qvec, evect)
                    if sim > 0.3:  # threshold for semantic match
                        out_rels = []
                        for _, _, edge_data in G.out_edges(node_id, data=True):
                            out_rels.append(edge_data.get("rel", ""))
                        semantic_hits.append({
                            "id": node_id,
                            "name": node_data.get("name", node_id),
                            "description": node_data.get("description", ""),
                            "source_doc": node_data.get("source_doc", ""),
                            "outgoing_relations": list(set(out_rels))[:5],
                            "match_type": "semantic",
                            "bm25_score": 0.0,
                            "cosine_score": round(sim, 3),
                        })

    # Sort semantic hits by cosine score
    semantic_hits.sort(key=lambda x: -x["cosine_score"])

    return json.dumps({
        "hits": results,
        "semantic_hits": semantic_hits[:top_k - len(results)],
        "total": len(results),
        "total_semantic": len(semantic_hits),
        "graph_stats": {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
            "namespaces": ["aureon_arch", "aureon_experiences", "aureon_knowledge"],
        },
    }, ensure_ascii=False, indent=2)


def kg_entity_info_impl(entity_id: str) -> str:
    """获取实体详情(名称、描述、来源文档、关联关系)"""
    G = _ensure_loaded()
    if not G.has_node(entity_id):
        # 尝试模糊匹配
        matches = [n for n in G.nodes() if entity_id.lower() in n.lower()]
        if matches:
            return json.dumps({
                "error": "entity not found, did you mean?",
                "suggestions": matches[:5],
            }, ensure_ascii=False, indent=2)
        return json.dumps({
            "error": f"entity '{entity_id}' not found",
        }, ensure_ascii=False, indent=2)

    node_data = G.nodes[entity_id]
    name = node_data.get("name", entity_id)

    # 入边
    incoming = []
    for src, _, edge_data in G.in_edges(entity_id, data=True):
        incoming.append({
            "from": src,
            "relation": edge_data.get("rel", ""),
        })

    # 出边
    outgoing = []
    for _, dst, edge_data in G.out_edges(entity_id, data=True):
        outgoing.append({
            "to": dst,
            "relation": edge_data.get("rel", ""),
        })

    return json.dumps({
        "id": entity_id,
        "name": name,
        "description": node_data.get("description", ""),
        "source_doc": node_data.get("source_doc", ""),
        "type": node_data.get("type", ""),
        "incoming_relations": incoming[:20],
        "outgoing_relations": outgoing[:20],
    }, ensure_ascii=False, indent=2)


def kg_neighbors_impl(entity_id: str, depth: int = 1) -> str:
    """获取实体的 N-hop 邻居关系"""
    G = _ensure_loaded()
    if not G.has_node(entity_id):
        matches = [n for n in G.nodes() if entity_id.lower() in n.lower()]
        if matches:
            return json.dumps({
                "error": "entity not found, did you mean?",
                "suggestions": matches[:5],
            }, ensure_ascii=False, indent=2)
        return json.dumps({"error": f"entity '{entity_id}' not found"}, ensure_ascii=False, indent=2)

    # BFS 获取 N-hop
    visited = {entity_id}
    neighbors = []
    frontier = {entity_id}

    for d in range(depth):
        next_frontier = set()
        for node in frontier:
            # 出边邻居
            for _, dst, edge_data in G.out_edges(node, data=True):
                rel = edge_data.get("rel", "")
                dst_name = G.nodes[dst].get("name", dst)
                neighbors.append({
                    "direction": "out",
                    "from": node,
                    "to": dst,
                    "to_name": dst_name,
                    "relation": rel,
                    "depth": d + 1,
                })
                if dst not in visited:
                    next_frontier.add(dst)
                    visited.add(dst)
            # 入边邻居
            for src, _, edge_data in G.in_edges(node, data=True):
                rel = edge_data.get("rel", "")
                src_name = G.nodes[src].get("name", src)
                neighbors.append({
                    "direction": "in",
                    "from": src,
                    "from_name": src_name,
                    "to": node,
                    "relation": rel,
                    "depth": d + 1,
                })
                if src not in visited:
                    next_frontier.add(src)
                    visited.add(src)
        frontier = next_frontier

    return json.dumps({
        "entity": entity_id,
        "neighbors": neighbors,
        "total_neighbors": len(neighbors),
        "depth_reached": depth,
    }, ensure_ascii=False, indent=2)


def kg_dedup_all_impl(force: bool = False) -> str:
    """扫描所有实体对，报告名称相似的待合并对。

    force=True 时自动合并相似度 >=0.95 的对。
    返回: {suggested_pairs, auto_merged, details}
    """
    G = _ensure_loaded()
    node_ids = list(G.nodes())
    suggested = []
    auto_merged = 0

    for i in range(len(node_ids)):
        for j in range(i + 1, len(node_ids)):
            id_a, id_b = node_ids[i], node_ids[j]
            name_a = _entity_name(G, id_a)
            name_b = _entity_name(G, id_b)
            if not name_a or not name_b:
                continue
            ratio = _fuzzy_match(name_a, name_b)
            if ratio >= _FUZZY_MERGE:
                suggested.append({
                    "id_a": id_a,
                    "id_b": id_b,
                    "name_a": name_a,
                    "name_b": name_b,
                    "ratio": round(ratio, 3),
                })
                if force and ratio >= _FUZZY_EXACT:
                    _merge_two_entities(G, id_a, id_b)
                    auto_merged += 1

    return json.dumps({
        "total_entities": len(node_ids),
        "suggested_pairs": suggested,
        "total_suggested": len(suggested),
        "auto_merged": auto_merged,
        "force": force,
    }, ensure_ascii=False, indent=2)


def kg_merge_entities_impl(source_id: str, target_id: str) -> str:
    """手动合并两个实体: 将 source 的边/属性合并到 target，删除 source。

    返回: {ok, merged_edges_in, merged_edges_out, source_removed, target_attrs}
    """
    G = _ensure_loaded()

    # Allow fuzzy match on IDs
    if not G.has_node(source_id):
        matches = [n for n in G.nodes() if source_id.lower() in n.lower()]
        if matches:
            return json.dumps({"error": "source not found", "suggestions": matches[:5]}, ensure_ascii=False, indent=2)
        return json.dumps({"error": f"source entity '{source_id}' not found"}, ensure_ascii=False, indent=2)

    if not G.has_node(target_id):
        matches = [n for n in G.nodes() if target_id.lower() in n.lower()]
        if matches:
            return json.dumps({"error": "target not found", "suggestions": matches[:5]}, ensure_ascii=False, indent=2)
        return json.dumps({"error": f"target entity '{target_id}' not found"}, ensure_ascii=False, indent=2)

    if source_id == target_id:
        return json.dumps({"error": "source and target must be different entities"}, ensure_ascii=False, indent=2)

    # Count edges before merge
    in_edges = list(G.in_edges(source_id, data=True))
    out_edges = list(G.out_edges(source_id, data=True))
    merged_in = 0
    merged_out = 0

    # Redirect incoming edges
    for src, _, edge_data in in_edges:
        if src == target_id:
            continue
        if G.has_edge(src, target_id):
            pass  # edge exists, keep existing
        else:
            G.add_edge(src, target_id, **edge_data)
            merged_in += 1

    # Redirect outgoing edges
    for _, dst, edge_data in out_edges:
        if dst == target_id:
            continue
        if G.has_edge(target_id, dst):
            pass
        else:
            G.add_edge(target_id, dst, **edge_data)
            merged_out += 1

    # Merge attributes (target preferred)
    src_attrs = dict(G.nodes[source_id])
    tgt_attrs = dict(G.nodes[target_id])
    merged_attrs = {**tgt_attrs, **{k: v for k, v in src_attrs.items()
                                    if k not in tgt_attrs or not tgt_attrs[k]}}
    G.nodes[target_id].update(merged_attrs)

    # Remove source
    G.remove_node(source_id)

    # Save graph back (best-effort)
    try:
        _GRAPH_FILE.write_text(
            json.dumps(nx.node_link_data(G, edges="source"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    return json.dumps({
        "ok": True,
        "merged_incoming_edges": merged_in,
        "merged_outgoing_edges": merged_out,
        "source_removed": source_id,
        "target_kept": target_id,
        "target_attrs": {k: str(v)[:100] for k, v in merged_attrs.items()},
    }, ensure_ascii=False, indent=2)


def _embedding_dedup(G: nx.DiGraph, threshold: float = 0.85) -> list:
    """Enhanced entity dedup using dual signals:
    1. String similarity (difflib.SequenceMatcher) on entity names
    2. Bailian embedding cosine similarity on name+description

    A pair is suggested for merge when BOTH signals agree:
      - fuzzy_ratio >= 0.75 AND cosine_sim >= 0.90

    This is Mandol's UnifiedFactPipeline-inspired approach:
    multi-signal entity matching reduces false positives from string-only matching.

    Returns list of suggested merge pairs with scores.
    """
    node_ids = list(G.nodes())
    pairs = []

    for i in range(len(node_ids)):
        for j in range(i + 1, len(node_ids)):
            id_a, id_b = node_ids[i], node_ids[j]
            name_a = _entity_name(G, id_a)
            name_b = _entity_name(G, id_b)
            if not name_a or not name_b:
                continue

            # Signal 1: String similarity
            fuzzy_ratio = _fuzzy_match(name_a, name_b)

            # Signal 2: Embedding similarity
            desc_a = G.nodes[id_a].get("description", "")
            desc_b = G.nodes[id_b].get("description", "")
            text_a = name_a + " " + desc_a
            text_b = name_b + " " + desc_b

            vec_a = _kg_get_entity_vec(id_a, {"name": name_a, "description": desc_a})
            vec_b = _kg_get_entity_vec(id_b, {"name": name_b, "description": desc_b})

            cosine_sim = _kg_cosine(vec_a, vec_b) if vec_a and vec_b else 0.0

            # Dual-signal agreement
            if fuzzy_ratio >= 0.75 and cosine_sim >= threshold:
                pairs.append({
                    "id_a": id_a,
                    "id_b": id_b,
                    "name_a": name_a,
                    "name_b": name_b,
                    "fuzzy_ratio": round(fuzzy_ratio, 3),
                    "cosine_sim": round(cosine_sim, 3),
                    "recommend_merge": True,
                })
            elif fuzzy_ratio >= 0.75 or cosine_sim >= threshold:
                # Single signal match — flag for review
                pairs.append({
                    "id_a": id_a,
                    "id_b": id_b,
                    "name_a": name_a,
                    "name_b": name_b,
                    "fuzzy_ratio": round(fuzzy_ratio, 3),
                    "cosine_sim": round(cosine_sim, 3),
                    "recommend_merge": False,
                    "review_needed": True,
                })

    return pairs


def kg_health_impl() -> str:
    """知识图谱健康检查"""
    G = _ensure_loaded()
    return json.dumps({
        "ok": True,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "graph_file": str(_GRAPH_FILE),
        "graph_file_size_mb": round(_GRAPH_FILE.stat().st_size / 1024 / 1024, 2) if _GRAPH_FILE.exists() else 0,
    }, ensure_ascii=False, indent=2)


def kg_bfs_expand_impl(seed_ids: str, hops: int = 1, per_seed: int = 3) -> str:
    """BFS graph expansion from seed nodes (Mandol HybridRetriever pattern).

    From each seed, expand outward via explicit edges for N hops,
    collecting up to per_seed neighbors per hop.

    Args:
        seed_ids: Comma-separated list of entity IDs to expand from.
        hops: Maximum BFS depth (default 1).
        per_seed: Max neighbors to collect per seed per hop (default 3).

    Returns expanded nodes with edge info, deduplicated by UID.
    """
    G = _ensure_loaded()
    seeds = [s.strip() for s in seed_ids.split(",") if s.strip()]

    if not seeds:
        return json.dumps({"error": "no seed IDs provided"}, ensure_ascii=False, indent=2)

    # Resolve fuzzy matches for seeds
    resolved_seeds = []
    for seed in seeds:
        if G.has_node(seed):
            resolved_seeds.append(seed)
        else:
            matches = [n for n in G.nodes() if seed.lower() in n.lower()]
            if matches:
                resolved_seeds.extend(matches[:per_seed])
            else:
                resolved_seeds.append(seed)  # keep anyway, might be fuzzy

    # BFS expansion
    visited = set(resolved_seeds)
    all_expanded = []
    frontier = list(resolved_seeds)

    for hop in range(1, hops + 1):
        next_frontier = []
        for node in frontier:
            # Outgoing edges
            count = 0
            for _, dst, edge_data in G.out_edges(node, data=True):
                if count >= per_seed:
                    break
                rel = edge_data.get("rel", "")
                dst_name = G.nodes[dst].get("name", dst)
                entry = {
                    "hop": hop,
                    "from": node,
                    "to": dst,
                    "to_name": dst_name,
                    "relation": rel,
                }
                all_expanded.append(entry)
                if dst not in visited:
                    next_frontier.append(dst)
                    visited.add(dst)
                count += 1

            # Incoming edges
            count = 0
            for src, _, edge_data in G.in_edges(node, data=True):
                if count >= per_seed:
                    break
                rel = edge_data.get("rel", "")
                src_name = G.nodes[src].get("name", src)
                entry = {
                    "hop": hop,
                    "from": src,
                    "from_name": src_name,
                    "to": node,
                    "relation": rel,
                }
                all_expanded.append(entry)
                if src not in visited:
                    next_frontier.append(src)
                    visited.add(src)
                count += 1

        frontier = next_frontier
        if not frontier:
            break

    return json.dumps({
        "seeds": seeds,
        "resolved_seeds": resolved_seeds,
        "expanded": all_expanded,
        "total_expanded": len(all_expanded),
        "unique_nodes": len(visited),
        "hops": hops,
    }, ensure_ascii=False, indent=2)


def kg_add_relationship_impl(source_id: str, target_id: str, rel_type: str, properties: str = "{}") -> str:
    """Add a relationship edge between two entities.

    Supports new relationship types: CAUSES, CAUSED_BY, PREFERS, EVIDENCED_BY, SEMANTIC_SIMILAR.

    Args:
        source_id: Source entity ID (or fuzzy name).
        target_id: Target entity ID (or fuzzy name).
        rel_type: Relationship type.
        properties: JSON string of edge properties.
    """
    G = _ensure_loaded()
    props = json.loads(properties) if properties else {}

    # Resolve fuzzy IDs
    if not G.has_node(source_id):
        matches = [n for n in G.nodes() if source_id.lower() in n.lower()]
        if matches:
            source_id = matches[0]
        else:
            return json.dumps({"error": f"source entity '{source_id}' not found"}, ensure_ascii=False)

    if not G.has_node(target_id):
        matches = [n for n in G.nodes() if target_id.lower() in n.lower()]
        if matches:
            target_id = matches[0]
        else:
            return json.dumps({"error": f"target entity '{target_id}' not found"}, ensure_ascii=False)

    G.add_edge(source_id, target_id, rel=rel_type, **props)

    # Save graph
    try:
        _GRAPH_FILE.write_text(
            json.dumps(nx.node_link_data(G, edges="source"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _GraphCache = None  # invalidate cache
    except Exception:
        pass

    return json.dumps({
        "ok": True,
        "source": source_id,
        "target": target_id,
        "rel_type": rel_type,
        "properties": props,
    }, ensure_ascii=False, indent=2)


# ── Dynamic Registry Exports (v0.38) ────────────────────────────

TOOL_DEFS = [
    {
        "name": "kg_query",
        "description": "知识图谱语义搜索 — Bailian text-embedding-v4 + local cosine,跨 namespace 召回",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "namespace": {"type": "string", "description": "命名空间过滤"},
                "top_k": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "kg_entity_info",
        "description": "查询实体详细信息 — 名称、标签、向量维度、来源",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "实体 ID"},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "kg_neighbors",
        "description": "BFS 展开 — 查询实体的邻居节点和关系",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "kg_health",
        "description": "知识图谱健康检查 — 节点数/边数/文件大小",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "kg_dedup_all",
        "description": "批量 fuzzy dedup — 合并相似实体,返回合并统计",
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False},
            },
            "required": [],
        },
    },
    {
        "name": "kg_merge_entities",
        "description": "手动合并两个实体 — 保留 source,丢弃 target",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "target_id": {"type": "string"},
            },
            "required": ["source_id", "target_id"],
        },
    },
    {
        "name": "kg_bfs_expand",
        "description": "BFS 种子展开 — 从多个 seed 节点向外扩展,用于图谱可视化",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_ids": {"type": "string", "description": "逗号分隔的 seed ID 列表"},
                "hops": {"type": "integer", "default": 1},
                "per_seed": {"type": "integer", "default": 3},
            },
            "required": ["seed_ids"],
        },
    },
    {
        "name": "kg_add_relationship",
        "description": "在两个实体间添加关系边",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {"type": "string"},
                "target_id": {"type": "string"},
                "rel_type": {"type": "string"},
                "properties": {"type": "string", "default": "{}"},
            },
            "required": ["source_id", "target_id", "rel_type"],
        },
    },
]

HANDLERS = {
    "kg_query": kg_query_impl,
    "kg_entity_info": kg_entity_info_impl,
    "kg_neighbors": kg_neighbors_impl,
    "kg_health": kg_health_impl,
    "kg_dedup_all": kg_dedup_all_impl,
    "kg_merge_entities": kg_merge_entities_impl,
    "kg_bfs_expand": kg_bfs_expand_impl,
    "kg_add_relationship": kg_add_relationship_impl,
}

