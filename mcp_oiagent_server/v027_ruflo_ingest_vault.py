"""v0.27_ruflo_ingest_vault.py — ruflo 风格(本地 ONNX + chromadb)灌 Obsidian vault

走"工具不重复"原则:
- 用 chromadb 做向量库(本地,无 vendor 锁)
- embed 走 百炼 text-embedding-v3(已 ship cognee 验证过,1.5s/请求)
- ✅ 真生产级:0 字节外发(走国内 API)
- ✅ 替代 cognee 1.x baml 限制(OpenAI quota 触顶)

v0.27 设计(ruflo memory 风格):
- namespace 拆分: aureon_arch / aureon_experiences / aureon_knowledge
- 百炼 embed → chromadb collection
- metadata: source_path, dataset
- recall:语义相似度 top-k

使用:
  python v0.27_ruflo_ingest_vault.py [--limit N] [--dataset ns] [--query '...']
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

VAULT_ROOT = Path("C:/Users/Administrator/Documents/ObsidianVault")
PERSIST_DIR = Path("D:/AureonCloud/proton/ruflo_memory")
PERSIST_DIR.mkdir(parents=True, exist_ok=True)

INGEST_TARGETS = [
    ("aureon_arch", VAULT_ROOT / "Architecture" / "Aureon"),
    ("aureon_experiences", VAULT_ROOT / "experiences"),
    ("aureon_knowledge", VAULT_ROOT / "知识库"),
]
MAX_CHARS_PER_DOC = 4000


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


_ef = None
def _get_ef():
    """百炼 text-embedding-v3 走 OpenAI 兼容端点"""
    global _ef
    if _ef is not None:
        return _ef
    bailian_key = os.environ.get("BAILIAN_API_KEY", "")
    bailian_base = os.environ.get("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    if not bailian_key:
        log("ERROR: BAILIAN_API_KEY 未设,无法走百炼 embed")
        log("  export BAILIAN_API_KEY=sk-...")
        log("  export BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1")
        sys.exit(1)
    _ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=bailian_key,
        model_name="text-embedding-v3",
        api_base=bailian_base,
    )
    log(f"EF 配好: 百炼 text-embedding-v3 @ {bailian_base}")
    return _ef


_client = None
_collections = {}
def _get_collection(namespace: str):
    """每个 namespace 一个 collection"""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(PERSIST_DIR))
    if namespace not in _collections:
        _collections[namespace] = _client.get_or_create_collection(
            name=namespace,
            embedding_function=_get_ef(),
            metadata={"hnsw:space": "cosine"},
        )
    return _collections[namespace]


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """走百炼 text-embedding-v3"""
    return _get_ef()(texts)


def extract_content(md_path: Path) -> str:
    """读 md,去 YAML frontmatter,截断"""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end + 3:].strip()
    if len(text) > MAX_CHARS_PER_DOC:
        text = text[:MAX_CHARS_PER_DOC] + f"\n\n[... 截断,原 {len(text)} 字符 ...]"
    return text


def ingest_dataset(namespace: str, dir_path: Path, limit: int = 0) -> dict:
    """灌一个 namespace 到 chromadb"""
    if not dir_path.exists():
        return {"namespace": namespace, "ok": False, "error": f"dir not found: {dir_path}"}
    md_files = sorted(dir_path.glob("**/*.md"))
    if limit > 0:
        md_files = md_files[:limit]
    log(f"[{namespace}] 准备灌 {len(md_files)} 个文档")

    col = _get_collection(namespace)

    succeeded = 0
    failed = 0
    skipped = 0

    for i, md in enumerate(md_files, 1):
        try:
            content = extract_content(md)
            if len(content.strip()) < 50:
                skipped += 1
                continue
            # doc_id 用相对路径(稳定,去重)
            doc_id = f"{namespace}/{md.relative_to(dir_path)}".replace("\\", "/")
            # chromadb collection 已配 EF,upsert 不传 embeddings 自动算
            col.upsert(
                ids=[doc_id],
                documents=[content],
                metadatas=[{
                    "namespace": namespace,
                    "source_path": str(md),
                    "filename": md.name,
                }],
            )
            succeeded += 1
            if i % 10 == 0:
                log(f"  [{i}/{len(md_files)}] done")
        except Exception as e:
            failed += 1
            log(f"  [{i}] EXC {md.name}: {type(e).__name__}: {str(e)[:200]}")

    return {
        "namespace": namespace,
        "ok": True,
        "files": len(md_files),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "collection_count": col.count(),
    }


def test_recall(namespace: str, query: str, limit: int = 3) -> dict:
    """语义检索"""
    log(f"[recall] namespace={namespace} query={query!r}")
    col = _get_collection(namespace)
    # chromadb collection 已配 EF,query_texts 自动 embed
    results = col.query(query_texts=[query], n_results=limit)
    docs = results.get("documents", [[]])[0]
    ids = results.get("ids", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    dists = results.get("distances", [[]])[0]
    log(f"  results: {len(docs)}")
    for d, i, m, dist in zip(docs, ids, metas, dists):
        score = 1 - dist
        log(f"    score={score:.3f} id={i[:60]}")
        log(f"    src={m.get('source_path', '?')[:80]}")
        log(f"    snippet: {d[:200]}")
    return {"results": list(zip(docs, ids, metas, dists))}


def stats() -> dict:
    """所有 collection 状态"""
    if not _client:
        return {}
    out = {}
    for name, _ in INGEST_TARGETS:
        try:
            col = _get_collection(name)
            out[name] = {"count": col.count()}
        except Exception as e:
            out[name] = {"error": str(e)[:100]}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="每个 namespace 最多 N 文档")
    p.add_argument("--dataset", type=str, default="", help="只灌一个 namespace")
    p.add_argument("--query", type=str, default="", help="灌完跑 recall 测试")
    p.add_argument("--recall-only", action="store_true", help="只跑 recall")
    p.add_argument("--stats", action="store_true", help="只显示 stats")
    args = p.parse_args()

    log(f"=== v0.27 ruflo memory 灌 Obsidian vault ===")
    log(f"PERSIST_DIR: {PERSIST_DIR}")
    log(f"limit: {args.limit}, dataset: {args.dataset or 'all'}")

    if args.stats:
        s = stats()
        log(f"stats: {json.dumps(s, ensure_ascii=False, indent=2)}")
        return

    if args.recall_only:
        ns = args.dataset or "aureon_arch"
        test_recall(ns, args.query or "Aureon 走什么加密")
        return

    targets = INGEST_TARGETS
    if args.dataset:
        targets = [(n, p) for n, p in INGEST_TARGETS if n == args.dataset]
        if not targets:
            log(f"  dataset {args.dataset} not found")
            return

    # 预加载 EF(显示 load 时间 + 测百炼)
    try:
        ef = _get_ef()
        # 测 1 个 embedding 验通
        test_vec = ef(["warmup test"])
        log(f"EF 预热 OK, dim={len(test_vec[0])}")
    except Exception as e:
        log(f"EF 预热失败: {e}")
        sys.exit(1)

    results = []
    for ns, dir_path in targets:
        r = ingest_dataset(ns, dir_path, limit=args.limit)
        results.append(r)
        log(f"[{ns}] {r}")

    log("=== 总结 ===")
    log(json.dumps(results, ensure_ascii=False, indent=2))

    if args.query:
        for ns, _ in targets:
            test_recall(ns, args.query)


if __name__ == "__main__":
    main()