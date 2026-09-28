#!/usr/bin/env python3
"""retry_intents_train.py — upload patched train_step1.py then restart train"""
import paramiko, time
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"
LOCAL = "C:/Users/Administrator/oi_enhancements/companion/train_step1.py"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=15)

# 上传(覆盖)
sftp = c.open_sftp()
sftp.put(LOCAL, "/workspace/companion/train_step1.py")
sftp.close()
print("[1/2] uploaded patched train_step1.py")

# 验证
si, so, se = c.exec_command("grep -n 'intents' /workspace/companion/train_step1.py | head -5")
print("[2/2] verify on aliyun:")
print(so.read().decode())

# 重启训练
si, so, se = c.exec_command("cd /workspace && setsid nohup bash run_train_intents_v1.sh > /workspace/run_train_intents_v1.log 2>&1 < /dev/null & echo PID=$!")
print("restart:", so.read().decode().strip())
c.close()