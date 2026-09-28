#!/usr/bin/env python3
"""_ch_train_one.py — 单个 AgentJev head 训练(冻结 backbone + bf16)(2026-09-24)

修复 train.py fp32 OOM:
1. backbone 改 bf16 加载(从 2.4GB → 1.2GB)
2. backbone params 设 requires_grad=False(AdamW state 从 596M 缩到 ~2.4M)
"""
import sys
import torch
sys.path.insert(0, "/workspace/agent-jev-repo")

# Monkey-patch:在 AgentJevModel __init__ 中强制 bf16 + freeze backbone
from agentjev.model import AgentJevModel

_orig_init = AgentJevModel.__init__

def _patched_init(self, *args, **kwargs):
    # 强制 bf16 backbone(默认 fp32 太重)
    kwargs["dtype"] = torch.bfloat16
    _orig_init(self, *args, **kwargs)
    # 冻结 backbone 所有 params(只有 set_encoder + head 训)
    n_frozen = 0
    n_trainable = 0
    for name, p in self.named_parameters():
        if name.startswith("path_encoder.backbone") or name.startswith("tree_encoder.backbone"):
            p.requires_grad = False
            n_frozen += p.numel()
        else:
            n_trainable += p.numel()
    print(f"[freeze] backbone frozen: {n_frozen/1e6:.1f}M (bf16) | head trainable: {n_trainable/1e6:.2f}M")

AgentJevModel.__init__ = _patched_init

# 现在调 train.main (会触发 patch)
from agentjev.train import main as train_main
train_main()