#!/bin/bash
# _v3_train_all.sh — M3.64 B3 4 个 v3_conf adapter 训练(2026-09-24)
# 在 aliyun T4 上跑,基于 v3_critical.py 产出的 4 个 *_v3_conf.jsonl
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128

BASE_MODEL=/workspace/base_model
DATA_DIR=/workspace/companion/data
OUT_BASE=/workspace

train_one() {
  local SCENE=$1
  local DATA_FILE=$2
  local OUT_NAME=$3

  echo ""
  echo "=========================================="
  echo "=== $SCENE conf_v3 训练 $(date) ==="
  echo "=========================================="
  nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
  rm -rf "${OUT_BASE}/${OUT_NAME}"

  python3 /workspace/companion/train_step1.py \
    --data ${DATA_DIR}/${DATA_FILE} \
    --base-model ${BASE_MODEL} \
    --schema ${SCENE} \
    --output ${OUT_BASE}/${OUT_NAME} \
    --epochs 4 --batch 4 2>&1 | tee ${OUT_BASE}/train_${OUT_NAME}.log

  echo "=== $SCENE conf_v3 完成 $(date) ==="
  ls -la ${OUT_BASE}/${OUT_NAME} 2>&1 | tail -5
}

# 4 个 scenario 串行训
train_one disk_cleanup data_disk_cleanup_v3_conf.jsonl qwen3guard-disk-cleanup-conf-v3
train_one tempfile     data_tempfile_v3_conf.jsonl     qwen3guard-tempfile-conf-v3
train_one email        data_email_v3_conf.jsonl        qwen3guard-email-conf-v3
train_one log          data_log_v3_conf.jsonl          qwen3guard-log-conf-v3

echo ""
echo "==========================================="
echo "=== ALL 4 v3_conf 训练结束 $(date) ==="
echo "==========================================="
ls -la /workspace/qwen3guard-*-conf-v3/adapter/adapter_model.safetensors 2>&1