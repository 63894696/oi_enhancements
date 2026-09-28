#!/usr/bin/env python3
# download_email_v2.py — 下载 v2 email + email_conf adapter
import os, paramiko
from pathlib import Path
ALIYUN_HOST = "43.106.53.242"
PASS = os.environ.get("ALIYUN_SSH_PASS", "PrisirTrain2026!")

JOBS = [
    ("/workspace/qwen3guard-email/adapter",
     Path("D:/prisir-train-assets/trained/email/adapter")),
    ("/workspace/qwen3guard-email-conf/adapter",
     Path("D:/prisir-train-assets/trained/email_conf/adapter")),
]

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(ALIYUN_HOST, username="root", password=PASS, timeout=15)
sftp = c.open_sftp()

for remote_dir, local_target in JOBS:
    local_target.mkdir(parents=True, exist_ok=True)
    # 清旧文件(v1 → v2)
    for old in local_target.iterdir():
        old.unlink()
    files = sftp.listdir(remote_dir)
    print(f"\n[1/2] {remote_dir} → {local_target}")
    print(f"  远端 {len(files)} 个文件")
    for f in sorted(files):
        lpath = local_target / f
        try:
            sftp.get(f"{remote_dir}/{f}", str(lpath))
            print(f"  ✓ {f} ({lpath.stat().st_size//1024}KB)")
        except Exception as e:
            print(f"  ✗ {f}: {e}")
    total = sum(f.stat().st_size for f in local_target.iterdir())
    print(f"  ✅ total: {total//1024} KB")

sftp.close(); c.close()