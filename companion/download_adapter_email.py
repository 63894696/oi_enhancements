#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# download_adapter_email.py — 把 email + email_conf adapter 从 aliyun 下载到本地
# 用 paramiko SFTP(避免 scp 截断 tokenizer.json)
from __future__ import annotations

import os
import sys
import paramiko
from pathlib import Path

ALIYUN_HOST = "43.106.53.242"
ALIYUN_USER = "root"
ALIYUN_PASS = os.environ.get("ALIYUN_SSH_PASS", "PrisirTrain2026!")

# 同时下两个 adapter:base + conf
JOBS = [
    ("/workspace/qwen3guard-email/adapter",
     Path("D:/prisir-train-assets/trained/email/adapter")),
    ("/workspace/qwen3guard-email-conf/adapter",
     Path("D:/prisir-train-assets/trained/email_conf/adapter")),
]


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ALIYUN_HOST, username=ALIYUN_USER, password=ALIYUN_PASS,
                   timeout=15)
    sftp = client.open_sftp()

    for remote_dir, local_target in JOBS:
        local_target.mkdir(parents=True, exist_ok=True)
        files = sftp.listdir(remote_dir)
        print(f"\n[1/2] {remote_dir} → {local_target}")
        print(f"  远端 {len(files)} 个文件:")
        for f in sorted(files):
            print(f"  - {f}")
        for fname in files:
            rpath = f"{remote_dir}/{fname}"
            lpath = local_target / fname
            print(f"  ↓ {fname} ...", end=" ", flush=True)
            try:
                sftp.get(rpath, str(lpath))
                size = lpath.stat().st_size
                print(f"OK ({size//1024} KB)")
            except Exception as e:
                print(f"FAIL: {e}")
                sys.exit(1)
        print(f"  ✅ {local_target}: "
              f"{sum(f.stat().st_size for f in local_target.iterdir())//1024} KB")

    sftp.close()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())