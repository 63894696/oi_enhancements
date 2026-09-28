#!/bin/bash
# run_train_intents_conf_v1.sh — M3.50 L3 intents_conf 训练
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Intents_conf v1 (ChatML) train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-intents-conf
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_intents_conf.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema intents \
  --output /workspace/qwen3guard-intents-conf \
  --epochs 4 --batch 4 2>&1
echo "=== Intents_conf v1 train end $(date) ==="