"""v0.29 真·知识图谱 — NetworkX 内存图 + 持久化 JSON

走"工具不重复"原则:
- 复用 v0.28b 抽的 JSON 实体关系(D:/AureonCloud/proton/ruflo_memory/*_extraction.json)
- 不用 Kuzu(Kuzu Cypher lite 限制多)
- ✅ NetworkX 内存图 — Python 原生,无 schema 限制
- ✅ 持久化:networkx 序列化到 graph.json + 可重载
- ✅ enhanced recall:doc + entity + relation 三方 join(NetworkX query)
- 绕开百炼 embed quota(用 entity name 字符串匹配,不做向量相似度)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import networkx as nx

PERSIST_DIR = Path("D:/AureonCloud/proton/ruflo_memory")
GRAPH_FILE = PERSIST_DIR / "knowledge_graph.json"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def init_graph() -> nx.DiGraph:
    """load 或建空图"""
    if GRAPH_FILE.exists():
        try:
            G = nx.readwrite.json_graph.node_link_graph(json.loads(GRAPH_FILE.read_text(encoding="utf-8")))
            log(f"load 图 from {GRAPH_FILE}: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
            return G
        except Exception as e:
            log(f"  load err: {e},建新图")
    G = nx.DiGraph()
    return G


def save_graph(G: nx.DiGraph):
    """持久化"""
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    data = nx.readwrite.json_graph.node_link_data(G)
    GRAPH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"saved {GRAPH_FILE} ({G.number_of_nodes()} nodes, {G.number_of_edges()} edges)")


def load_extraction(G: nx.DiGraph, namespace: str) -> tuple[int, int]:
    """从 v0.28b JSON 加载到 NetworkX"""
    json_path = PERSIST_DIR / f"{namespace}_extraction.json"
    if not json_path.exists():
        log(f"  {json_path} not found")
        return 0, 0
    data = json.loads(json_path.read_text(encoding="utf-8"))
    entities = data.get("entities", {})
    relations = data.get("relations", [])

    e_count = 0
    for name, e in entities.items():
        if not name:
            continue
        eid = name.replace(" ", "_")
        if not G.has_node(eid):
            G.add_node(eid, **{
                "namespace": namespace,
                "type": e.get("type", "unknown"),
                "description": e.get("desc", "")[:500],
                "source_doc": e.get("source_doc", "")[:200],
            })
            e_count += 1

    r_count = 0
    for r in relations:
        src = r.get("source", "").replace(" ", "_")
        tgt = r.get("target", "").replace(" ", "_")
        rel = r.get("rel", "related-to")
        if not src or not tgt or src == tgt:
            continue
        # 端点必须存在(NetworkX 不会自动建)
        if G.has_node(src) and G.has_node(tgt):
            G.add_edge(src, tgt, rel=rel)
            r_count += 1
    log(f"  {namespace}: +{e_count} entities, +{r_count} relations")
    return e_count, r_count


def enhanced_recall(G: nx.DiGraph, namespace: str, query: str, top_k: int = 5) -> dict:
    """enhanced recall — NetworkX 查询"""
    log(f"[recall] ns={namespace} q={query!r}")
    q_lower = query.lower()

    # 1. 找匹配 entity(关键词 in id 或 description)
    q_keywords = [q_lower] + [kw.strip().lower() for kw in query.split() if len(kw.strip()) >= 2]
    matched_entities = []
    for node_id, attrs in G.nodes(data=True):
        if attrs.get("namespace") != namespace:
            continue
        if any(kw in node_id.lower() or kw in (attrs.get("description", "")).lower() for kw in q_keywords):
            matched_entities.append({
                "id": node_id,
                "type": attrs.get("type"),
                "description": attrs.get("description"),
                "source_doc": attrs.get("source_doc"),
            })
            if len(matched_entities) >= top_k * 2:
                break

    log(f"  matched entities: {len(matched_entities)}")
    for e in matched_entities[:3]:
        log(f"    - {e['id']} ({e['type']}): {e['description'][:60]}")

    # 2. 1-hop 邻接
    relations = []
    seen = set()
    for e in matched_entities[:top_k]:
        eid = e["id"]
        for _, tgt, attrs in G.out_edges(eid, data=True):
            rel_key = (eid, attrs.get("rel", "related-to"), tgt)
            if rel_key not in seen:
                seen.add(rel_key)
                relations.append({"source": eid, "rel": attrs.get("rel", "related-to"), "target": tgt})
    for src, _, attrs in G.in_edges(eid, data=True):
        rel_key = (src, attrs.get("rel", "related-to"), eid)
        if rel_key not in seen:
            seen.add(rel_key)
            relations.append({"source": src, "rel": attrs.get("rel", "related-to"), "target": eid})

    log(f"  relations: {len(relations)}")
    for r in relations[:5]:
        log(f"    - {r['source']} -[{r['rel']}]-> {r['target']}")

    # 3. source_docs
    source_docs = list({e["source_doc"] for e in matched_entities if e.get("source_doc")})
    log(f"  source docs: {len(source_docs)}")

    return {
        "query": query,
        "namespace": namespace,
        "entities": matched_entities[:top_k],
        "relations": relations,
        "source_docs": source_docs,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=str, default="")
    p.add_argument("--query", type=str, default="")
    p.add_argument("--load-only", action="store_true")
    args = p.parse_args()

    log("=== v0.29 真·知识图谱(NetworkX 替代 Kuzu) ===")

    G = init_graph()
    n0, e0 = G.number_of_nodes(), G.number_of_edges()
    log(f"start: {n0} nodes, {e0} edges")

    targets = ["aureon_arch", "aureon_experiences", "aureon_knowledge"]
    if args.dataset:
        targets = [args.dataset]

    for ns in targets:
        e, r = load_extraction(G, ns)
        n0 += 0
        e0 += 0
    log(f"after load: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    save_graph(G)

    if args.query:
        ns = args.dataset or "aureon_arch"
        out = enhanced_recall(G, ns, args.query)
        log("=== recall 结果 ===")
        log(json.dumps(out, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()