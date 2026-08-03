"""v0.30 本地 0 quota embed + 重灌 v0.27 失败的 33 docs

走"工具不重复"原则:
- 不重写 embed,fastembed 自带 BGE-small-en-v1.5(384 维,本地)
- 不重写 LLM,v0.28b qwen-flash 已 ship
- 不重写 vector store,chromadb 复用
- ✅ 0 vendor 锁 / 0 quota / 0 网络
- ✅ 真正本地生产级

设计:
- 只灌 33 docs 失败部分(从 chromadb.sqlite3 查哪些 missing)
- 重新 v0.27 流程但用 fastembed
- 跑完重生成完整 knowledge_graph.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# v0.27 + fastembed
sys.path.insert(0, "C:/Users/Administrator/oi_enhancements/mcp_oiagent_server")
import chromadb
from fastembed import TextEmbedding

VAULT_ROOT = Path("C:/Users/Administrator/Documents/ObsidianVault")
PERSIST_DIR = Path("D:/AureonCloud/proton/ruflo_memory")
INGEST_TARGETS = [
    ("aureon_arch", VAULT_ROOT / "Architecture" / "Aureon"),
    ("aureon_experiences", VAULT_ROOT / "experiences"),
    ("aureon_knowledge", VAULT_ROOT / "知识库"),
]
MAX_CHARS_PER_DOC = 4000


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


_model = None
_EMBED_MODEL = os.environ.get("AUREON_V30_EMBED", "BAAI/bge-large-en-v1.5")  # 1024 维兼容 v0.27
_EMBED_DIM = 1024 if "large" in _EMBED_MODEL or "1024" in _EMBED_MODEL else 384

def _get_model():
    global _model
    if _model is None:
        log(f"加载 fastembed {_EMBED_MODEL}(首次 ~30s)...")
        _model = TextEmbedding(model_name=_EMBED_MODEL)
        log(f"fastembed model loaded, dim={_EMBED_DIM}")
    return _model


def extract_content(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end + 3:].strip()
    if len(text) > MAX_CHARS_PER_DOC:
        text = text[:MAX_CHARS_PER_DOC] + f"\n\n[... 截断,原 {len(text)} 字符 ...]"
    return text


def get_existing_ids(client, namespace: str) -> set:
    """从 chromadb 拿已有 doc_id"""
    try:
        col = client.get_or_create_collection(namespace)
        result = col.get(include=[])
        return set(result.get("ids", []))
    except Exception:
        return set()


def ingest_dataset(namespace: str, dir_path: Path, client, model):
    """灌缺失的 docs(v0.27 失败的 33 docs)"""
    if not dir_path.exists():
        log(f"  {dir_path} not found")
        return 0, 0

    col = client.get_or_create_collection(namespace)
    existing = get_existing_ids(client, namespace)
    log(f"  {namespace}: 已存在 {len(existing)} docs")

    md_files = sorted(dir_path.glob("**/*.md"))
    missing = [md for md in md_files
               if f"{namespace}/{md.relative_to(dir_path)}".replace("\\", "/") not in existing]
    log(f"  {namespace}: 总 {len(md_files)} docs, 缺 {len(missing)} docs")

    if not missing:
        return 0, 0

    succeeded = 0
    failed = 0
    for i, md in enumerate(missing, 1):
        try:
            content = extract_content(md)
            if len(content.strip()) < 50:
                continue
            doc_id = f"{namespace}/{md.relative_to(dir_path)}".replace("\\", "/")
            # fastembed embed(本地,0 quota)
            vec = list(model.embed([content]))[0].tolist()
            col.upsert(
                ids=[doc_id],
                embeddings=[vec],
                documents=[content],
                metadatas=[{
                    "namespace": namespace,
                    "source_path": str(md),
                    "filename": md.name,
                }],
            )
            succeeded += 1
            if i % 5 == 0:
                log(f"    [{i}/{len(missing)}] done")
        except Exception as e:
            failed += 1
            log(f"    [{i}] FAILED {md.name}: {e}")
    return succeeded, failed


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=str, default="")
    args = p.parse_args()

    log("=== v0.30 fastembed 本地重灌 33 docs ===")
    log(f"PERSIST_DIR: {PERSIST_DIR}")

    model = _get_model()
    client = chromadb.PersistentClient(path=str(PERSIST_DIR))

    targets = INGEST_TARGETS
    if args.dataset:
        targets = [(n, p) for n, p in targets if n == args.dataset]

    total_s, total_f = 0, 0
    for ns, dir_path in targets:
        log(f"[{ns}]")
        s, f = ingest_dataset(ns, dir_path, client, model)
        total_s += s
        total_f += f
        log(f"  +{s} succeeded, {f} failed")

    log(f"=== 总结: +{total_s} docs, {total_f} failed ===")

    # 验证:看现在每个 collection 多少 doc
    log("=== 当前 chromadb collections ===")
    for ns, _ in targets:
        col = client.get_or_create_collection(ns)
        log(f"  {ns}: {col.count()} docs")


if __name__ == "__main__":
    main()