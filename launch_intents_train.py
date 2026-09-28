#!/usr/bin/env python3
"""launch_intents_train.py — use atexit to close channel cleanly"""
import paramiko, time
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=10, banner_timeout=10, auth_timeout=10)

si, so, se = c.exec_command("grep -n 'intents' /workspace/companion/train_step1.py | head -3")
out = so.read().decode()
print("[verify]:", out)

# 用 -tt 给 pty,避免 stuck
si, so, se = c.exec_command("cd /workspace; nohup bash run_train_intents_v1.sh > /workspace/run_train_intents_v1.log 2>&1 & echo PID=$!")
time.sleep(2)
out = so.read(1024).decode()
print("[launch]:", out)

c.close()