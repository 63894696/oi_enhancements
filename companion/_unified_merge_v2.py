#!/usr/bin/env python3
"""_unified_merge_v2.py — M3.66+ 合并 v2 数据集(2026-09-24)

与 v1 区别:每个 scenario 同时合并 baseline + v2 补丁
- safety: baseline 120 + v2 500 = 620
- tempfile: baseline 290 + v2 480 = 770
- email: baseline 400 + v2 240 = 640
- disk_cleanup: baseline 203 + v2 320 = 523
- log/perf/intents/task: 仅 baseline(已平衡)

ch.jsonl 已通过 data_prep_classification_head.py 生成过,
本次直接对 v2 数据源(baseline ch.jsonl + v2 ch.jsonl)合并。
"""
import json
import sys
from pathlib import Path

# Windows 本地: C:/Users/Administrator/oi_enhancements/companion/data/
# aliyun 上: /workspace/companion/data/
COMPANION_DATA = Path("C:/Users/Administrator/oi_enhancements/companion/data")
ALIYUN_DATA = Path("/workspace/companion/data")

CH_BASE_LOCAL = COMPANION_DATA / "ch_out"
CH_BASE_ALIYUN = ALIYUN_DATA / "ch_out"
CH_BASE = CH_BASE_ALIYUN if CH_BASE_ALIYUN.exists() else CH_BASE_LOCAL

OUT_DIR_LOCAL = COMPANION_DATA
OUT_DIR_ALIYUN = ALIYUN_DATA
OUT_DIR = OUT_DIR_ALIYUN if OUT_DIR_ALIYUN.exists() else OUT_DIR_LOCAL

# 每个 scenario 拼接 (baseline, v2_patch)
# 注意:同一 scenario 只能有一条 ch.jsonl(否则冲突)
# → 策略: 先把 v2 patch 注入 baseline 同名 jsonl,**就地合并**
# 仅 Windows 本地有的 scenario(intents/task 原始 jsonl 不在 Windows)
SCENES_WITH_V2 = {
    "safety":       ["data_safety_ch.jsonl",       "data_safety_v2_ch.jsonl"],
    "tempfile":     ["data_tempfile_ch.jsonl",     "data_tempfile_v2_ch.jsonl"],
    "email":        ["data_email_ch.jsonl",        "data_email_v2_ch.jsonl"],
    "disk_cleanup": ["data_disk_cleanup_ch.jsonl", "data_disk_cleanup_v2_ch.jsonl"],
}

# 仅 baseline 的(intents/task 原始 jsonl 不在 Windows 本地,但 ch.jsonl 从 aliyun 下载回来了)
SCENES_BASELINE = ["log", "perf", "intents", "task"]

out_path = OUT_DIR / "data_unified_ch_v2.jsonl"
total = 0
per_scene = {}

with open(out_path, "w", encoding="utf-8") as fout:
    # v2 patched scenarios
    for scene, files in SCENES_WITH_V2.items():
        n = 0
        for fn in files:
            src = CH_BASE / fn
            if not src.exists():
                print(f"   ⚠️ {scene}: missing {fn}, skipping")
                continue
            with open(src, encoding="utf-8") as fin:
                for line in fin:
                    line = line.strip()
                    if not line:
                        continue
                    fout.write(line + "\n")
                    n += 1
        per_scene[scene] = n
        total += n

    # baseline-only scenarios
    for scene in SCENES_BASELINE:
        src = CH_BASE / f"data_{scene}_ch.jsonl"
        n = 0
        if not src.exists():
            print(f"   ⚠️ {scene}: missing baseline, skipping")
            per_scene[scene] = 0
            continue
        with open(src, encoding="utf-8") as fin:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                fout.write(line + "\n")
                n += 1
        per_scene[scene] = n
        total += n

print(f"✅ unified v2: {total} samples → {out_path}")
for s, n in per_scene.items():
    print(f"   {s:14s}: {n} samples")