#!/usr/bin/env python3
"""high_level_memory.py — Hierarchical Abstract Memory Derivation (Mandol-inspired)

Automatically derives high-level memory abstractions from base KG nodes:
- Episodic: Event chains (CAUSES/CAUSED_BY clusters)
- Semantic: Entity clusters (SEMANTIC_SIMILAR / RELATED_TO groups)
- Emotional: User preference patterns (PREFERS clusters)

Based on Mandol's hierarchical memory model where base memories are
uniformly represented as a structured semantic graph, and the abstract
layer automatically derives episodic, semantic, and emotional memories
with traceable links.

v0.1 (2026-07-17)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add paths
_OI_ENHANCEMENTS = Path("C:/Users/Administrator/oi_enhancements")
sys.path.insert(0, str(_OI_ENHANCEMENTS))

from pathlib import Path

# Import kg_tools utilities
sys.path.insert(0, str(_OI_ENHANCEMENTS / "mcp_prisiragent_server"))
import kg_tools as kg


# ── Config ──────────────────────────────────────────────────────────

_CLUSTER_THRESHOLD = 0.85  # cosine similarity threshold for clustering
_EPISODIC_MIN_SIZE = 3     # minimum cluster size for episodic memory
_SEMANTIC_MIN_SIZE = 2     # minimum cluster size for semantic memory
_OUTPUT_DIR = Path(r"C:/Users/Administrator/oi_enhancements/mcp_prisiragent_server/high_level")


# ── Clustering ─────────────────────────────────────────────────────

def _cluster_nodes(
    G,
    embedding_fn,
    threshold: float = _CLUSTER_THRESHOLD,
    min_size: int = 2,
) -> List[List[str]]:
    """Simple agglomerative clustering of KG nodes by embedding similarity.

    Groups nodes whose embeddings have cosine similarity >= threshold.
    Returns list of clusters, each cluster is a list of node IDs.
    """
    node_ids = list(G.nodes())
    if not node_ids:
        return []

    # Compute pairwise similarities
    clusters: List[List[str]] = []
    assigned: set = set()

    for i, nid_a in enumerate(node_ids):
        if nid_a in assigned:
            continue

        cluster = [nid_a]
        assigned.add(nid_a)

        for j in range(i + 1, len(node_ids)):
            nid_b = node_ids[j]
            if nid_b in assigned:
                continue

            vec_a = embedding_fn(nid_a)
            vec_b = embedding_fn(nid_b)

            if vec_a and vec_b:
                sim = kg._kg_cosine(vec_a, vec_b)
                if sim >= threshold:
                    cluster.append(nid_b)
                    assigned.add(nid_b)

        if len(cluster) >= min_size:
            clusters.append(cluster)

    return clusters


def _build_embedding_fn(G) -> callable:
    """Build a closure that returns embedding for a node by name+description."""
    def embed_fn(node_id: str):
        node_data = G.nodes[node_id]
        name = node_data.get("name", node_id)
        desc = node_data.get("description", "")
        return kg._kg_get_entity_vec(node_id, {"name": name, "description": desc})
    return embed_fn


# ── Episodic Memory Derivation ─────────────────────────────────────

def derive_episodic(G) -> List[dict]:
    """Derive episodic memories (event chains) from KG.

    Looks for nodes with CAUSES/CAUSED_BY edges and clusters them
    into event chains with evidence links.
    """
    # Find nodes involved in causal relationships
    causal_nodes = set()
    for _, _, edge_data in G.edges(data=True):
        rel = edge_data.get("rel", "")
        if rel in (kg.REL_CAUSES, kg.REL_CAUSED_BY):
            # We'll find these from edges below
            pass

    # Collect causal edges
    causal_edges = []
    for src, dst, edge_data in G.edges(data=True):
        rel = edge_data.get("rel", "")
        if rel in (kg.REL_CAUSES, kg.REL_CAUSED_BY):
            causal_edges.append({
                "source": src,
                "target": dst,
                "source_name": G.nodes[src].get("name", src),
                "target_name": G.nodes[dst].get("name", dst),
                "rel": rel,
            })

    if not causal_edges:
        return []

    # Cluster by semantic similarity of the edge pairs
    # (events that are semantically similar form episodic chains)
    embed_fn = _build_embedding_fn(G)
    edge_embeddings = []
    for ce in causal_edges:
        text = f"{ce['source_name']} {ce['target_name']}"
        vec = kg._kg_bailian_embed(text)
        edge_embeddings.append((ce, vec))

    # Group similar causal edges
    chains = []
    used = set()
    for i, (ce_a, vec_a) in enumerate(edge_embeddings):
        if i in used:
            continue
        chain = [ce_a]
        used.add(i)

        for j, (ce_b, vec_b) in enumerate(edge_embeddings):
            if j in used or not vec_a or not vec_b:
                continue
            if kg._kg_cosine(vec_a, vec_b) >= _CLUSTER_THRESHOLD:
                chain.append(ce_b)
                used.add(j)

        if len(chain) >= 1:  # Even single-edge chains are useful
            chains.append({
                "type": "episodic",
                "size": len(chain),
                "edges": chain,
                "summary": f"Event chain with {len(chain)} related causal relationships",
            })

    return chains


# ── Semantic Memory Derivation ─────────────────────────────────────

def derive_semantic(G) -> List[dict]:
    """Derive semantic memories (entity clusters) from KG.

    Groups semantically similar entities into topic clusters.
    Each cluster becomes a high-level semantic memory entry.
    """
    embed_fn = _build_embedding_fn(G)
    clusters = _cluster_nodes(G, embed_fn, threshold=_CLUSTER_THRESHOLD, min_size=_SEMANTIC_MIN_SIZE)

    semantic_memories = []
    for cluster in clusters:
        # Get representative name from the largest entity
        names = [G.nodes[nid].get("name", nid) for nid in cluster]
        desc_parts = [G.nodes[nid].get("description", "") for nid in cluster if G.nodes[nid].get("description")]

        semantic_memories.append({
            "type": "semantic",
            "cluster_id": "_".join(cluster[:3]),
            "member_count": len(cluster),
            "representative_names": names[:5],
            "combined_description": " | ".join(desc_parts[:3])[:500],
            "members": cluster,
        })

    return semantic_memories


# ── Emotional Memory Derivation ────────────────────────────────────

def derive_emotional(G) -> List[dict]:
    """Derive emotional memories (user preferences) from KG.

    Looks for nodes with PREFERS edges or nodes tagged as preferences.
    """
    preferences = []

    # Find PREFERS edges
    for src, dst, edge_data in G.edges(data=True):
        rel = edge_data.get("rel", "")
        if rel == kg.REL_PREFERS:
            preferences.append({
                "type": "preference",
                "source": src,
                "source_name": G.nodes[src].get("name", src),
                "target": dst,
                "target_name": G.nodes[dst].get("name", dst),
                "properties": {k: v for k, v in edge_data.items() if k != "rel"},
            })

    # Find nodes tagged as preference/user_pref
    for nid, ndata in G.nodes(data=True):
        ntype = ndata.get("type", "")
        if "preference" in ntype.lower() or "pref" in ntype.lower():
            preferences.append({
                "type": "preference_tagged",
                "entity": nid,
                "name": ndata.get("name", nid),
                "description": ndata.get("description", ""),
            })

    return preferences


# ── High-Level Memory Builder ──────────────────────────────────────

def build_high_level_memory() -> dict:
    """Build all high-level memory abstractions from the KG.

    Returns a dict with episodic, semantic, and emotional memories.
    Each abstraction includes traceable links to source base memories.
    """
    G = kg._ensure_loaded()

    episodic = derive_episodic(G)
    semantic = derive_semantic(G)
    emotional = derive_emotional(G)

    result = {
        "derived_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_graph_stats": {
            "nodes": G.number_of_nodes(),
            "edges": G.number_of_edges(),
        },
        "episodic": {
            "chain_count": len(episodic),
            "chains": episodic,
        },
        "semantic": {
            "cluster_count": len(semantic),
            "clusters": semantic,
        },
        "emotional": {
            "preference_count": len(emotional),
            "preferences": emotional,
        },
    }

    # Save to output directory
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = _OUTPUT_DIR / "high_level_memory.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return result


# ── CLI entry point ────────────────────────────────────────────────

if __name__ == "__main__":
    result = build_high_level_memory()
    print(json.dumps(result, ensure_ascii=False, indent=2))
