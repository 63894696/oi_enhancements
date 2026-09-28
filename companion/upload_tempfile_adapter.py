#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 paramiko SFTP 重新上传 tempfile adapter"""
import os
import paramiko

IP = "43.106.53.242"
PASSWORD = os.environ["SSHPASS"]
USER = "root"
LOCAL_DIR = r"D:\prisir-train-assets\trained\tempfile\adapter"
REMOTE_DIR = "/workspace/qwen3guard-tempfile/adapter"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(IP, username=USER, password=PASSWORD, timeout=30)
sftp = client.open_sftp()

for name in ["adapter_config.json", "adapter_model.safetensors",
             "tokenizer.json", "tokenizer_config.json",
             "chat_template.jinja", "README.md"]:
    local = os.path.join(LOCAL_DIR, name)
    remote = f"{REMOTE_DIR}/{name}"
    if not os.path.exists(local):
        print(f"  - skip {name} (no local)")
        continue
    size = os.path.getsize(local)
    print(f"  ↑ {name} ({size/1024:.0f}KB)")
    sftp.put(local, remote)

sftp.close()
client.close()
print("done")