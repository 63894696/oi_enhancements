#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# local_train_smoke.py — 1060 3GB 本机 PoC 烟雾测试(2026-09-22)
#
# 目的:
#   - 不下载模型权重,直接探测 VRAM 容量 + bitsandbytes 4bit 可用性
#   - 测完后会输出:能跑 / 不能跑 + 余量多少
#   - 预计 < 30s 完成(不需要装 transformers)
#
# 用法:
#   python local_train_smoke.py
from __future__ import annotations

import sys
import platform
from pathlib import Path


def check_python():
    print(f"[1/6] Python: {sys.version.split()[0]} ({platform.platform()})")
    if sys.version_info < (3, 9):
        print("  ⚠ 建议 ≥ 3.10")


def check_torch():
    try:
        import torch
    except ImportError:
        print("[2/6] PyTorch: ❌ 未装")
        return False
    print(f"[2/6] PyTorch: {torch.__version__}")
    print(f"  CUDA 可用: {torch.cuda.is_available()}")
    if not torch.cuda.is_available():
        return False
    print(f"  CUDA 版本: {torch.version.cuda}")
    print(f"  设备数: {torch.cuda.device_count()}")
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        total_gb = props.total_memory / 1024**3
        print(f"  GPU {i}: {props.name}  显存 {total_gb:.1f} GB  "
              f"compute {props.major}.{props.minor}")
    return True


def check_vram():
    """真占显存测容量 — 关键一步,1060 3GB 可能被 OS/Win 偷 200-400MB"""
    import torch
    if not torch.cuda.is_available():
        return 0.0
    torch.cuda.empty_cache()
    free_b, total_b = torch.cuda.mem_get_info(0)
    free_gb = free_b / 1024**3
    total_gb = total_b / 1024**3
    used_gb = total_gb - free_gb
    print(f"[3/6] 显存: 总 {total_gb:.2f} GB  "
          f"已用 {used_gb:.2f} GB  可用 {free_gb:.2f} GB")
    return free_gb


def check_bitsandbytes():
    try:
        import bitsandbytes as bnb
    except ImportError:
        print("[4/6] bitsandbytes: ❌ 未装(4bit 训练必需)")
        return False
    print(f"[4/6] bitsandbytes: {bnb.__version__}")
    return True


def check_transformers_peft():
    try:
        import transformers
        import peft
    except ImportError as e:
        print(f"[5/6] transformers/peft: ❌ {type(e).__name__}: {e}")
        return False
    print(f"[5/6] transformers: {transformers.__version__}")
    print(f"         peft:       {peft.__version__}")
    return True


def check_disk():
    """模型权重 + 数据 + checkpoint 至少 3GB 空间"""
    import shutil
    free_gb = shutil.disk_usage(Path.cwd().anchor).free / 1024**3
    print(f"[6/6] 磁盘: 可用 {free_gb:.1f} GB "
          f"(cwd={Path.cwd()})")
    return free_gb >= 3.0


def verdict(free_vram_gb: float, has_bb: bool, has_libs: bool) -> str:
    print("\n" + "=" * 60)
    print(" PoC 判定:")
    print("=" * 60)
    # 4bit + LoRA 0.6B:
    #   - 模型权重 ~0.4 GB(INT4)
    #   - LoRA 梯度 + 优化器 ~0.2 GB
    #   - 激活值 batch=4 seq=512 ~0.5 GB
    #   - CUDA context + 框架 ~0.8 GB
    #   - 安全余量 0.5 GB
    #   合计: ~2.4 GB
    need = 2.4
    print(f"  4bit + LoRA 0.6B 训练实测需要 ≈ {need:.1f} GB 显存")
    if not has_bb:
        print("  ❌ bitsandbytes 未装 → 无法 4bit 加载")
        print("     解:pip install bitsandbytes")
        return "BLOCKED_LIB"
    if not has_libs:
        print("  ❌ transformers/peft 未装 → 无法 LoRA 训练")
        print("     解:pip install transformers peft accelerate")
        return "BLOCKED_LIB"
    if free_vram_gb < need:
        print(f"  ❌ 显存不足:可用 {free_vram_gb:.2f} GB < 需要 {need:.1f} GB")
        print("     → 必须走 cn-hongkong 实例路径")
        return "BLOCKED_VRAM"
    margin = free_vram_gb - need
    print(f"  ✅ 能跑:余量 {margin:.2f} GB")
    if margin < 0.5:
        print(f"  ⚠ 余量偏紧,建议:")
        print(f"     - batch=2(不 4)")
        print(f"     - max_len=256(不 512)")
        print(f"     - 关所有其他 GPU 进程")
        return "OK_TIGHT"
    print(f"  ✅ 余量充足,直接跑全 batch=4 / max_len=512")
    return "OK"


def main() -> int:
    print("=== 1060 3GB 本机 PoC 烟雾测试 ===\n")
    check_python()
    has_cuda = check_torch()
    free_vram = check_vram() if has_cuda else 0.0
    has_bb = check_bitsandbytes()
    has_libs = check_transformers_peft()
    check_disk()
    if not has_cuda:
        print("\n❌ 没有 CUDA — 本机 PoC 不成立,直接走实例")
        return 0
    verdict(free_vram, has_bb, has_libs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())