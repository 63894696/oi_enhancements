#!/usr/bin/env python3
"""cleanup_and_teacher.py — kill stale train, wait GPU free, then teacher"""
import paramiko, time
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)

# 杀进程
si, so, se = c.exec_command("pkill -9 -f train_step1.py; sleep 3; pkill -9 -f confidence_teacher.py; sleep 2; nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader")
print("[kill] GPU:")
print(so.read().decode())

# 等 5s 确保释放
time.sleep(5)

# 再跑 teacher
si, so, se = c.exec_command(
    "cd /workspace/companion && python3 confidence_teacher.py "
    "--data /workspace/data_intents.jsonl "
    "--base /workspace/models/Qwen3Guard-Gen-0.6B "
    "--adapter /workspace/qwen3guard-intents/adapter "
    "--schema intents "
    "--output /workspace/data_intents_conf.jsonl 2>&1 | tail -10",
    timeout=900,
)
print("[teacher]:")
print(so.read().decode(errors="replace"))
c.close()