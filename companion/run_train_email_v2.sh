#!/bin/bash
# run_train_email_v2.sh — M3.48.1 email base 重训(v2 加 50 条 mix 边界样本)
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Email base train v2 start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-email
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_email.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema email \
  --output /workspace/qwen3guard-email \
  --epochs 4 --batch 4 2>&1
echo "=== Email base train v2 end $(date) ==="