#!/usr/bin/env python3
import paramiko, time
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)

# 验证 _conf 已生成
si, so, se = c.exec_command("ls -la /workspace/data_intents_conf.jsonl 2>&1; wc -l /workspace/data_intents_conf.jsonl 2>&1")
print("[_conf file]:")
print(so.read().decode())

# 启 intents_conf 训练
si, so, se = c.exec_command(
    "cd /workspace; nohup bash run_train_intents_conf_v1.sh > /workspace/run_train_intents_conf_v1.log 2>&1 & echo PID=$!"
)
time.sleep(2)
out = so.read(1024).decode()
print("[launch]:", out)
c.close()