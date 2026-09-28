#!/bin/bash
# run_train_step2.sh — M3.46 Step 2 tempfile 训练(在新加坡 T4 实例上跑)
#
# 与 run_train.sh 的差别:
#   - schema=tempfile(用 ChatML 格式 + action 字段)
#   - epochs=4(数据集 266 条比 Step 1 大 5x,多一轮)
#   - 输出 outputs/qwen3guard-tempfile/
#
# 用法(在新加坡 T4 实例上):
#   cd /workspace/companion
#   nohup bash run_train_step2.sh > /var/log/prisirt-train.log 2>&1 &
set -e
cd /workspace
export HF_ENDPOINT=https://hf-mirror.com
export TRANSFORMERS_OFFLINE=1
export HF_HUB_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Step 2 train start $(date) ==="
nvidia-smi
cd /workspace/companion
python3 train_step1.py \
  --data /workspace/companion/data_tempfile.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --schema tempfile \
  --output /workspace/qwen3guard-tempfile \
  --epochs 4 --batch 4 2>&1 | tee -a /var/log/prisirt-train.log
echo "=== Step 2 train end $(date) ==="