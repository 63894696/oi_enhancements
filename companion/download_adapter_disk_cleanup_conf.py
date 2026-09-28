#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 paramiko 下载 disk_cleanup_conf adapter"""
import os
import paramiko

IP = "43.106.53.242"
PASSWORD = os.environ.get("SSHPASS", "")
USER = "root"
REMOTE_DIR = "/workspace/qwen3guard-disk-cleanup-conf/adapter"
LOCAL_DIR = r"D:\prisir-train-assets\trained\disk_cleanup_conf\adapter"

if not PASSWORD:
    raise SystemExit("SSHPASS not set")

os.makedirs(LOCAL_DIR, exist_ok=True)

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(IP, username=USER, password=PASSWORD, timeout=30)
sftp = client.open_sftp()

for attr in sftp.listdir_attr(REMOTE_DIR):
    if attr.filename in (".", ".."):
        continue
    if attr.st_mode and (attr.st_mode & 0o170000) == 0o040000:
        continue
    rpath = f"{REMOTE_DIR}/{attr.filename}"
    lpath = os.path.join(LOCAL_DIR, attr.filename)
    print(f"  ↓ {attr.filename} ({attr.st_size/1024:.0f}KB)")
    sftp.get(rpath, lpath)

sftp.close()
client.close()
print(f"done → {LOCAL_DIR}")