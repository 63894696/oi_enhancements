#!/bin/bash
# _unified_train_v2.sh — M3.66+ unified v2 训练启动(2026-09-24)
# 5498 samples × 3000 steps × bs=2×grad_accum=4 = ~90 min @ T4
set -e
cd /workspace/agent-jev-repo

# 找 python(优先 venv,其次系统)
if [ -f venv/bin/python ]; then
    PY=venv/bin/python
elif [ -f /workspace/agent-jev-venv/bin/python ]; then
    PY=/workspace/agent-jev-venv/bin/python
else
    PY=$(which python3 || which python)
fi
echo "Using PY=$PY"
$PY -c "import torch; print('torch:', torch.__version__, 'cuda:', torch.cuda.is_available())"

# OOM 防御:启用可扩展段 + 缓存清理
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

mkdir -p /workspace/agent-jev-output/unified_v3_v2

nohup $PY -m agentjev.train \
    --config configs/unified_v3_v2.yaml \
    > /workspace/agent-jev-output/unified_v3_v2/stdout.log 2>&1 &

TRAIN_PID=$!
echo "训练 PID: $TRAIN_PID"
echo "stdout: /workspace/agent-jev-output/unified_v3_v2/stdout.log"
echo "log:    /workspace/agent-jev-output/unified_v3_v2/log.jsonl"
echo "checkpoint: /workspace/agent-jev-output/unified_v3_v2/"