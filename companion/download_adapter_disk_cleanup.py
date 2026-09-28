#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# download_adapter_disk_cleanup.py — M3.47 Step 1 disk_cleanup adapter 下载
# 用 paramiko SFTP 避免 scp 被 prisir_ime log 干扰
import os
import sys
import paramiko

IP = "43.106.53.242"
PASSWORD = os.environ.get("SSHPASS", "")
USER = "root"
REMOTE_DIR = "/workspace/qwen3guard-disk-cleanup/adapter"
LOCAL_DIR = r"D:\prisir-train-assets\trained\disk_cleanup\adapter"

if not PASSWORD:
    print("ERROR: SSHPASS env not set")
    sys.exit(1)

os.makedirs(LOCAL_DIR, exist_ok=True)

print(f"[1/3] connect {IP} via paramiko...")
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(IP, username=USER, password=PASSWORD, timeout=30)

sftp = client.open_sftp()
print(f"[2/3] list {REMOTE_DIR}...")


def listdir(sftp, path):
    files = []
    for attr in sftp.listdir_attr(path):
        full = f"{path}/{attr.filename}".replace("//", "/")
        if attr.filename in (".", ".."):
            continue
        files.append((full, attr))
    return files


for rpath, attr in listdir(sftp, REMOTE_DIR):
    # 跳过目录
    if attr.st_mode and (attr.st_mode & 0o170000) == 0o040000:
        continue
    fname = os.path.basename(rpath)
    local = os.path.join(LOCAL_DIR, fname)
    print(f"  ↓ {fname} ({attr.st_size/1024:.0f}KB)")
    sftp.get(rpath, local)

sftp.close()
client.close()
print(f"[3/3] ✅ done → {LOCAL_DIR}")