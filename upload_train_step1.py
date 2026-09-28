#!/usr/bin/env python3
"""upload_train_step1.py — just upload, no exec_command, no sleep"""
import paramiko
ALIYUN = "43.106.53.242"
PASS = "PrisirTrain2026!"
LOCAL = "C:/Users/Administrator/oi_enhancements/companion/train_step1.py"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN, username="root", password=PASS, timeout=10, banner_timeout=10, auth_timeout=10)
sftp = c.open_sftp()
sftp.put(LOCAL, "/workspace/companion/train_step1.py")
sftp.close()
print("UPLOADED")
c.close()