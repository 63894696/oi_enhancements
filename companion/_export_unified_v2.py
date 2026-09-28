#!/usr/bin/env python3
"""_export_unified_v2.py — M3.66+ unified v2 head-only 导出

导出 v2 训完 final.pt → head-only 9MB,本地推理时按 scenario 挑子集
"""
import sys
import torch
from pathlib import Path

CKPT = Path("/workspace/agent-jev-output/unified_v3_v2/final.pt")
OUT = Path("/workspace/agent-jev-output/unified_v3_v2_export/unified_ch_v2_head.pt")

ALL_CANDIDATES = [
    "safe", "low", "medium", "high", "critical",
    "chat", "code", "search", "tool_call", "roleplay",
    "code_call", "code_qa", "creative", "long", "fast", "general",
    "unsafe",
]


def main() -> int:
    if not CKPT.exists():
        print(f"❌ final.pt 不存在: {CKPT}")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    ckpt = torch.load(CKPT, map_location="cpu", weights_only=False)
    sd = ckpt["state_dict"]

    head_state = {}
    backbone_state = {}
    for k, v in sd.items():
        if k.startswith("path_encoder.backbone") or k.startswith("tree_encoder.backbone"):
            backbone_state[k] = v
        else:
            head_state[k] = v

    head_n = sum(v.numel() for v in head_state.values() if hasattr(v, "numel"))
    bb_n = sum(v.numel() for v in backbone_state.values() if hasattr(v, "numel"))

    out = {
        "state_dict": head_state,
        "candidates": ALL_CANDIDATES,
        "schema": "unified_v2",
        "head_params": head_n,
        "step": ckpt.get("step"),
        "data_path": "/workspace/companion/data/data_unified_ch_v2.jsonl",
        "samples": 5498,
        "max_steps": 3000,
    }
    torch.save(out, OUT)
    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"✅ unified v2 head-only → {OUT}")
    print(f"   head params: {head_n/1e6:.2f}M | backbone(frozen,未存): {bb_n/1e6:.1f}M")
    print(f"   candidates: {len(ALL_CANDIDATES)}")
    print(f"   export size: {size_mb:.1f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())