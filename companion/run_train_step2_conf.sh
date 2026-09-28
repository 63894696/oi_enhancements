#!/bin/bash
# run_train_step2_conf.sh — M3.46 tempfile 带 confidence 训练
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Step 2 (conf) train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_tempfile_conf.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema tempfile \
  --output /workspace/qwen3guard-tempfile-conf \
  --epochs 4 --batch 4 2>&1
echo "=== Step 2 (conf) train end $(date) ==="