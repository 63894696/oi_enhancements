#!/bin/bash
# run_train_perf_conf_v2.sh — 训 perf_conf_v2 adapter
cd /workspace
setsid nohup python3 /workspace/companion/train_step1.py \
  --data /workspace/companion/data/data_perf_v2_conf.jsonl \
  --schema perf \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --output /workspace/qwen3guard-perf-conf-v2 \
  --epochs 4 --batch 4 --lr 2e-4 --max-len 320 \
  > /workspace/train_perf_conf_v2.log 2>&1 &
echo "PID=$!"