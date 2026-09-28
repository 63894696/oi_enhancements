#!/usr/bin/env python3
"""upload_and_train_intents.py — 把训练脚本上传 aliyun + 后台启训练"""
import paramiko, io, time
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=15)
sftp = c.open_sftp()

# 上传 train 脚本(aliyun 上需可执行)
script = """#!/bin/bash
# run_train_intents_v1.sh
set -e
cd /workspace/companion
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
echo "=== Intents base v1 train start $(date) ==="
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
rm -rf /workspace/qwen3guard-intents
python3 /workspace/companion/train_step1.py \\
  --data /workspace/data_intents.jsonl \\
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \\
  --schema intents \\
  --output /workspace/qwen3guard-intents \\
  --epochs 4 --batch 4 2>&1
echo "=== Intents base v1 train end $(date) ==="
"""
sftp.putfo(io.BytesIO(script.encode("utf-8")), "/workspace/run_train_intents_v1.sh")
si, so, se = c.exec_command("chmod +x /workspace/run_train_intents_v1.sh && ls -la /workspace/run_train_intents_v1.sh")
print(so.read().decode())

# 启后台训练
print("[*] launching intents base train (setsid nohup)...")
si, so, se = c.exec_command(
    "cd /workspace && setsid nohup bash run_train_intents_v1.sh > /workspace/run_train_intents_v1.log 2>&1 < /dev/null & echo PID=$!"
)
print(so.read().decode().strip())

# 等 8 秒看是否起得来
time.sleep(8)
si, so, se = c.exec_command("ps aux | grep -E 'train_step1' | grep -v grep && echo --- && tail -20 /workspace/run_train_intents_v1.log")
print(so.read().decode(errors="replace"))

sftp.close()
c.close()