#!/bin/bash
# run_train_log.sh — M3.49 log base 训练(aliyun T4)
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Log base train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-log
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_log.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema log \
  --output /workspace/qwen3guard-log \
  --epochs 4 --batch 4 2>&1
echo "=== Log base train end $(date) ==="