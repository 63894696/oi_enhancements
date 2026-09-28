#!/usr/bin/env python3
"""l3_intents_conf.py — upload patched teacher, generate _conf data, launch conf train"""
import paramiko, time
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=10, banner_timeout=10, auth_timeout=10)

sftp = c.open_sftp()
sftp.put("C:/Users/Administrator/oi_enhancements/companion/confidence_teacher.py",
         "/workspace/companion/confidence_teacher.py")
# 上传 conf 训脚本
script = """#!/bin/bash
# run_train_intents_conf_v1.sh
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Intents_conf v1 train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-intents-conf
python3 /workspace/companion/train_step1.py \\
  --data /workspace/data_intents_conf.jsonl \\
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \\
  --schema intents \\
  --output /workspace/qwen3guard-intents-conf \\
  --epochs 4 --batch 4 2>&1
echo "=== Intents_conf v1 train end $(date) ==="
"""
sftp.putfo(__import__("io").BytesIO(script.encode("utf-8")), "/workspace/run_train_intents_conf_v1.sh")
sftp.close()

# 跑 teacher
si, so, se = c.exec_command(
    "cd /workspace/companion && python3 confidence_teacher.py "
    "--data /workspace/data_intents.jsonl "
    "--base /workspace/models/Qwen3Guard-Gen-0.6B "
    "--adapter /workspace/qwen3guard-intents/adapter "
    "--schema intents "
    "--output /workspace/data_intents_conf.jsonl 2>&1 | tail -10",
    timeout=600,
)
print("[teacher] output:")
print(so.read().decode(errors="replace"))
c.close()