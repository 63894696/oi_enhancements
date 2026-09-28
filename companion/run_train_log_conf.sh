#!/bin/bash
# run_train_log_conf.sh — M3.49 log_conf 训练(带 confidence)
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Log conf train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-log-conf
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_log_conf.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema log \
  --output /workspace/qwen3guard-log-conf \
  --epochs 4 --batch 4 2>&1
echo "=== Log conf train end $(date) ==="