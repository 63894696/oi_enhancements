"""v0.28b 简化版:LLM 抽实体关系不存 chromadb,改存 JSON 文件
原因:百炼 embed quota 触顶,只 LLM 不存 vector
"""
import os, json, sys, time
from pathlib import Path
sys.path.insert(0, "C:/Users/Administrator/oi_enhancements/mcp_oiagent_server")
import v028_entity_extraction as v28
import v027_ruflo_ingest_vault as v27

# 强制 qwen-flash(LLM quota 已恢复)
v28.LLM_MODEL = "qwen-flash"

VAULT_ROOT = Path("C:/Users/Administrator/Documents/ObsidianVault")
INGEST_TARGETS = v27.INGEST_TARGETS
PERSIST_DIR = Path("D:/AureonCloud/proton/ruflo_memory")
PERSIST_DIR.mkdir(parents=True, exist_ok=True)
MAX_DOC_CHARS = 3000


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def extract_dataset(namespace: str, dir_path: Path, limit: int = 0):
    """对 v0.27 docs 抽实体关系存 JSON"""
    BAILIAN_KEY = os.environ.get("BAILIAN_API_KEY", ""); v28.BAILIAN_KEY = BAILIAN_KEY
    if not BAILIAN_KEY:
        log("BAILIAN_API_KEY 未设"); return {}

    md_files = sorted(dir_path.glob("**/*.md"))
    if limit > 0:
        md_files = md_files[:limit]
    log(f"[{namespace}] {len(md_files)} docs, 抽实体关系 → JSON")

    all_ents = {}
    all_rels = []
    for i, md in enumerate(md_files, 1):
        text = v27.extract_content(md) if hasattr(v27, 'extract_content') else md.read_text(encoding="utf-8", errors="replace")[:MAX_DOC_CHARS]
        if len(text.strip()) < 50:
            continue
        # 截 + 去 frontmatter
        if text.startswith("---"):
            end = text.find("---", 3)
            if end > 0:
                text = text[end+3:].strip()
        text = text[:MAX_DOC_CHARS]
        result = v28.call_llm_extract([text])
        ents = result.get("entities", [])
        rels = result.get("relations", [])
        for e in ents:
            name = e.get("name", "").strip()
            if name and name not in all_ents:
                all_ents[name] = {**e, "source_doc": str(md)}
        for r in rels:
            all_rels.append({**r, "source_doc": str(md)})
        if i % 5 == 0:
            log(f"  [{i}/{len(md_files)}] ents={len(all_ents)} rels={len(all_rels)}")
        time.sleep(0.5)  # 避免百炼限速

    out_path = PERSIST_DIR / f"{namespace}_extraction.json"
    out = {"namespace": namespace, "entities": all_ents, "relations": all_rels, "extracted_at": time.time()}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  → {out_path} ({len(all_ents)} entities, {len(all_rels)} relations)")
    return out


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=str, default="")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    log("=== v0.28b LLM 抽实体关系(不存 chromadb embed) ===")
    log(f"LLM: {v28.LLM_MODEL}")
    log(f"PERSIST_DIR: {PERSIST_DIR}")

    targets = INGEST_TARGETS
    if args.dataset:
        targets = [(n, p) for n, p in targets if n == args.dataset]

    for ns, dir_path in targets:
        extract_dataset(ns, dir_path, limit=args.limit)
    log("=== 完成 ===")

if __name__ == "__main__":
    main()
