#!/usr/bin/env python3
"""sync_intents_data.py — 把本地 data_intents.jsonl 上传到 aliyun"""
import paramiko, io
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=15)
sftp = c.open_sftp()

local = "C:/Users/Administrator/oi_enhancements/companion/data_intents.jsonl"
print(f"[1/2] upload {local} → /workspace/data_intents.jsonl")
sftp.put(local, "/workspace/data_intents.jsonl")

print(f"[2/2] verify aliyun side")
si, so, se = c.exec_command("ls -la /workspace/data_intents.jsonl && wc -l /workspace/data_intents.jsonl && head -1 /workspace/data_intents.jsonl")
print(so.read().decode(errors="replace"))

sftp.close()
c.close()