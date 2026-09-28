#!/usr/bin/env python3
"""_export_unified.py — M3.66 L3 unified head 单独导出(不分 scenario)

unified 训完 final.pt 包含:
  - path_encoder.backbone.* (596M, frozen)
  - proj_in / set_encoder / proj_out / scorer.* (~2.4M, head)

只存 head + 全 18 candidates,本地推理时按 scenario 挑子集
"""
import sys
import torch
from pathlib import Path

CKPT = Path("/workspace/agent-jev-output/unified_v3/final.pt")
OUT = Path("/workspace/agent-jev-output/unified_v3_export/unified_ch_head.pt")

# 18 candidates = 5 risk ∪ 5 intent ∪ 6 task ∪ 2 safety ∪ 1 unsafe 重复(已在 risk/safety 覆盖)
ALL_CANDIDATES = [
    "safe", "low", "medium", "high", "critical",            # 5 risk (also safety uses safe)
    "chat", "code", "search", "tool_call", "roleplay",       # 5 intent
    "code_call", "code_qa", "creative", "long", "fast", "general",  # 6 task
    "unsafe",                                                # safety 专属
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
        "schema": "unified",  # 特殊标记
        "head_params": head_n,
        "step": ckpt.get("step"),
    }
    torch.save(out, OUT)
    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"✅ unified head-only → {OUT}")
    print(f"   head params: {head_n/1e6:.2f}M | backbone(frozen,未存): {bb_n/1e6:.1f}M")
    print(f"   candidates: {len(ALL_CANDIDATES)}")
    print(f"   export size: {size_mb:.1f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())