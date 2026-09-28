#!/usr/bin/env python3
"""_unified_merge.py — 合并 8 ch.jsonl → 1 个 unified 数据集(2026-09-24)

每个 ch.jsonl 一行 = {state, questions:[{text, candidates, gold.distribution}]}
- 不同 scenario 的 candidates 长度不同(2/5/6 个)
- AgentJev CandidateSetEncoder permutation-equivariant:每条内部 candidates 顺序无关
- 合并:直接 concat,保留每条的 candidates

unified 训练一个 head 覆盖所有 18 个 label:
  safe / low / medium / high / critical
  chat / code / search / tool_call / roleplay
  code_call / code_qa / creative / long / fast / general
  unsafe

推理时按场景挑 candidates 子集,model 输出 top。
"""
import json
from pathlib import Path

CH_BASE = Path("/workspace/companion/data/ch_out")
SCENES = ["disk_cleanup", "tempfile", "email", "log", "perf",
          "intents", "task", "safety"]

out_path = CH_BASE.parent / "data_unified_ch.jsonl"
total = 0
per_scene = {}
with open(out_path, "w", encoding="utf-8") as fout:
    for s in SCENES:
        src = CH_BASE / f"data_{s}_ch.jsonl"
        n = 0
        with open(src, encoding="utf-8") as fin:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                fout.write(line + "\n")
                n += 1
                total += 1
        per_scene[s] = n

print(f"✅ unified: {total} samples → {out_path}")
for s, n in per_scene.items():
    print(f"   {s:14s}: {n} samples")