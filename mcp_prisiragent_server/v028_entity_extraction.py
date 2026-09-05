"""v028_entity_extraction.py — v0.27 + LLM 抽实体 + 关系

走"工具不重复"原则:
- 复用 v0.27 chromadb 灌的文档(同 PERSIST_DIR)
- 复用 cursor-harness llm_providers(走百炼 qwen3-coder-plus)
- ✅ 新:对每个 doc 调 LLM 抽 entities + relations(JSON)
- ✅ 新:存到独立 collection: aureon_entities + aureon_relations
- ✅ 新:enhanced recall 同时返 doc + entities + relations(真知识图谱)

v0.28 设计:
- doc_ids 已存在 v0.27 collections,从那里 get 所有 docs
- 批量抽实体(batch 5 doc/次,避免百炼 rate limit)
- recall: 搜 doc → 拿 doc_id → 查 entities/relations 关联
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

# 复用 v0.27
sys.path.insert(0, "C:/Users/Administrator/oi_enhancements/mcp_prisiragent_server")
import v027_ruflo_ingest_vault as v27

BAILIAN_BASE = os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
BAILIAN_KEY = os.environ.get("BAILIAN_API_KEY", "")
# v0.32 修: qwen-flash context 仅 32K + JSON 质量差(47 docs 中 45 个解析失败)
# qwen-plus context 128K 改善但仍偶发 JSON 解析错误
# 改 qwen3-coder-plus(1M context, excellent JSON)
# 环境变量 AUREON_V28_LLM 可覆盖, 默认 qwen3-coder-plus
LLM_MODEL = os.environ.get("AUREON_V28_LLM", "qwen3-coder-plus")
BATCH_SIZE = 5

# prompt 模板 — 抽 entities + relations JSON
EXTRACTION_PROMPT = """你是一个项目知识抽取助手。从下面 1+ 段项目文档中,提取结构化信息。

要求:
1. entities(实体):从文档中识别人名、模块名、版本号、文件路径、技术概念等关键实体
   格式:`[{{"name": "实体名", "type": "entity_type", "desc": "简短描述"}}]`
2. relations(关系):实体之间的关系
   格式:`[{{"source": "实体1", "target": "实体2", "rel": "关系类型(uses/owns/refers-to/implements/depends-on/...)"}}]`
3. 只输出 JSON,无其他内容
4. 如果文档无有意义实体,返回空数组

输出示例:
```json
{{
  "entities": [{{"name": "Aureon", "type": "project", "desc": "AI 操作系统"}}],
  "relations": []
}}
```

