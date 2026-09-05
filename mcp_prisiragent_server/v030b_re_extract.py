"""v0.30b 重抽实体关系 — 给 v0.30 新灌的 32 docs 补 entity/relation

走"工具不重复"原则:
- 复用 v0.28b LLM(qwen-flash)+ JSON 存
- 复用 v0.27 chromadb collection 拿 doc
- 对新灌 doc 抽 entity/relation
"""
import os, json, sys, time
from pathlib import Path
sys.path.insert(0, "C:/Users/Administrator/oi_enhancements/mcp_prisiragent_server")
import chromadb
import v028_entity_extraction as v28
import v027_ruflo_ingest_vault as v27
v28.LLM_MODEL = "qwen-flash"

PERSIST_DIR = Path("D:/AureonCloud/proton/ruflo_memory")
MAX_DOC_CHARS = 3000


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get_already_extracted_docs(json_path):
    """从已有 JSON 拿已抽过的 source_doc 集合"""
    if not json_path.exists():
        return set()
    d = json.loads(json_path.read_text(encoding="utf-8"))
    docs = set()
    for name, e in d.get("entities", {}).items():
        if e.get("source_doc"):
            docs.add(e["source_doc"])
    for r in d.get("relations", []):
        if r.get("source_doc"):
            docs.add(r["source_doc"])
    return docs


def re_extract_dataset(namespace, dir_path, client):
    """对 v0.30 新灌的 32 docs 重抽"""
    json_path = PERSIST_DIR / f"{namespace}_extraction.json"
    already_extracted = get_already_extracted_docs(json_path)
    log(f"[{namespace}] 已抽 {len(already_extracted)} source_docs")

    # 拿 v0.30 真入库的 doc
    col = client.get_or_create_collection(namespace)
    all_data = col.get(include=["documents", "metadatas"])
    doc_ids = all_data["ids"]
    documents = all_data["documents"]
    metas = all_data["metadatas"]

    # 找未抽过的
    to_extract = []
    for i, did in enumerate(doc_ids):
        src = metas[i].get("source_path", "")
        if src and src not in already_extracted and len(documents[i].strip()) >= 50:
            to_extract.append((i, did, documents[i], src))

    log(f"  {namespace}: 总 {len(doc_ids)} docs, 未抽 {len(to_extract)} docs")

    if not to_extract:
        return 0, 0

    # 读已有 JSON(增量追加)
    if json_path.exists():
        existing = json.loads(json_path.read_text(encoding="utf-8"))
        all_ents = existing.get("entities", {})
        all_rels = existing.get("relations", [])
    else:
        all_ents = {}
        all_rels = []

    new_e, new_r = 0, 0
    for i, did, content, src in to_extract:
        text = content[:MAX_DOC_CHARS]
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                text = text[end+3:].strip()
        result = v28.call_llm_extract([text])
        ents = result.get("entities", [])
        rels = result.get("relations", [])
        for e in ents:
            name = e.get("name", "").strip()
            if name and name not in all_ents:
                all_ents[name] = {**e, "source_doc": src}
                new_e += 1
        for r in rels:
            all_rels.append({**r, "source_doc": src})
            new_r += 1
        if (new_e + new_r) % 10 == 0:
            log(f"    新 +{new_e} ents, +{new_r} rels")
        time.sleep(0.3)

    out = {"namespace": namespace, "entities": all_ents, "relations": all_rels, "extracted_at": time.time()}
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  → {json_path} (+{new_e} ents, +{new_r} rels)")
    return new_e, new_r


def main():
    client = chromadb.PersistentClient(path=str(PERSIST_DIR))
    total_e, total_r = 0, 0
    for ns, dir_path in v27.INGEST_TARGETS:
        new_e, new_r = re_extract_dataset(ns, dir_path, client)
        total_e += new_e
        total_r += new_r
    log(f"=== 总结: +{total_e} ents, +{total_r} rels ===")


if __name__ == "__main__":
    main()
