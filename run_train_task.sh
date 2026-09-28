#!/bin/bash
# run_train_task.sh — M3.58 task 6 类训练(aliyun T4)
#   task = code_call / code_qa / creative / long / fast / general
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Task base train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-task
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_task.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema task \
  --output /workspace/qwen3guard-task \
  --epochs 4 --batch 1 --max-len 4096 2>&1
echo "=== Task base train end $(date) ==="
