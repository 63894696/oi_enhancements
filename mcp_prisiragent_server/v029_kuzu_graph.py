"""v0.29 真·知识图谱 — Kuzu 图数据库 + enhanced recall

走"工具不重复"原则:
- 复用 v0.28b 抽的 JSON 实体关系(D:/AureonCloud/proton/ruflo_memory/*_extraction.json)
- 不重写 LLM,复用 qwen-flash
- ✅ Kuzu 真图数据库(支持 Cypher 查询)— 替代 JSON 文件
- ✅ enhanced recall:doc + entity + relation 三方 join(query Kuzu)
- 绕开百炼 embed quota(用 entity name 字符串匹配,不做向量相似度)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import kuzu

PERSIST_DIR = Path("D:/AureonCloud/proton/ruflo_memory")
# Kuzu 路径:Windows 上绝对路径报"不能是 dir"bug,用相对路径
KUDU_NAME = "kuzu_graph_v0_29"  # 短名,无后缀,Kuzu 接受
PERSIST_DIR.mkdir(parents=True, exist_ok=True)  # 改用 PERSIST_DIR 代替 KUDU_DIR


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def init_kuzu():
    """init Kuzu schema (Entity, Relation)"""
    # v0.29 修:Windows 上 Kuzu 绝对路径报"不能是 dir",改相对路径
    import os
    os.chdir(PERSIST_DIR)
    db = kuzu.Database(KUDU_NAME)
    conn = kuzu.Connection(db)
    # 节点表
    conn.execute("""
        CREATE NODE TABLE IF NOT EXISTS Entity(
            id STRING,
            namespace STRING,
            type STRING,
            description STRING,
            source_doc STRING,
            PRIMARY KEY(id)
        )
    """)
    # 关系表
    conn.execute("""
        CREATE REL TABLE IF NOT EXISTS REL(
            FROM Entity TO Entity,
            rel STRING
        )
    """)
    log("Kuzu schema init OK")
    return db, conn


def load_extraction(namespace: str, conn):
    """从 v0.28b JSON 加载到 Kuzu"""
    json_path = PERSIST_DIR / f"{namespace}_extraction.json"
    if not json_path.exists():
        log(f"  {json_path} not found")
        return 0, 0
    data = json.loads(json_path.read_text(encoding="utf-8"))
    entities = data.get("entities", {})
    relations = data.get("relations", [])

    # 先插 entity(去重,确保 start node 在)
    entity_count = 0
    for name, e in entities.items():
        if not name:
            continue
        # 简化:entity name 转合法 node id(去空格)
        eid = name.replace(" ", "_")
        try:
            # v0.29 修:Kuzu 不支持 MATCH+SET/MERGE+SET 单 query,直接 CREATE
            conn.execute(
                "CREATE (e:Entity {id: $id, namespace: $ns, type: $type, description: $desc, source_doc: $src})",
                {
                    "id": eid,
                    "ns": namespace,
                    "type": e.get("type", "unknown"),
                    "desc": e.get("desc", "")[:500],
                    "src": e.get("source_doc", "")[:200],
                },
            )
            entity_count += 1
        except Exception as ex:
            err_str = str(ex)
            # 同名 entity 重复(主键冲突)不报错
            if "Duplicate" in err_str or "already exists" in err_str.lower():
                entity_count += 1  # 算成功
            else:
                log(f"  entity {name} err: {ex}")
    # 插 relation(两端 entity 必须存在)
    rel_count = 0
    for r in relations:
        src = r.get("source", "").replace(" ", "_")
        tgt = r.get("target", "").replace(" ", "_")
        rel = r.get("rel", "related-to")
        if not src or not tgt or src == tgt:
            continue
        try:
            conn.execute(
                "MATCH (a:Entity {id: $s}), (b:Entity {id: $t}) "
                "MERGE (a)-[r:REL {rel: $rel}]->(b)",
                {"s": src, "t": tgt, "rel": rel},
            )
            rel_count += 1
        except Exception as ex:
            log(f"  rel {src}->{tgt} err: {ex}")
    log(f"  {namespace}: {entity_count} entities + {rel_count} relations")
    return entity_count, rel_count


def load_all():
    """3 dataset 全部灌 Kuzu"""
    db, conn = init_kuzu()
    total_e, total_r = 0, 0
    for ns in ["aureon_arch", "aureon_experiences", "aureon_knowledge"]:
        log(f"[{ns}] loading...")
        e, r = load_extraction(ns, conn)
        total_e += e
        total_r += r
    log(f"=== 总计: {total_e} entities + {total_r} relations ===")
    return db, conn


def enhanced_recall(conn, namespace: str, query: str, top_k: int = 5):
    """enhanced recall — 查 Kuzu 图 + 关联 entity/relation

    1. 关键词 query → 查 Entity.name / desc 包含关键词的节点
    2. 拿这些节点的 1-hop 邻接(relations + entities)
    3. 拿节点的 source_doc 路径
    """
    log(f"[recall] ns={namespace} q={query!r}")

    # 1. 关键词匹配 entity(简化:LIKE)
    q_lower = query.lower()
    q_keywords = [q_lower]
    # 简单分词:按空格拆
    for kw in query.split():
        kw = kw.strip().lower()
        if len(kw) >= 2:
            q_keywords.append(kw)

    # 2. 找匹配 entity
    matched_entities = []
    seen_eid = set()
    for kw in q_keywords:
        pattern = f"%{kw}%"
        try:
            results = conn.execute(
                "MATCH (e:Entity) "
                "WHERE e.namespace = $ns AND (LOWER(e.id) CONTAINS $kw OR LOWER(e.description) CONTAINS $kw) "
                "RETURN e.id, e.type, e.description, e.source_doc LIMIT $limit",
                {"ns": namespace, "kw": pattern.lower(), "limit": top_k * 2},
            )
            while results.has_next():
                row = results.get_next()
                eid = row[0]
                if eid not in seen_eid:
                    seen_eid.add(eid)
                    matched_entities.append({
                        "id": eid,
                        "type": row[1],
                        "desc": row[2],
                        "source_doc": row[3],
                    })
        except Exception as ex:
            log(f"  query err: {ex}")

    log(f"  matched entities: {len(matched_entities)}")
    for e in matched_entities[:3]:
        log(f"    - {e['id']} ({e['type']}): {e['desc'][:60]}")

    # 3. 拿关联 relations(1-hop)
    relations = []
    seen_rel = set()
    for e in matched_entities[:top_k]:
        eid = e["id"]
        try:
            results = conn.execute(
                "MATCH (a:Entity {id: $id})-[r:REL]->(b:Entity) "
                "RETURN a.id, r.rel, b.id LIMIT 10",
                {"id": eid},
            )
            while results.has_next():
                row = results.get_next()
                rel_key = (row[0], row[1], row[2])
                if rel_key not in seen_rel:
                    seen_rel.add(rel_key)
                    relations.append({
                        "source": row[0],
                        "rel": row[1],
                        "target": row[2],
                    })
        except Exception as ex:
            log(f"  rel query err: {ex}")

    log(f"  relations: {len(relations)}")
    for r in relations[:5]:
        log(f"    - {r['source']} -[{r['rel']}]-> {r['target']}")

    # 4. 拿 source_doc
    source_docs = list({e["source_doc"] for e in matched_entities if e["source_doc"]})
    log(f"  source docs: {len(source_docs)}")
    for sd in source_docs[:3]:
        log(f"    - {sd[:80]}")

    return {
        "query": query,
        "namespace": namespace,
        "entities": matched_entities[:top_k],
        "relations": relations,
        "source_docs": source_docs,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query", type=str, default="", help="recall 测试查询")
    p.add_argument("--dataset", type=str, default="", help="单 namespace load")
    p.add_argument("--load-only", action="store_true", help="只 load 不 recall")
    args = p.parse_args()

    log("=== v0.29 Kuzu 图数据库 + enhanced recall ===")

    if args.dataset:
        # 单 namespace load
        db, conn = init_kuzu()
        e, r = load_extraction(args.dataset, conn)
        log(f"  → {e} ents + {r} rels")
        return

    # load all
    db, conn = load_all()

    if args.query:
        ns = args.dataset or "aureon_arch"
        out = enhanced_recall(conn, ns, args.query)
        log("=== recall 结果 ===")
        log(json.dumps(out, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()
