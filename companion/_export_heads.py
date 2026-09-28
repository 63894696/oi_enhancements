#!/usr/bin/env python3
"""_export_heads.py — 从 8 个 final.pt 重新正确拆 head-only(2026-09-24)

修复 v1 wrapper bug:之前 'head' in k 把 state_dict 本身匹配,导致嵌套 dict
正确做法:final.pt['state_dict'] 是 OrderedDict{key: tensor},head param 命名约定:
  - path_encoder.backbone.*  → backbone(冻结,不存)
  - proj_in / set_encoder / proj_out / scorer.*  → head(存)
"""
import sys
import json
import torch
from pathlib import Path

SCENES = ["disk_cleanup", "tempfile", "email", "log", "perf", "intents", "task", "safety"]
CANDIDATES = {
    "disk_cleanup": ["safe", "low", "medium", "high", "critical"],
    "tempfile": ["safe", "low", "medium", "high", "critical"],
    "email": ["safe", "low", "medium", "high", "critical"],
    "log": ["safe", "low", "medium", "high", "critical"],
    "perf": ["safe", "low", "medium", "high", "critical"],
    "intents": ["chat", "code", "search", "tool_call", "roleplay"],
    "task": ["code_call", "code_qa", "creative", "long", "fast", "general"],
    "safety": ["safe", "unsafe"],
}

OUT_BASE = Path("/workspace/agent-jev-output")


def export_one(scene: str) -> dict:
    final_pt = OUT_BASE / f"{scene}_v3" / "final.pt"
    export_pt = OUT_BASE / f"{scene}_v3_export" / f"{scene}_ch_head.pt"
    export_pt.parent.mkdir(parents=True, exist_ok=True)

    ckpt = torch.load(final_pt, map_location="cpu", weights_only=False)
    sd = ckpt["state_dict"]

    # 拆分:head = 不以 path_encoder.backbone 开头的
    head_state = {}
    backbone_state = {}
    for k, v in sd.items():
        if k.startswith("path_encoder.backbone") or k.startswith("tree_encoder.backbone"):
            backbone_state[k] = v
        else:
            head_state[k] = v

    head_n = sum(v.numel() for v in head_state.values() if hasattr(v, "numel"))
    bb_n = sum(v.numel() for v in backbone_state.values() if hasattr(v, "numel"))

    # 写 head-only(用 config + candidates 让本地推理 wrapper 能重建 head)
    out = {
        "state_dict": head_state,
        "config": ckpt.get("config", {}),
        "candidates": CANDIDATES[scene],
        "schema": scene,
        "step": ckpt.get("step"),
    }
    torch.save(out, export_pt)
    size_mb = export_pt.stat().st_size / 1024 / 1024
    return {
        "scene": scene,
        "head_params": head_n,
        "backbone_params": bb_n,
        "head_keys": len(head_state),
        "export_size_mb": round(size_mb, 1),
    }


def main() -> int:
    results = []
    for s in SCENES:
        try:
            r = export_one(s)
            results.append(r)
            print(f"  ✅ {s:12s}: head={r['head_params']/1e6:.2f}M keys={r['head_keys']} "
                  f"size={r['export_size_mb']}MB", file=sys.stderr)
        except Exception as e:
            print(f"  ❌ {s}: {e}", file=sys.stderr)
            results.append({"scene": s, "error": str(e)})

    print(f"\n=== {sum(1 for r in results if 'error' not in r)}/{len(results)} head-only 导出 ===",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())