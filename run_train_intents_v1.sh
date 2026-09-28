#!/bin/bash
# run_train_intents_v1.sh — M3.50 L2 intents base 训练(ChatML 模板继承自 M3.49 L6)
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Intents base v1 (ChatML) train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-intents
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_intents.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema intents \
  --output /workspace/qwen3guard-intents \
  --epochs 4 --batch 4 2>&1
echo "=== Intents base v1 train end $(date) ==="