文档:
{docs}
"""


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _repair_json(text: str) -> str | None:
    """尽力修复常见 JSON 语法错误,返回修复后的字符串或 None(修不了)"""
    # 1. 去掉 ```json ... ``` 或 ``` ... ``` 包裹
    text = re.sub(r'^```(?:json)?\s*\n?', '', text.strip())
    text = re.sub(r'\n?\s*```$', '', text.strip())

    # 2. 提取第一个 { 到最后一个 } 的内容
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace >= 0 and last_brace > first_brace:
        text = text[first_brace:last_brace + 1]
    else:
        return None

    # 3. 修复尾随逗号 (},] -> }] 或 ,] -> ])
    text = re.sub(r",\s*([}\]])", r"\1", text)

    return text


def call_llm_extract(docs: list[str]) -> dict:
    """调 LLM 抽实体关系"""
    docs_text = "\n\n---\n\n".join(f"[doc {i+1}]\n{d[:3000]}" for i, d in enumerate(docs))
    prompt = EXTRACTION_PROMPT.format(docs=docs_text)
    try:
        req = urllib.request.Request(
            f"{BAILIAN_BASE}/chat/completions",
            data=json.dumps({
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": "你是项目知识抽取助手。你必须输出合法 JSON。不要输出任何解释文字。不要使用 markdown 代码块标记。只输出纯 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 8000,
            }).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {BAILIAN_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read().decode())
        content = resp["choices"][0]["message"]["content"]
        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # 修复后重试
        repaired = _repair_json(content)
        if repaired:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        log(f"  JSON parse 失败(已尝试修复),原始内容前200: {content[:200]}")
        return {"entities": [], "relations": []}
    except Exception as e:
        log(f"  LLM err: {e}")
        return {"entities": [], "relations": []}


def extract_dataset(namespace: str, batch_size: int = BATCH_SIZE, limit: int = 0):
    """对 v0.27 已灌的 docs 抽实体关系"""
    if not BAILIAN_KEY:
        log("ERROR: BAILIAN_API_KEY 未设")
        sys.exit(1)

    # 复用 v0.27 client + collection
    doc_col = v27._get_collection(namespace)
    ent_col = v27._get_collection(f"{namespace}_entities")
    rel_col = v27._get_collection(f"{namespace}_relations")

    # 拿所有 docs
    all_docs = doc_col.get(include=["documents", "metadatas"])
    doc_ids = all_docs["ids"]
    documents = all_docs["documents"]
    log(f"[{namespace}] 共 {len(doc_ids)} docs, 抽实体关系...")

    if limit > 0:
        doc_ids = doc_ids[:limit]
        documents = documents[:limit]

    # batch 处理
    all_entities = []
    all_relations = []
    for i in range(0, len(doc_ids), batch_size):
        batch_ids = doc_ids[i:i+batch_size]
        batch_docs = documents[i:i+batch_size]
        log(f"  [{i+1}-{i+len(batch_ids)}/{len(doc_ids)}] LLM 抽实体...")
        result = call_llm_extract(batch_docs)
        ents = result.get("entities", [])
        rels = result.get("relations", [])
        log(f"    {len(ents)} entities, {len(rels)} relations")
        all_entities.extend(ents)
        all_relations.extend(rels)

    # 去重 entities(by name)
    seen = set()
    unique_entities = []
    for e in all_entities:
        name = e.get("name", "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        unique_entities.append(e)
    # 去重 relations(by source+target+rel)
    seen_r = set()
    unique_relations = []
    for r in all_relations:
        key = (r.get("source", ""), r.get("target", ""), r.get("rel", ""))
        if not all(key) or key in seen_r:
            continue
        seen_r.add(key)
        unique_relations.append(r)

    # 存到 chromadb(entities 和 relations 都用 0-vector 占位,因 chromadb collection 已配 EF)
    # 真 metadata 存 entity/relation 字段
    # v0.28 修:百炼 embed batch_size ≤ 10,真大 entity/relation 列表需分批
    UPSERT_BATCH = 10
    log(f"  存 {len(unique_entities)} entities, {len(unique_relations)} relations")
    if unique_entities:
        for i in range(0, len(unique_entities), UPSERT_BATCH):
            batch_e = unique_entities[i:i+UPSERT_BATCH]
            ent_ids = [f"ent/{namespace}/{i+j}/{e['name']}" for j, e in enumerate(batch_e)]
            ent_col.upsert(
                ids=ent_ids,
                documents=[f"{e.get('type', '')}: {e.get('name', '')} - {e.get('desc', '')}" for e in batch_e],
                metadatas=[{
                    "namespace": namespace,
                    "name": e.get("name", ""),
                    "type": e.get("type", ""),
                    "desc": e.get("desc", ""),
                } for e in batch_e],
            )
    if unique_relations:
        for i in range(0, len(unique_relations), UPSERT_BATCH):
            batch_r = unique_relations[i:i+UPSERT_BATCH]
            rel_ids = [f"rel/{namespace}/{i+j}/{r.get('source', '')}/{r.get('target', '')}" for j, r in enumerate(batch_r)]
            rel_col.upsert(
                ids=rel_ids,
                documents=[f"{r.get('source', '')} -[{r.get('rel', '')}]-> {r.get('target', '')}" for r in batch_r],
                metadatas=[{
                    "namespace": namespace,
                    "source": r.get("source", ""),
                    "target": r.get("target", ""),
                    "rel": r.get("rel", ""),
                } for r in batch_r],
            )

    return {
        "namespace": namespace,
        "docs": len(doc_ids),
        "entities": len(unique_entities),
        "relations": len(unique_relations),
    }


def enhanced_recall(namespace: str, query: str, limit: int = 3):
    """v0.28 enhanced recall — 返 doc + 关联 entities + relations"""
    log(f"[enhanced recall] ns={namespace} q={query!r}")
    doc_col = v27._get_collection(namespace)
    ent_col = v27._get_collection(f"{namespace}_entities")
    rel_col = v27._get_collection(f"{namespace}_relations")

    # 1. 查 doc
    docs = doc_col.query(query_texts=[query], n_results=limit)
    doc_ids = docs["ids"][0]
    doc_metas = docs["metadatas"][0]
    doc_texts = docs["documents"][0]
    log(f"  docs: {len(doc_ids)}")

    # 2. 用 doc 里的关键词查 entity
    if doc_texts:
        # 用 doc content 查 entity
        ent_results = ent_col.query(query_texts=doc_texts[:3], n_results=5)
        ent_ids = ent_results["ids"][0]
        ent_metas = ent_results["metadatas"][0]
        log(f"  entities: {len(ent_ids)}")
    else:
        ent_metas = []

    # 3. 用 entity 查 relation
    if ent_metas:
        rel_results = rel_col.query(
            query_texts=[e.get("name", "") for e in ent_metas[:5]],
            n_results=5,
        )
        rel_metas = rel_results["metadatas"][0]
        log(f"  relations: {len(rel_metas)}")
    else:
        rel_metas = []

    # 拼成"知识图谱"格式
    out = {
        "query": query,
        "namespace": namespace,
        "docs": [
            {"id": did, "meta": m, "snippet": d[:200]}
            for did, m, d in zip(doc_ids, doc_metas, doc_texts)
        ],
        "entities": [
            {"name": e.get("name"), "type": e.get("type"), "desc": e.get("desc")}
            for e in ent_metas
        ],
        "relations": [
            {"source": r.get("source"), "rel": r.get("rel"), "target": r.get("target")}
            for r in rel_metas
        ],
    }
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=str, default="", help="只抽一个 namespace")
    p.add_argument("--limit", type=int, default=0, help="每个 namespace 最多 N doc 抽")
    p.add_argument("--query", type=str, default="", help="抽完跑 enhanced recall")
    p.add_argument("--recall-only", action="store_true", help="只跑 recall")
    args = p.parse_args()

    log("=== v0.28 ruflo entity extraction ===")
    log(f"BAILIAN_BASE: {BAILIAN_BASE}")
    log(f"LLM_MODEL: {LLM_MODEL}")
    log(f"BATCH_SIZE: {BATCH_SIZE}")

    # 复用 v0.27 client
    v27._get_collection("aureon_arch")  # 触发 client init

    if args.recall_only:
        ns = args.dataset or "aureon_arch"
        out = enhanced_recall(ns, args.query or "Aureon 走什么加密")
        log(f"=== enhanced recall 结果 ===")
        log(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
        return

    targets = v27.INGEST_TARGETS
    if args.dataset:
        targets = [(n, p) for n, p in targets if n == args.dataset]
        if not targets:
            log(f"  dataset {args.dataset} not found")
            return

    results = []
    for ns, _ in targets:
        r = extract_dataset(ns, batch_size=BATCH_SIZE, limit=args.limit)
        results.append(r)
        log(f"[{ns}] {r}")

    log("=== 总结 ===")
    log(json.dumps(results, ensure_ascii=False, indent=2))

    if args.query:
        for ns, _ in targets:
            out = enhanced_recall(ns, args.query)
            log(f"=== enhanced recall: {ns} ===")
            log(json.dumps(out, ensure_ascii=False, indent=2)[:2000])


if __name__ == "__main__":
    main()