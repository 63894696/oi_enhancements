#!/usr/bin/env python3
"""upload_bench.py — upload bench_intents_local.py to aliyun"""
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('43.106.53.242', username='root', password='PrisirTrain2026!', timeout=10, banner_timeout=10, auth_timeout=10)
sftp = c.open_sftp()
sftp.put("C:/Users/Administrator/oi_enhancements/companion/bench_intents_local.py",
         "/workspace/companion/bench_intents_local.py")
sftp.put("C:/Users/Administrator/oi_enhancements/companion/classify_intents.py",
         "/workspace/companion/classify_intents.py")
sftp.put("C:/Users/Administrator/oi_enhancements/companion/adapter_registry.py",
         "/workspace/companion/adapter_registry.py")
sftp.close()
print("UPLOADED 3 files")
c.close()