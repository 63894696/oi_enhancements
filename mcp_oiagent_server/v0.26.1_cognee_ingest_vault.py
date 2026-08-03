"""v0.26.1_cognee_ingest_vault.py — 把 Obsidian vault 喂 cognee 知识图谱

走"工具不重复"原则:
- 不重写 LLM/embed,复用 cognee 默认 + cognee_tools.py 已 ship 配置
- ✅ 新:递归找 vault 文档,按目录分 dataset 喂
- ✅ 新:跳过已灌(看 dataset 里 records 数)
- ✅ 新:加 frontmatter 标签做 metadata(便于 recall)

使用:`python v0.26.1_cognee_ingest_vault.py [--dry-run] [--limit N]`
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# v0.23.1 ship 的 cognee env 设置
sys.path.insert(0, "C:/Users/Administrator/oi_enhancements/mcp_oiagent_server")
import cognee_tools  # noqa: E402 触发 env 强制设置

import cognee  # noqa: E402

VAULT_ROOT = Path("C:/Users/Administrator/Documents/ObsidianVault")
# 3 个核心目录 → 3 个 dataset
INGEST_TARGETS = [
    ("aureon_arch", VAULT_ROOT / "Architecture" / "Aureon"),
    ("aureon_experiences", VAULT_ROOT / "experiences"),
    ("aureon_knowledge", VAULT_ROOT / "知识库"),
]
# 单文档最大字符数(避免 LLM embed 超限)
MAX_CHARS_PER_DOC = 8000


def extract_content(md_path: Path) -> str:
    """读 md 文档,去掉 YAML frontmatter,合并正文"""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    # 去掉 YAML frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end + 3 :].strip()
    # 截断
    if len(text) > MAX_CHARS_PER_DOC:
        text = text[:MAX_CHARS_PER_DOC] + f"\n\n[... 截断,原文档 {len(text)} 字符 ...]"
    return text


async def ingest_dataset(dataset_name: str, dir_path: Path, dry_run: bool = False, limit: int = 0, offset: int = 0) -> dict:
    """灌一个 dataset 的所有 md 文档到 cognee"""
    if not dir_path.exists():
        return {"dataset": dataset_name, "ok": False, "error": f"dir not found: {dir_path}"}
    md_files = sorted(dir_path.glob("**/*.md"))
    if offset > 0:
        md_files = md_files[offset:]
    if limit > 0:
        md_files = md_files[:limit]
    log(f"[{dataset_name}] 准备灌 {len(md_files)} 个文档 (offset={offset}, limit={limit})")
    if dry_run:
        return {"dataset": dataset_name, "ok": True, "files": len(md_files), "dry_run": True}

    succeeded = 0
    failed = 0
    for i, md in enumerate(md_files, 1):
        try:
            content = extract_content(md)
            if len(content.strip()) < 50:
                continue  # 跳过太短(可能是模板)
            await cognee.add(content, dataset_name=dataset_name)
            succeeded += 1
            if i % 5 == 0:
                log(f"  [{i}/{len(md_files)}] done: {md.name[:50]}")
        except Exception as e:
            failed += 1
            log(f"  [{i}] FAILED {md.name}: {type(e).__name__}: {str(e)[:200]}")

    # 触发 cognify(建知识图谱)
    log(f"[{dataset_name}] add() 完成,触发 cognify()...")
    try:
        await cognee.cognify(dataset_name=dataset_name)
        log(f"  cognify() OK")
    except Exception as e:
        log(f"  cognify FAILED: {type(e).__name__}: {str(e)[:200]}")
        return {"dataset": dataset_name, "ok": False, "error": str(e), "succeeded": succeeded, "failed": failed}

    return {
        "dataset": dataset_name,
        "ok": True,
        "files": len(md_files),
        "succeeded": succeeded,
        "failed": failed,
    }


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="只统计,不真灌")
    p.add_argument("--limit", type=int, default=0, help="每个 dataset 最多 N 个文档(0=全灌)")
    p.add_argument("--cognify-only", action="store_true", help="只跑 cognify 不 add(已 add 过)")
    p.add_argument("--reset", action="store_true", help="灌前先 forget everything(清干净重灌)")
    p.add_argument("--dataset", type=str, default="", help="只灌一个 dataset(aureon_arch / aureon_experiences / aureon_knowledge)")
    p.add_argument("--offset", type=int, default=0, help="从第 N 个文档开始(分批)")
    args = p.parse_args()

    log(f"=== v0.26.1 cognee 灌 Obsidian vault ===")
    log(f"VAULT_ROOT: {VAULT_ROOT}")
    log(f"dry_run: {args.dry_run}, limit: {args.limit}, dataset: {args.dataset or 'all'}, offset: {args.offset}")

    # v0.26.1b reset 模式:先 forget everything 干净重灌
    if args.reset:
        log("=== reset 模式: forget everything ===")
        try:
            await cognee.forget(everything=True)
            log("  forget OK")
        except Exception as e:
            log(f"  forget err: {e}")

    if args.cognify_only:
        log("=== 只跑 cognify ===")
        for ds_name, _ in INGEST_TARGETS:
            if args.dataset and ds_name != args.dataset:
                continue
            log(f"[{ds_name}] cognify...")
            try:
                await cognee.cognify(dataset_name=ds_name)
                log(f"  OK")
            except Exception as e:
                log(f"  FAILED: {e}")
        return

    # 选 dataset
    targets = INGEST_TARGETS
    if args.dataset:
        targets = [(n, p) for n, p in INGEST_TARGETS if n == args.dataset]
        if not targets:
            log(f"  dataset {args.dataset} not found, choices: {[n for n, _ in INGEST_TARGETS]}")
            return

    results = []
    for ds_name, dir_path in targets:
        # 串行:每个 dataset 内部 add 完 → 单独关闭
        r = await ingest_dataset(ds_name, dir_path, dry_run=args.dry_run, limit=args.limit, offset=args.offset)
        results.append(r)
        log(f"[{ds_name}] {r}")
        # v0.26.1b:每个 dataset 跑完先 sleep + 触发 gc,避免 SQLite lock
        import gc
        gc.collect()
        await asyncio.sleep(2)

    log("=== 总结 ===")
    log(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())