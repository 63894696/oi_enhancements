#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# download_assets_local.py — 本机下载模型权重 + 数据集(2026-09-22)
#
# 目的:
#   - 方案 E 的「本机中转」环节:在本地把训练要用的所有素材下好
#   - 模型权重走 ModelScope 主 / hf-mirror 备
#   - 数据集(hh-rlhf / oasst1)走 hf-mirror
#   - 下完后 tar 打包,scp 到实例
#
# 用法(本地):
#   cd companion
#   python download_assets_local.py --output-dir D:/prisir-train-assets
#
# 输出:
#   D:/prisir-train-assets/
#     models/Qwen3Guard-Gen-0.6B/    # ~400MB INT4 / ~1.2GB FP16
#     data/data_step1.jsonl          # 训练数据
#     assets.tar.gz                  # 全部打包,便于 scp
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def download_via_modelscope(model_id: str, target_dir: Path):
    """ModelScope SDK 下载(国内主通道)。"""
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("  modelscope 未装,装一下:")
        subprocess.run([sys.executable, "-m", "pip", "install", "modelscope",
                        "-i", "https://mirrors.aliyun.com/pypi/simple/"],
                       check=False)
        from modelscope import snapshot_download
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    p = snapshot_download(model_id, cache_dir=str(target_dir.parent),
                          revision="master")
    # 移到 target_dir(去掉中间 cache 命名)
    if Path(p).resolve() != target_dir.resolve():
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(p, target_dir)
    return target_dir


def download_via_hf_mirror(model_id: str, target_dir: Path):
    """hf-mirror.com 备通道(走 huggingface_hub)。"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from huggingface_hub import snapshot_download
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    p = snapshot_download(repo_id=model_id, cache_dir=str(target_dir.parent),
                          revision="main",
                          allow_patterns=["*.json", "*.txt", "*.safetensors",
                                          "*.bin", "tokenizer*", "*.model"])
    if Path(p).resolve() != target_dir.resolve():
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(p, target_dir)
    return target_dir


def main() -> int:
    ap = argparse.ArgumentParser(description="本机下载训练素材")
    ap.add_argument("--output-dir", default="D:/prisir-train-assets",
                    help="资产存放目录")
    ap.add_argument("--model-id", default="Qwen/Qwen3Guard-Gen-0.6B")
    ap.add_argument("--skip-model", action="store_true",
                    help="已下载则跳过")
    ap.add_argument("--skip-data", action="store_true")
    args = ap.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 1) 模型权重 — ModelScope 主, hf-mirror 备
    if not args.skip_model:
        model_dir = out / "models" / Path(args.model_id).name
        if model_dir.exists() and any(model_dir.iterdir()):
            print(f"[skip] 模型已存在: {model_dir}")
        else:
            print(f"[1/3] 下载模型: {args.model_id}")
            for fn_name, fn in [("modelscope", download_via_modelscope),
                                ("hf-mirror", download_via_hf_mirror)]:
                try:
                    print(f"  try {fn_name}...")
                    download_via_modelscope(args.model_id, model_dir)
                    print(f"  ✅ {fn_name} 成功: {model_dir}")
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"  {fn_name} fail: {type(e).__name__}: "
                          f"{str(e)[:120]}")
                    continue
            else:
                print("  ❌ 两通道都失败")
                return 1
    else:
        print("[skip-model] 跳过模型下载")

    # 2) 训练数据
    if not args.skip_data:
        print("[2/3] 生成训练数据 data_step1.jsonl")
        data_dir = out / "data"
        data_dir.mkdir(exist_ok=True)
        data_jsonl = data_dir / "data_step1.jsonl"
        # 调 data_prep_step1.main()
        sys.path.insert(0, str(_HERE))
        import data_prep_step1
        data_prep_step1.main.__globals__["__file__"] = str(_HERE / "data_prep_step1.py")
        old_argv = sys.argv
        sys.argv = ["data_prep_step1.py", "--output", str(data_jsonl)]
        try:
            data_prep_step1.main()
        finally:
            sys.argv = old_argv
        print(f"  ✅ {data_jsonl} ({data_jsonl.stat().st_size/1024:.1f} KB)")
    else:
        print("[skip-data] 跳过数据生成")

    # 3) 打包
    print("[3/3] tar 打包便于 scp")
    tar_path = out / "assets.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for sub in ["models", "data"]:
            sub_path = out / sub
            if sub_path.exists():
                tf.add(sub_path, arcname=sub)
    size_mb = tar_path.stat().st_size / 1024**2
    print(f"  ✅ {tar_path} ({size_mb:.1f} MB)")
    print()
    print("=== 下一步 ===")
    print(f"  1. 启实例: ./aliyun_train_cn.sh start")
    print(f"  2. 等 RUNNING,scp 上传:")
    print(f"     scp {tar_path} root@<ip>:/workspace/")
    print(f"  3. SSH 上实例,解压 + 训练:")
    print(f"     ssh root@<ip>")
    print(f"     cd /workspace && tar -xzf assets.tar.gz")
    print(f"     source /opt/prisirt-venv/bin/activate")
    print(f"     python train_step1.py \\")
    print(f"       --data /workspace/data/data_step1.jsonl \\")
    print(f"       --base-model /workspace/models/Qwen3Guard-Gen-0.6B \\")
    print(f"       --epochs 3 --batch 4 --quant-gguf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())