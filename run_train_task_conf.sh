#!/bin/bash
# run_train_task_conf.sh — M3.58 task_conf 训练(aliyun T4)
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Task confidence teacher start $(date) ==="
rm -f /workspace/data_task_conf.jsonl
python3 /workspace/companion/confidence_teacher.py \
  --data /workspace/data_task.jsonl \
  --base /workspace/models/Qwen3Guard-Gen-0.6B \
  --adapter /workspace/qwen3guard-task/adapter \
  --schema task \
  --output /workspace/data_task_conf.jsonl 2>&1
echo "=== Task confidence teacher end $(date) ==="
echo "=== Task_conf train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-task_conf
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_task_conf.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema task \
  --output /workspace/qwen3guard-task_conf \
  --epochs 4 --batch 1 --max-len 4096 2>&1
echo "=== Task_conf train end $(date) ==="
