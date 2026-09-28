#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# download_adapter_tempfile_conf.py — 把 tempfile_conf adapter 从 aliyun 下载到本地
# 用 paramiko SFTP,避免 scp 截断 tokenizer.json(11MB)
from __future__ import annotations

import os
import sys
import paramiko
from pathlib import Path

ALIYUN_HOST = "43.106.53.242"
ALIYUN_USER = "root"
ALIYUN_PASS = os.environ.get("ALIYUN_SSH_PASS", "PrisirTrain2026!")
REMOTE_ADAPTER_DIR = "/workspace/qwen3guard-tempfile-conf/adapter"
LOCAL_TARGET = Path("D:/prisir-train-assets/trained/tempfile_conf/adapter")

def main() -> int:
    LOCAL_TARGET.mkdir(parents=True, exist_ok=True)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ALIYUN_HOST, username=ALIYUN_USER, password=ALIYUN_PASS,
                   timeout=15)
    sftp = client.open_sftp()

    files = sftp.listdir(REMOTE_ADAPTER_DIR)
    print(f"[1/2] 远端 {len(files)} 个文件待下载:")
    for f in sorted(files):
        print(f"  - {f}")

    for fname in files:
        rpath = f"{REMOTE_ADAPTER_DIR}/{fname}"
        lpath = LOCAL_TARGET / fname
        print(f"  ↓ {fname} ...", end=" ", flush=True)
        try:
            sftp.get(rpath, str(lpath))
            size = lpath.stat().st_size
            print(f"OK ({size//1024} KB)")
        except Exception as e:
            print(f"FAIL: {e}")
            sys.exit(1)

    sftp.close()
    client.close()
    print(f"\n[2/2] 本地 adapter 落盘: {LOCAL_TARGET}")
    print(f"总大小: {sum(f.stat().st_size for f in LOCAL_TARGET.iterdir())//1024} KB")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())