#!/usr/bin/env python3
# launch_email_v2.py — 用 paramiko 启动 email v2 训练(避免 sshpass 密码问题)
import os, paramiko, time

ALIYUN = "43.106.53.242"
USER = "root"
PASS = os.environ.get("ALIYUN_SSH_PASS", "PrisirTrain2026!")

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(ALIYUN, username=USER, password=PASS, timeout=15)
print("[1/3] SSH OK")

# 启动训练
cmd = ("setsid nohup bash /workspace/run_train_email_v2.sh "
       "> /workspace/run_train_email_v2.log 2>&1 < /dev/null &")
print(f"[2/3] exec: {cmd!r}")
stdin, stdout, stderr = client.exec_command(cmd)
stdout.read()
stderr.read()
print("  launched")

# 给 sleep(让进程起来)
time.sleep(5)

# 验证进程在跑
verify_cmd = "ps -ef | grep -E 'train_step1|python3' | grep -v grep | head -5"
stdin, stdout, stderr = client.exec_command(verify_cmd)
print("[3/3] 进程检查:")
print(stdout.read().decode() or "  (no python process found)")

# 看 log 头几行
log_cmd = "head -20 /workspace/run_train_email_v2.log 2>&1"
stdin, stdout, stderr = client.exec_command(log_cmd)
print("\n[log] /workspace/run_train_email_v2.log:")
print(stdout.read().decode())

client.close()
print("\n✅ done — disconnect safe; training will continue in setsid group")