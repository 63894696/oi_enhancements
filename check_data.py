#!/usr/bin/env python3
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)

# 1. _conf 文件是否生成
si, so, se = c.exec_command("ls -la /workspace/data_intents_conf.jsonl 2>&1; head -2 /workspace/data_intents_conf.jsonl 2>&1")
print("[1] data_intents_conf.jsonl:")
print(so.read().decode(errors="replace"))

# 2. GPU 占用
si, so, se = c.exec_command("nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader")
print("[2] GPU:")
print(so.read().decode())

# 3. 训练是否还在跑
si, so, se = c.exec_command("ps aux | grep -E 'train_step1|confidence_teacher' | grep -v grep")
print("[3] running processes:")
print(so.read().decode(errors="replace"))
c.close()