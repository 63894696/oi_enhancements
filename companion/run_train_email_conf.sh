#!/bin/bash
# run_train_email_conf.sh — M3.48 email_conf 重训(带 confidence)
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Email conf train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_email_conf.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema email \
  --output /workspace/qwen3guard-email-conf \
  --epochs 4 --batch 4 2>&1
echo "=== Email conf train end $(date) ==="