#!/bin/bash
# run_teacher_perf.sh — confidence_teacher 生成 data_perf_conf.jsonl
set -e
cd /workspace
python3 /workspace/companion/confidence_teacher.py \
  --data /workspace/data_perf.jsonl \
  --base /workspace/models/Qwen3Guard-Gen-0.6B \
  --adapter /workspace/qwen3guard-perf/adapter \
  --output /workspace/data_perf_conf.jsonl \
  --schema perf
echo "TEACHER_PERF_DONE"