#!/bin/bash
# run_train_perf_v2.sh — 用 v2 数据集(645 base + 400 抽象模式 = 1045 条)重训 perf adapter
cd /workspace
setsid nohup python3 /workspace/companion/train_step1.py \
  --data /workspace/companion/data/data_perf_v2.jsonl \
  --schema perf \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --output /workspace/qwen3guard-perf-v2 \
  --epochs 4 --batch 4 --lr 2e-4 --max-len 320 \
  > /workspace/train_perf_v2.log 2>&1 &
echo "PID=$!"