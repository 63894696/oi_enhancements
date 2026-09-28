# -*- coding: utf-8 -*-
"""_rebuild_p14_index.py — 一次性重建 _incremental/_p14_index.json(2026-09-23)

用途:
  - 之前 fcontent_root 指向不存在的 D:/Temp/p14_test_v3,导致 _incremental/ 文件已
    写在真 vault(C:/Users/Administrator/Documents/ObsidianVault/_incremental/)但
    _p14_index.json 还在死路径下;p14_ingest.load_index() 返空 dict,后续段都被
    当成"新段"重写
  - 本脚本扫真 vault _incremental/*.md,从每个 md 抽 (ts, sha16) 块,重建
    _p14_index.json,让 p14_ingest 重新认识已有段

原理:
  - _p14_index.json schema: {sha16: relative_path}
  - 每条入档段在 md 里格式: "## 2026-09-22 18:00:56 (49d6ae51cce01f17)"
  - 抽 16-hex hash,index 写到根目录 _p14_index.json

用法:
  python _rebuild_p14_index.py           # 用真 vault
  python _rebuild_p14_index.py --dry-run # 只显示统计不写

退出码:
  0 = ok; 1 = 没找到 _incremental; 2 = 找到但 0 个 md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 真 vault 路径(fcontent_root 同 companion_asr_settings.json)
DEFAULT_ROOT = Path("C:/Users/Administrator/Documents/ObsidianVault")
DIR_NAME = "_incremental"
HASH_PATTERN = re.compile(r"## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \(([0-9a-f]{16})\)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT),
                    help="fcontent_root(vault 根)")
    ap.add_argument("--dir", default=DIR_NAME,
                    help=f"incremental 子目录名(默认 {DIR_NAME})")
    ap.add_argument("--dry-run", action="store_true", help="只显示不写")
    args = ap.parse_args()

    root = Path(args.root)
    incr_dir = root / args.dir

    if not incr_dir.exists():
        print(f"[rebuild] FAIL: {incr_dir} 不存在")
        return 1

    md_files = sorted(incr_dir.glob("*.md"))
    if not md_files:
        print(f"[rebuild] FAIL: {incr_dir} 下无 .md 文件")
        return 2

    print(f"[rebuild] 扫 {incr_dir} ({len(md_files)} files)")
    new_index: dict[str, str] = {}
    file_segments: dict[str, int] = {}
    for md in md_files:
        rel = str(md.relative_to(root)).replace("\\", "/")
        text = md.read_text(encoding="utf-8")
        hashes = HASH_PATTERN.findall(text)
        file_segments[md.name] = len(hashes)
        for _ts, h in hashes:
            new_index[h] = rel

    print(f"[rebuild] 共 {len(new_index)} 段(去重前)")
    # 同 hash 多文件 → 记录冲突(但仍取第一个)
    hash_to_files: dict[str, list[str]] = {}
    for md in md_files:
        for _ts, h in HASH_PATTERN.findall(md.read_text(encoding="utf-8")):
            hash_to_files.setdefault(h, []).append(md.name)
    conflicts = {h: files for h, files in hash_to_files.items() if len(files) > 1}
    if conflicts:
        print(f"[rebuild] WARN: {len(conflicts)} 个 hash 跨多文件(去重取首):")
        for h, files in list(conflicts.items())[:5]:
            print(f"  {h}: {files}")

    print("[rebuild] 文件级统计:")
    for name, n in file_segments.items():
        print(f"  {name}: {n} 段")

    if args.dry_run:
        print("[rebuild] DRY-RUN,不写")
        return 0

    index_path = root / "_p14_index.json"
    index_path.write_text(
        json.dumps(new_index, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"[rebuild] OK: 写 {index_path} ({len(new_index)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())