#!/bin/bash
# run_train_perf.sh — aliyun T4 训 perf base adapter
set -e
cd /workspace
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_perf.jsonl \
  --schema perf \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --output /workspace/qwen3guard-perf \
  --epochs 4 \
  --batch 4 \
  --lr 2e-4 \
  --max-len 320
echo "TRAIN_PERF_DONE"