#!/bin/bash
# run_train_perf_conf.sh — aliyun T4 训 perf_conf adapter
set -e
cd /workspace
python3 /workspace/companion/train_step1.py \
  --data /workspace/data_perf_conf.jsonl \
  --schema perf \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --output /workspace/qwen3guard-perf-conf \
  --epochs 4 \
  --batch 4 \
  --lr 2e-4 \
  --max-len 320
echo "TRAIN_PERF_CONF_DONE"