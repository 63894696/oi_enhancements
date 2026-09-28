#!/bin/bash
# run_train_step2_conf_v2.sh — M3.46 tempfile_conf 重训 v2:6 epochs + 显式拼接
# 修复 v1 输出格式崩溃(parse fail 94%)。loss 2.18→0.27 应更稳定
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Step 2 (conf v2) train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_tempfile_conf.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema tempfile \
  --output /workspace/qwen3guard-tempfile-conf-v2 \
  --epochs 6 --batch 4 --lr 1e-4 2>&1
echo "=== Step 2 (conf v2) train end $(date) ==="